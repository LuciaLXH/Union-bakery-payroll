import unittest
from core.workflow import initialize, load_inputs, apply_edits, recalculate
from core.excel_handler import example_inputs
from core.payroll_calc import PayrollValidationError


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        initialize(self.state)
        load_inputs(self.state, example_inputs(), "测试")

    def test_edits_invalidate_results_and_recalculate_from_saved_inputs(self):
        recalculate(self.state)
        apply_edits(self.state, {0: {"工资": 5000}})
        self.assertIsNone(self.state["payroll_df"])
        self.assertEqual(recalculate(self.state).iloc[0]["合计"], 5900)
        apply_edits(self.state, {0: {"工资": 5100, "工作失误项目": "新备注"}})
        self.assertEqual(recalculate(self.state).iloc[0]["合计"], 6000)
        self.assertEqual(self.state["draft_df"].iloc[0]["工作失误项目"], "新备注")

    def test_invalid_edit_cannot_leave_old_exportable_result(self):
        recalculate(self.state)
        apply_edits(self.state, {0: {"工资": None}})
        with self.assertRaises(PayrollValidationError):
            recalculate(self.state)
        self.assertIsNone(self.state["payroll_df"])

    def test_new_inputs_invalidate_old_results_and_keep_copies(self):
        recalculate(self.state)
        df = example_inputs()
        load_inputs(self.state, df, "新文件")
        df.loc[0, "工资"] = 1
        self.assertIsNone(self.state["payroll_df"])
        self.assertEqual(self.state["draft_df"].iloc[0]["工资"], 4500)

    def test_reinitialization_does_not_erase_draft(self):
        apply_edits(self.state, {0: {"工资": 5000}})
        initialize(self.state)
        self.assertEqual(self.state["draft_df"].iloc[0]["工资"], 5000)


if __name__ == "__main__":
    unittest.main()
