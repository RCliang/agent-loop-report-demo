"""直接调用真实模型的最小工具循环。"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path

from deepseek_model import DeepSeekModel, TASK, TOOLS
from report_service import ReportService, file_hashes

SYSTEM = '根据用户任务使用工具，只依据工具返回处理数据。金额计算交给工具，原始文件不改。任务完成或需要用户补充时，直接用中文答复。'
REPORT_TOOLS = [tool for tool in TOOLS if tool['function']['name'] != 'ask_user']


def minimal_loop(task, model, execute):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    while True:
        message = model.chat(messages, tools=REPORT_TOOLS)
        messages.append(message)
        if not message.get("tool_calls"):
            return message["content"]
        for call in message["tool_calls"]:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            result = execute(name, args)
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})


# 以下仅整理运行证据；演示最小执行骨架时只展示上面的函数。
def run_minimal(source_dir, model=None):
    service = ReportService(source_dir)
    model = model or DeepSeekModel()
    tool_events = []

    def execute(name, args):
        result = service.dispatch(name, args)
        tool_events.append({"type": "tool", "name": name, "args": args, "result": result})
        return result

    answer = minimal_loop(TASK, model, execute)
    history = [{"type": "user", "text": TASK}]
    executed = iter(tool_events)
    for turn, record in enumerate(model.requests, 1):
        message = record["response"]["choices"][0]["message"]
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                history.append({"type": "decision", "round": turn, "source": "deepseek",
                                "name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])})
                history.append({**next(executed), "round": turn})
        else:
            history.append({"type": "decision", "round": turn, "source": "deepseek",
                            "name": "final", "args": {"text": message["content"]}})
    drafts = [event["result"] for event in tool_events if event["name"] == "build_draft" and event["result"].get("ok")]
    stats = model.metadata()
    return {"status": "stopped_on_final", "answer": answer, "history": history,
            "tool_calls": len(tool_events), "decision_turns": stats["model_calls"],
            "action_count": len(tool_events)+1, "draft": drafts[-1] if drafts else None,
            "business_verified": service.published_report() is not None, "report": service.published_report(),
            "engine": "deepseek", "model_metadata": stats, "model_calls": stats["model_calls"],
            "real_model_runs": stats["real_model_runs"], "source_unchanged": service.hashes == file_hashes(source_dir),
            "decision_policy": "真实模型原生 tool_calls；无工具调用时返回文本",
            "scope": "最小宿主不设轮数预算、重复检测或强制完成门"}


def main():
    here = Path(__file__).resolve().parent
    started = datetime.now(timezone(timedelta(hours=8)))
    try:
        model = DeepSeekModel()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    folder = here/"runs"/"minimal"/started.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    source = folder/"source"
    source.mkdir()
    names = ("minimal_loop.py", "deepseek_model.py", "report_service.py")
    for name in names:
        (source/name).write_bytes((here/name).read_bytes())
    model.audit_path = folder/"requests.json"
    result = run_minimal(here/"fixtures", model=model)
    (folder/"trace.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if result["report"] is not None:
        (folder/"report.json").write_text(json.dumps(result["report"], ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    summary = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
               "input_hashes": file_hashes(here/"fixtures"),
               "code_hashes": {name: hashlib.sha256((source/name).read_bytes()).hexdigest() for name in names},
               "model_calls": result["model_calls"], "tool_calls": result["tool_calls"],
               "decision_turns": result["decision_turns"], "action_count": result["action_count"],
               "business_verified": result["business_verified"], "source_unchanged": result["source_unchanged"],
               "model_metadata": result["model_metadata"]}
    (folder/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    from live_run import markdown_trace
    (folder/"trace.md").write_text(markdown_trace("minimal", result), encoding="utf-8")
    (folder.parent/"current.json").write_text(json.dumps({"directory": folder.name}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(result["answer"])
    print(f"\n实际请求 {result['model_calls']} 次，工具 {result['tool_calls']} 次，业务工具验证通过：{result['business_verified']}。")
    print("MINIMAL_RUN_DIRECTORY="+str(folder))


if __name__ == "__main__":
    main()
