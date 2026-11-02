"""控制一轮的决策、执行、反馈、继续与退出。"""
from collections import Counter
from copy import deepcopy
import json

from report_service import ReportService, file_hashes
from report_models import RehearsalModel
from deepseek_model import ModelError


def run_report(source_dir, scenario="duplicate", mode="agent", behavior="normal", max_turns=32, model=None):
    if mode not in ("agent", "workflow") or max_turns < 0:
        raise ValueError("模式不支持或预算为负")
    service = ReportService(source_dir, scenario)
    observations, events = [], []
    chooser = model or RehearsalModel(behavior)
    repeats = Counter()
    calls = turns = false_finals = 0

    def feedback(message):
        events.append({"type": "feedback", "message": message})
        observations.append({"name": "program_feedback", "args": {},
                             "result": {"ok": True, "message": message}})

    def finish(status, reason):
        report = service.published_report() if status == "completed" else None
        stats = chooser.metadata() if mode == "agent" and hasattr(chooser, "metadata") else {}
        return {"scenario": scenario, "mode": mode, "engine": stats.get("engine", "custom" if model else "rehearsal"),
                "status": status, "reason": reason, "report": report, "events": events,
                "tool_calls": calls, "decision_turns": turns,
                "model_calls": stats.get("model_calls", 0), "real_model_runs": stats.get("real_model_runs", 0),
                "model_metadata": stats,
                "source_unchanged": service.hashes == file_hashes(service.root)}

    # Workflow 将固定步骤与分支直接编码；agent 模式将观察交给可替换的决策接口。
    fixed = iter([("list_files", {}), ("read_policy", {}),
                  *(("read_table", {"filename": name}) for name in service.basis["sources"]),
                  ("build_draft", {}), ("reconcile", {})])
    workflow_queue = []
    for _ in range(max_turns):
        if mode == "workflow":
            action = workflow_queue.pop(0) if workflow_queue else next(fixed, ("final", {}))
        else:
            turns += 1
            try:
                action = chooser.decide(deepcopy(observations))
            except ModelError as exc:
                events.append({"type": "model_error", "message": str(exc)})
                return finish("model_error", str(exc))
        name, args = action
        events.append({"type": "decision", "name": name, "args": args,
                       "source": "workflow" if mode == "workflow" else getattr(chooser, "last_decision_source", "rule" if not model else "custom")})
        if name == "ask_user":
            return finish("needs_input", args.get("text", "需要用户确认"))
        if name == "final":
            if service.published_report() is not None:
                return finish("completed", "来源与部门合计已核对，派生汇总通过验证")
            false_finals += 1
            feedback("缺少当前版本的验证记录，不能完成")
            if false_finals >= 3:
                return finish("no_progress", "连续最终提议未通过完成检查")
            continue
        result = service.dispatch(name, args)
        calls += 1
        observation = {"name": name, "args": args, "result": result}
        observations.append(observation)
        events.append({"type": "tool", **observation})
        if mode == "workflow":
            if not result.get("ok"):
                return finish("needs_input", "固定流程遇到缺失或冲突，停止等待确认")
            if name == "reconcile":
                ids = result["duplicate_ids"]
                if ids:
                    workflow_queue += [item for oid in ids for item in (("lookup_order", {"order_id": oid}), ("exclude_duplicate", {"order_id": oid}))]
                    workflow_queue += [("build_draft", {}), ("reconcile", {})]
                elif result["differences"]:
                    return finish("needs_input", "固定流程无法解释差额，需要确认")
                else:
                    workflow_queue += [("validate_report", {}), ("final", {})]
        signature = json.dumps(observation, ensure_ascii=False, sort_keys=True)
        repeats[signature] += 1
        if repeats[signature] == 3:
            feedback("同一动作反复得到相同结果，请换一个行动")
        if repeats[signature] >= 4:
            return finish("no_progress", "同一动作持续重复，缺少新事实")
    return finish("budget_exhausted", "执行轮数预算耗尽，未交付最终汇总")
