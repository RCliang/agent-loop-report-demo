"""离线机制检查与回归入口，不调用真实模型；正片演示使用 minimal_loop.py 或 live_run.py。"""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import platform
import subprocess
import sys

from report_service import file_hashes
from loop import run_report

HERE = Path(__file__).resolve().parent
CASES = {
    "normal": ({"scenario": "normal"}, "completed"),
    "duplicate": ({"scenario": "duplicate"}, "completed"),
    "conflict": ({"scenario": "conflict"}, "needs_input"),
    "unexplained": ({"scenario": "unexplained"}, "needs_input"),
    "missing": ({"scenario": "missing"}, "needs_input"),
    "premature": ({"behavior": "premature"}, "no_progress"),
    "stuck": ({"behavior": "stuck"}, "no_progress"),
    "budget": ({"max_turns": 2}, "budget_exhausted"),
    "workflow-normal": ({"scenario": "normal", "mode": "workflow"}, "completed"),
    "workflow-duplicate": ({"scenario": "duplicate", "mode": "workflow"}, "completed"),
}


def export_result(folder, name, result):
    folder.mkdir(parents=True, exist_ok=True)
    result["evidence_type"] = "deterministic_mechanism_test"
    (folder / "trace.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {name}", "", "虚构办公数据，规则替身或固定 Workflow。真实模型运行 0 次。", "",
             f"退出：{result['status']}；工具处理 {result['tool_calls']} 次；替身决策 {result['decision_turns']} 轮。", ""]
    for e in result["events"]:
        if e["type"] == "tool":
            lines += [f"## {e['name']}", "", "```json", json.dumps({"arguments": e["args"], "result": e["result"]}, ensure_ascii=False, indent=2), "```", ""]
        elif e["type"] == "feedback":
            lines += ["程序反馈：" + e["message"], ""]
    lines += ["停止原因：" + result["reason"], ""]
    (folder / "trace.md").write_text("\n".join(lines), encoding="utf-8")
    report = result.get("report")
    target = folder / "report.json"
    if report is None:
        # 当前运行不允许留下本目录上次生成的“最终结果”。
        if target.exists():
            target.unlink()
    else:
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"case": name, "status": result["status"], "reason": result["reason"],
            "tool_calls": result["tool_calls"], "decision_turns": result["decision_turns"],
            "total": report["total"] if report else None,
            "source_unchanged": result["source_unchanged"], "real_model_runs": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="duplicate")
    parser.add_argument("--all", action="store_true", help="重跑十条场景并执行报表测试")
    parser.add_argument("--out", type=Path, default=HERE / "runs" / "offline")
    args = parser.parse_args()
    rows = []
    for name in CASES if args.all else [args.case]:
        kwargs, expected = CASES[name]
        result = run_report(HERE / "fixtures", **kwargs)
        if result["status"] != expected or not result["source_unchanged"]:
            raise AssertionError(f"{name}: {result['status']}, expected {expected}, source_unchanged={result['source_unchanged']}")
        if result["report"] is not None and result["report"]["total"] != "9000.00":
            raise AssertionError("实际合计与人工核对样例不一致")
        rows.append(export_result(args.out / name, name, result))
        print(f"[{name}] {result['status']} | 工具 {result['tool_calls']} 次 | 替身决策 {result['decision_turns']} 轮")
        if not args.all:
            print("[虚构 CSV + 规则替身；不是实际大模型运行]")
            for event in result["events"]:
                if event["type"] == "tool":
                    data = event["result"]
                    print(f"\n{event['name']} {json.dumps(event['args'], ensure_ascii=False)}")
                    selected = {k: v for k, v in data.items() if k in ("ok", "code", "total", "departments", "differences", "duplicate_ids", "records", "excluded", "message")}
                    print(json.dumps(selected or data, ensure_ascii=False))
            print("\n最终报告：" + (str(args.out / name / "report.json") if result["report"] else "未生成，等待确认或已停止"))
    if args.all:
        for directory, pattern, filename in ((HERE, "test_*.py", "report-tests.txt"),):
            completed = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(directory), "-p", pattern, "-v"],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace")
            (args.out / filename).write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
            if completed.returncode:
                raise AssertionError("回归失败，检查 " + filename)
        summary = {"recorded_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                   "python": sys.version, "platform": platform.platform(), "case_count": len(rows),
                   "real_model_runs": 0, "input_hashes": file_hashes(HERE / "fixtures"),
                   "code_hashes": {p.name: __import__("hashlib").sha256(p.read_bytes()).hexdigest() for p in HERE.glob("*.py")},
                   "results": rows}
        (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# 部门汇总表离线成绩单", "", "十条固定场景，各运行一次；含主动停止用例，不代表模型成功率。", "",
                 "| 场景 | 退出 | 工具处理 | 替身决策 | 最终合计 |", "| --- | --- | --- | --- | --- |"]
        lines += [f"| [{r['case']}]({r['case']}/trace.md) | {r['status']} | {r['tool_calls']} | {r['decision_turns']} | {r['total'] or '未生成'} |" for r in rows]
        (args.out / "成绩单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("全部离线场景与报表回归测试完成；真实模型运行 0 次。")


if __name__ == "__main__":
    main()
