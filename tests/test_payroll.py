"""基于老板反馈的算例及独立边界测试。"""
import unittest
from decimal import Decimal
import pandas as pd
from pandas.testing import assert_frame_equal
from core.payroll_calc import calculate_payroll, normalize_inputs, PayrollValidationError


def employee(**changes):
    row = {"姓名": "测试员工", "工资": 4500, "绩效": 600, "全勤": 300}
    row.update(changes)
    return pd.DataFrame([row])


class PayrollCalculationTests(unittest.TestCase):
    def test_acceptance_examples(self):
        cases = [
            ({}, [0, 0, 0, 0, 0, 5400]),
            ({"普通缺勤天数": 1}, [150, 20, 10, 0, 0, 5220]),
            ({"旷工天数": 1}, [450, 20, 10, 0, 0, 4920]),
            ({"迟到": 7}, [0, 0, 0, 7, 0, 5393]),
            ({"有效加班时长": 2}, [0, 0, 0, 0, 50, 5450]),
            ({"通勤补贴": 200}, [0, 0, 0, 0, 0, 5600]),
            ({"职级": "零基础学徒", "工作失误项目": "练习中失误"}, [0, 0, 0, 0, 0, 5400]),
            ({"普通缺勤天数": 1, "旷工天数": 1}, [600, 40, 20, 0, 0, 4740]),
        ]
        fields = ["工资扣款", "绩效扣款", "全勤扣款", "迟到扣款", "加班费", "合计"]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                result = calculate_payroll(employee(**changes)).iloc[0]
                self.assertEqual(result[fields].tolist(), expected)

    def test_combined_inputs(self):
        result = calculate_payroll(employee(普通缺勤天数=1, 旷工天数=1, 迟到=7,
                                            有效加班时长=2, 通勤补贴=200)).iloc[0]
        self.assertEqual(result["合计"], 4983)
        self.assertEqual(result["应发合计"] - result["扣款合计"], result["合计"])

    def test_legacy_fields_cannot_change_pay(self):
        baseline = calculate_payroll(employee(有效加班时长=2))
        legacy = employee(有效加班时长=2, 职位="学徒", 岗位="前台", 职级="有基础学徒",
                          是否上社保=True, 是否处于试用期=True, 店铺平均人效=0,
                          营业额=0, 社保扣减额=1000, 报损扣款=900, 投诉扣款=50,
                          手动福利=100, 工作失误项目="原材料失误金额999元，次数5次")
        changed = calculate_payroll(legacy)
        for col in ("工资扣款", "加班费", "合计"):
            self.assertEqual(baseline.iloc[0][col], changed.iloc[0][col])
        self.assertNotIn("社保扣减额", changed)
        self.assertNotIn("报损扣款", changed)

    def test_overtime_uses_full_wage_before_absence(self):
        result = calculate_payroll(employee(旷工天数=1, 有效加班时长=2)).iloc[0]
        self.assertEqual(result["加班费"], 50)
        self.assertEqual(result["合计"], 4970)

    def test_late_does_not_cancel_attendance_bonus(self):
        result = calculate_payroll(employee(迟到=1)).iloc[0]
        self.assertEqual(result["全勤扣款"], 0)
        self.assertEqual(result["合计"], 5399)

    def test_half_up_rounding_without_rounding_hourly_rate(self):
        half_cent = calculate_payroll(employee(工资=0.90, 绩效=0, 全勤=0, 有效加班时长=1)).iloc[0]
        self.assertEqual(half_cent["加班费"], 0.01)
        result = calculate_payroll(employee(工资=4000, 有效加班时长=3)).iloc[0]
        self.assertEqual(result["加班费"], 66.67)  # 提前舍入小时费将得到66.66

    def test_item_rounding_reconciles_visible_total(self):
        result = calculate_payroll(employee(工资=1000, 绩效=100, 全勤=100, 普通缺勤天数=1)).iloc[0]
        self.assertEqual(result["扣款合计"], 39.99)
        self.assertEqual(result["合计"], 1160.01)
        self.assertEqual(sum(Decimal(str(result[c])) for c in ["工资扣款", "绩效扣款", "全勤扣款", "迟到扣款"]), Decimal(str(result["扣款合计"])))

    def test_negative_total_preserves_evidence(self):
        result = calculate_payroll(employee(旷工天数=30)).iloc[0]
        self.assertEqual(result["合计"], -9000)
        self.assertIn("实发金额为负", result["核算提示"])
        self.assertEqual(result["工资扣款"], 13500)

    def test_zero_and_full_month_absence(self):
        self.assertEqual(calculate_payroll(employee(工资=0, 绩效=0, 全勤=0)).iloc[0]["合计"], 0)
        self.assertEqual(calculate_payroll(employee(普通缺勤天数=30)).iloc[0]["合计"], 0)

    def test_recalculation_is_idempotent_and_does_not_mutate_input(self):
        original = employee(旷工天数=1, 有效加班时长=2)
        before = original.copy(deep=True)
        result = calculate_payroll(original)
        assert_frame_equal(original, before)
        assert_frame_equal(result, calculate_payroll(result))

    def test_reference_values_are_only_for_comparison(self):
        result = calculate_payroll(employee(原表合计=5000, 原表加班费=10, 合计=1, 加班费=99)).iloc[0]
        self.assertEqual(result["合计"], 5400)
        self.assertEqual(result["合计差额"], 400)
        self.assertEqual(result["加班费差额"], -10)


