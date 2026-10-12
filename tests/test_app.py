"""通过真实 Streamlit 页面脚本验证跨页状态和异常导出。"""
import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from core.workflow import apply_edits

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class PageTests(unittest.TestCase):
    def setUp(self):
        self.at = AppTest.from_file(APP, default_timeout=20).run()
        self.assertEqual(len(self.at.exception), 0)
        self.at.switch_page("views/import_page.py").run()
        self.at.button[0].click().run()
        self.at.switch_page("views/editor_page.py").run()

    def test_sample_calculation_export_and_page_return(self):
        self.at.button[0].click().run()
        self.assertEqual(self.at.session_state.payroll_df["合计"].tolist(),
                         [5400, 5220, 4920, 5393, 5450, 5600, 5400, 4740])
        self.at.switch_page("views/export_page.py").run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertEqual(len(self.at.get("download_button")), 1)
        self.assertIn("一致", self.at.success[0].value)
        self.at.switch_page("views/import_page.py").run()
        self.at.switch_page("views/editor_page.py").run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertEqual(self.at.session_state.payroll_df["合计"].sum(), 42123)

    def test_changed_inputs_cannot_export_old_result(self):
        self.at.button[0].click().run()
        apply_edits(self.at.session_state, {0: {"工资": 5000}})
        self.at.switch_page("views/export_page.py").run()
        self.assertEqual(len(self.at.get("download_button")), 0)
        self.assertIn("尚无", self.at.warning[0].value)
        self.at.switch_page("views/editor_page.py").run()
        self.at.button[0].click().run()
        self.assertEqual(self.at.session_state.payroll_df.iloc[0]["合计"], 5900)
        self.at.switch_page("views/export_page.py").run()
        self.assertIn("不同", self.at.warning[0].value)
        self.assertEqual(len(self.at.exception), 0)

    def test_negative_total_visible_but_export_disabled(self):
        apply_edits(self.at.session_state, {0: {"旷工天数": 30}})
        self.at.run()
        self.at.button[0].click().run()
        self.assertIn("负数", self.at.error[0].value)
        self.at.switch_page("views/export_page.py").run()
        self.assertEqual(len(self.at.get("download_button")), 0)
        self.assertIn("异常", self.at.error[0].value)
        self.assertEqual(len(self.at.exception), 0)

    def test_invalid_inputs_shown_without_crashing_page(self):
        apply_edits(self.at.session_state, {0: {"普通缺勤天数": 31}})
        self.at.run()
        self.at.button[0].click().run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertIn("修正", self.at.error[0].value)
        self.assertIsNone(self.at.session_state.payroll_df)


if __name__ == "__main__":
    unittest.main()
