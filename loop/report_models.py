"""固定规则决策替身，只读取传入的观察；不执行工具。"""


class RehearsalModel:
    """只依据工具返回决定动作，不读取 ReportService 的内部状态。"""
    def __init__(self, behavior="normal"):
        self.behavior = behavior

    def decide(self, observations):
        if self.behavior == "premature":
            return "final", {"text": "汇总已经完成"}
        if self.behavior == "stuck":
            return "list_files", {}
        seen = {o["name"] for o in observations if o["result"].get("ok")}
        if "list_files" not in seen:
            return "list_files", {}
        if "read_policy" not in seen:
            return "read_policy", {}
        if observations and not observations[-1]["result"].get("ok"):
            return "ask_user", {"text": "当前证据不足，见上一条工具错误，需要补充或确认"}
        policy = next(o["result"] for o in observations if o["name"] == "read_policy")
        read = {o["args"]["filename"] for o in observations if o["name"] == "read_table"}
        for filename in policy["sources"]:
            if filename not in read:
                return "read_table", {"filename": filename}
        last = observations[-1]
        if last["name"] in ("read_table", "exclude_duplicate"):
            return "build_draft", {}
        if last["name"] == "build_draft":
            return "reconcile", {}
        if last["name"] == "reconcile":
            result = last["result"]
            if result["duplicate_ids"]:
                return "lookup_order", {"order_id": result["duplicate_ids"][0]}
            return ("ask_user", {"text": "差额没有找到充分解释，请确认明细或部门合计"}) if result["differences"] else ("validate_report", {})
        if last["name"] == "lookup_order":
            return "exclude_duplicate", {"order_id": last["args"]["order_id"]}
        return "final", {"text": "已完成来源核对和汇总验证"}