class InputValidationTests(unittest.TestCase):
    def test_missing_required_column_and_blank_are_not_default_bonuses(self):
        for col in ("工资", "绩效", "全勤"):
            for df in (employee().drop(columns=[col]), employee(**{col: None}), employee(**{col: " "})):
                with self.subTest(col=col), self.assertRaises(PayrollValidationError):
                    calculate_payroll(df)

    def test_invalid_values_are_rejected_with_row_and_field(self):
        for col, val in [("工资", -1), ("工资", "abc"), ("工资", True),
                         ("工资", "NaN"), ("工资", float("inf")), ("工资", "1e100"),
                         ("工资", 0.001), ("旷工天数", -1), ("旷工天数", .5),
                         ("普通缺勤天数", 31), ("迟到", 1.5), ("有效加班时长", -1),
                         ("有效加班时长", "Infinity")]:
            with self.subTest(col=col, val=val), self.assertRaises(PayrollValidationError) as error:
                calculate_payroll(employee(**{col: val}))
            self.assertEqual(error.exception.issues[0]["行号"], 2)

    def test_multiple_errors_are_reported_together(self):
        with self.assertRaises(PayrollValidationError) as error:
            calculate_payroll(employee(工资=-1, 全勤="abc", 姓名=" "))
        self.assertEqual({i["字段"] for i in error.exception.issues}, {"姓名", "工资", "全勤"})

    def test_combined_absence_limit(self):
        with self.assertRaises(PayrollValidationError):
            calculate_payroll(employee(普通缺勤天数=20, 旷工天数=11))

    def test_empty_input_or_duplicate_headers(self):
        with self.assertRaises(PayrollValidationError):
            calculate_payroll(employee().iloc[:0])
        df = employee()
        df.columns = ["姓名", "工资", "工资", "全勤"]
        with self.assertRaises(PayrollValidationError):
            calculate_payroll(df)

    def test_same_names_have_distinct_ids(self):
        df = pd.concat([employee(), employee()], ignore_index=True)
        result = normalize_inputs(df)
        self.assertEqual(result["员工编号"].nunique(), 2)
        df["员工编号"] = ["a", "a"]
        with self.assertRaises(PayrollValidationError):
            normalize_inputs(df)

    def test_generated_ids_do_not_conflict_with_supplied_ids(self):
        df = pd.concat([employee(), employee(员工编号="临时-0001")], ignore_index=True)
        self.assertEqual(normalize_inputs(df)["员工编号"].nunique(), 2)

    def test_clean_text_default_store_optional_values_and_date(self):
        df = employee(姓名=" 员工甲 ", 分店=" ", 工资="4500.00", 入职时间="2026-09-01")
        result = normalize_inputs(df, default_store=" 门店一 ").iloc[0]
        self.assertEqual(result["姓名"], "员工甲")
        self.assertEqual(result["分店"], "门店一")
        self.assertEqual(result["通勤补贴"], 0)
        self.assertEqual(result["入职时间"], "2026-09-01")
        with self.assertRaises(PayrollValidationError):
            normalize_inputs(employee(入职时间="not a date"))


if __name__ == "__main__":
    unittest.main()
