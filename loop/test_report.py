"""真实 CSV、金额计算、来源核对与退出行为测试。"""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from loop import run_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(Path(__file__).parent / "fixtures", self.root / "inputs")
        self.inputs = self.root / "inputs"

    def tearDown(self):
        self.temp.cleanup()

    def run_case(self, **kwargs):
        return run_report(self.inputs, **kwargs)

    def test_duplicate_is_explained_by_archive_and_recomputed(self):
        r = self.run_case()
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["report"]["raw_total"], "11500.00")
        self.assertEqual(r["report"]["total"], "9000.00")
        self.assertEqual(r["report"]["previous_total"], "8000.00")
        self.assertEqual(r["report"]["growth"], "0.125")
        self.assertEqual(r["report"]["excluded"][0]["source"], "dept_b.csv:4")
        self.assertEqual(len(r["report"]["rows"]), 7)
        names = [e["name"] for e in r["events"] if e["type"] == "tool"]
        self.assertLess(names.index("lookup_order"), names.index("exclude_duplicate"))
        self.assertEqual(names.count("build_draft"), 2)

    def test_source_bytes_remain_unchanged(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.inputs.iterdir()}
        self.assertEqual(self.run_case()["status"], "completed")
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.inputs.iterdir()})

    def test_normal_needs_no_exclusion(self):
        r = self.run_case(scenario="normal")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["report"]["total"], "9000.00")
        self.assertEqual(r["report"]["excluded"], [])

    def test_conflicting_same_id_requires_user(self):
        r = self.run_case(scenario="conflict")
        self.assertEqual(r["status"], "needs_input")
        self.assertIsNone(r["report"])
        self.assertTrue(any("B202" in str(e) for e in r["events"]))

    def test_unexplained_difference_cannot_be_force_balanced(self):
        r = self.run_case(scenario="unexplained")
        self.assertEqual(r["status"], "needs_input")
        self.assertIsNone(r["report"])

    def test_missing_department_is_not_zero(self):
        r = self.run_case(scenario="missing")
        self.assertEqual(r["status"], "needs_input")
        self.assertIsNone(r["report"])

    def test_premature_answer_does_not_publish_report(self):
        r = self.run_case(behavior="premature")
        self.assertEqual(r["status"], "no_progress")
        self.assertIsNone(r["report"])

    def test_repeated_observation_stops(self):
        r = self.run_case(behavior="stuck")
        self.assertEqual(r["status"], "no_progress")
        self.assertLess(r["tool_calls"], 10)

    def test_budget_stops_before_report_is_verified(self):
        r = self.run_case(max_turns=2)
        self.assertEqual(r["status"], "budget_exhausted")
        self.assertIsNone(r["report"])

    def test_workflow_uses_same_rules_and_can_handle_duplicate(self):
        r = self.run_case(mode="workflow")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["report"]["total"], "9000.00")
        self.assertEqual(r["model_calls"], 0)

    def test_equal_amount_different_order_ids_are_both_kept(self):
        p = self.inputs / "dept_a.csv"
        rows = list(csv.reader(p.read_text(encoding="utf-8").splitlines()))
        rows[2][-1], rows[3][-1] = "900.00", "900.00"
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        r = self.run_case()
        self.assertEqual(r["status"], "completed")
        self.assertEqual(len([x for x in r["report"]["rows"] if x["department"] == "一部"]), 3)

    def test_out_of_period_record_does_not_enter_current_week(self):
        with (self.inputs / "dept_a.csv").open("a", encoding="utf-8", newline="") as f:
            f.write("A104,一部,2026-10-18,999.00\n")
        r = self.run_case()
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["report"]["total"], "9000.00")
        self.assertEqual(len(r["report"]["out_of_period"]), 1)

    def test_non_numeric_money_stops_with_explicit_error(self):
        p = self.inputs / "dept_a.csv"
        p.write_text(p.read_text(encoding="utf-8").replace("1200.00", "缺失"), encoding="utf-8")
        r = self.run_case()
        self.assertEqual(r["status"], "needs_input")
        self.assertIsNone(r["report"])

    def test_completion_rejection_is_visible_to_next_decision(self):
        class CorrectingModel:
            def decide(self, observations):
                if observations:
                    return "ask_user", {"text": "看到了完成检查反馈，需要补充确认"}
                return "final", {"text": "已经完成"}
        r = self.run_case(model=CorrectingModel())
        self.assertEqual(r["status"], "needs_input")
        self.assertEqual(r["decision_turns"], 2)

    def test_missing_previous_amount_requires_confirmation(self):
        p = self.inputs / "basis.json"
        basis = json.loads(p.read_text(encoding="utf-8"))
        basis["previous_totals"]["一部"] = "缺失"
        p.write_text(json.dumps(basis, ensure_ascii=False), encoding="utf-8")
        r = self.run_case()
        self.assertEqual(r["status"], "needs_input")
        self.assertIsNone(r["report"])

    def test_normal_fixture_without_duplicate_is_not_truncated(self):
        p = self.inputs / "dept_b.csv"
        rows = list(csv.reader(p.read_text(encoding="utf-8").splitlines()))[:-1]
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        r = self.run_case(scenario="normal")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["report"]["total"], "9000.00")


if __name__ == "__main__":
    unittest.main()
