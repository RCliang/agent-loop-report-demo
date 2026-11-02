"""实际调用 DeepSeek，逐场景保存证据；不把模型输出断言为预定路线。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

from deepseek_model import DeepSeekModel
from loop import run_report
from minimal_loop import run_minimal
from report_service import file_hashes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CASES = ('minimal', 'normal', 'duplicate', 'conflict', 'unexplained', 'missing', 'budget', 'premature-recovery')


class InjectedFinal:
    """第一轮人为注入 final，后续由真实模型接收拒绝反馈并决定；不冒充模型失误。"""
    def __init__(self, model):
        self.model = model
        self.injected = False
        self.last_decision_source = 'fault_injection'

    def decide(self, observations):
        if not self.injected:
            self.injected = True
            return 'final', {'text': '故障注入：尚未读取数据就提出完成'}
        self.last_decision_source = 'deepseek'
        return self.model.decide(observations)

    def metadata(self):
        return {**self.model.metadata(), 'fault_injection': '首次 final 由程序注入，之后交给真实模型'}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def markdown_trace(name, result):
    meta = result['model_metadata']
    lines = [f'# {name}｜DeepSeek 实际运行', '',
             '输入为虚构教学数据。模型每次返回的动作实际执行，没有预设成功路线。', '',
             f"退出：{result['status']}；工具 {result['tool_calls']} 次；循环决策 {result['decision_turns']} 轮；HTTP 请求 {result['model_calls']} 次。", '',
             f"token：{meta['usage']['total_tokens']}；API 累计耗时：{meta['api_elapsed_seconds']} 秒。", '',
             '[原生请求与响应](requests.json)，[结构化轨迹](trace.json)。', '']
    if name == 'premature-recovery':
        lines += ['首轮 final 为程序故障注入，后续才由真实模型决策；不能说成模型主动过早结束。', '']
    for event in result.get('events', result.get('history', [])):
        kind = event.get('type')
        if kind == 'user': continue
        if kind == 'decision':
            lines += [f"## 动作：{event['name']}", '', f"来源：{event.get('source', 'deepseek')}", '', '```json', json.dumps(event['args'], ensure_ascii=False, indent=2), '```', '']
        elif kind == 'tool':
            lines += [f"### 工具返回：{event['name']}", '', '```json', json.dumps(event['result'], ensure_ascii=False, indent=2), '```', '']
        else:
            lines += ['程序记录：'+event.get('message', ''), '']
    lines += ['退出原因：'+result.get('reason', result.get('answer', '')), '']
    if name == 'minimal':
        lines += [f"业务工具是否验证通过：{result['business_verified']}。最小循环自身没有强制完成门。", '']
    return '\n'.join(lines)


def run_case(name, output, args):
    folder = output/name
    folder.mkdir()
    model = DeepSeekModel(base_url=args.base_url, model=args.model, timeout=args.timeout)
    model.audit_path = folder/'requests.json'
    chooser = InjectedFinal(model) if name == 'premature-recovery' else model
    if name == 'minimal':
        result = run_minimal(HERE/'fixtures', model=model)
    else:
        scenario = name if name in ('normal', 'duplicate', 'conflict', 'unexplained', 'missing') else 'duplicate'
        result = run_report(HERE/'fixtures', scenario=scenario, model=chooser,
                            max_turns=2 if name=='budget' else args.max_turns)
    result['evidence_type'] = 'live_model_with_fault_injection' if name=='premature-recovery' else 'live_model'
    save_json(folder/'trace.json', result)
    save_json(folder/'requests.json', model.requests)
    if result.get('report') is not None:
        save_json(folder/'report.json', result['report'])
    (folder/'trace.md').write_text(markdown_trace(name, result), encoding='utf-8')
    row = {'case': name, 'status': result['status'], 'tool_calls': result['tool_calls'],
           'decision_turns': result['decision_turns'], 'model_calls': result['model_calls'],
           'real_model_runs': result['real_model_runs'],
           'total': result['report']['total'] if result.get('report') else None,
           'draft_total': result['draft']['total'] if result.get('draft') else None,
           'business_verified': result.get('business_verified'),
           'source_unchanged': result['source_unchanged'], 'usage': result['model_metadata']['usage'],
           'api_elapsed_seconds': result['model_metadata']['api_elapsed_seconds'],
           'fault_injection': name=='premature-recovery'}
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, default='duplicate')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--base-url', default='https://api.deepseek.com')
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--max-turns', type=int, default=32)
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--workers', type=int, choices=(1,2,3), default=2)
    parser.add_argument('--out', type=Path)
    args=parser.parse_args()
    if not os.environ.get('DEEPSEEK_API_KEY'):
        parser.error('先设置 DEEPSEEK_API_KEY；可复制 .env.example 为 .env 填入（已排除出 git），或 export 环境变量；不要写进命令行参数或其他工程文件')
    if args.max_turns<1 or args.timeout<1:
        parser.error('轮数和超时必须为正数')
    started=datetime.now(timezone(timedelta(hours=8)))
    output=args.out or HERE/'runs/live'/started.strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    source=output/'source'
    source.mkdir()
    for p in HERE.glob('*.py'):
        (source/p.name).write_bytes(p.read_bytes())
    config={'started_at': started.isoformat(), 'base_url': args.base_url, 'model': args.model,
            'max_turns': args.max_turns, 'timeout': args.timeout, 'workers': args.workers,
            'thinking': 'disabled', 'temperature': 0, 'max_tokens': 2048,
            'task': model_task(), 'input_hashes': file_hashes(HERE/'fixtures'),
            'code_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')},
            'python': sys.version, 'platform': platform.platform(),
            'scope': '每场景运行一次，不自动重试，不筛选结果。premature-recovery 首轮由程序注入 final。'}
    save_json(output/'config.json', config)
    cases=CASES if args.all else (args.case,)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows=list(pool.map(lambda name: run_case(name, output, args), cases))
    summary={**config, 'finished_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
             'case_count': len(rows), 'model_calls': sum(r['model_calls'] for r in rows),
             'real_model_runs': sum(r['real_model_runs'] for r in rows),
             'usage': {k:sum(r['usage'][k] for r in rows) for k in ('prompt_tokens','completion_tokens','total_tokens')},
             'results': rows}
    save_json(output/'summary.json', summary)
    lines=['# 真实模型运行成绩单', '', f"模型：{args.model}；每场景一次。包含一条明确标注的故障注入，不计算成功率。", '',
           '| 场景 | 退出 | 工具 | 决策轮 | API 请求 | 最终表金额 | token |', '| --- | --- | --- | --- | --- | --- | --- |']
    lines += [f"| [{r['case']}]({r['case']}/trace.md) | {r['status']} | {r['tool_calls']} | {r['decision_turns']} | {r['model_calls']} | {r['total'] or '未生成'} | {r['usage']['total_tokens']} |" for r in rows]
    (output/'成绩单.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    # 只把默认证据目录登记为当前运行；每次原始记录仍单独保留。
    evidence_root = HERE/'runs/live'
    if args.all and output.resolve().parent == evidence_root.resolve():
        save_json(evidence_root/'当前运行.json', {'directory': output.name, 'summary': output.name+'/summary.json'})
    print('LIVE_RUN_DIRECTORY='+str(output), flush=True)
    if any(r['status']=='model_error' or not r['source_unchanged'] for r in rows):
        raise SystemExit(1)


def model_task():
    from deepseek_model import TASK
    return TASK


if __name__=='__main__':
    main()
