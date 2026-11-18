"""Commute entitlement, single reduction, persisted history and export regression tests."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import config
from core.payroll_calc import calculate_payroll
from core.workspace import PayrollWorkspace, result_frame
from core.excel_handler import export_payroll_to_excel
from ai_entry.review import confirm_rows
from ai_entry.service import EntryError
from test_database_schema import DatabaseFixture


class CommuteCalculationTests(TestCase):
    def test_eligibility_and_absence_types(self):
        for allowance, ordinary, unauthorized, expected in [
            (0, 0, 0, 0), (0, 2, 1, 0), (300, 0, 0, 300),
            (300, 2, 0, 280), (300, 0, 1, 290), (300, 2, 1, 270),
            (300, 30, 0, 0), (200, 2, 0, 280),
        ]:
            with self.subTest(allowance=allowance, ordinary=ordinary, unauthorized=unauthorized):
                inputs = pd.DataFrame([dict(姓名='虚构员工', 工资=2600, 绩效=0, 全勤=200,
                    通勤补贴=allowance, 普通缺勤天数=ordinary, 旷工天数=unauthorized)])
                result = calculate_payroll(inputs, decimal_output=True)
                row = result.iloc[0]
                self.assertEqual(row['实发通勤补贴'], Decimal(expected))
                self.assertEqual(row['标准通勤补贴'], 300 if allowance else 0)
                self.assertEqual(row['通勤减发'], (ordinary + unauthorized) * 10 if allowance else 0)
                self.assertEqual(row['应发合计'], 2800 + expected)
                self.assertEqual(row['合计'], row['应发合计'] - row['扣款合计'])
                assert_frame_equal(result, calculate_payroll(result, decimal_output=True))

    def test_lateness_and_overtime_do_not_change_commute(self):
        row = calculate_payroll(pd.DataFrame([dict(姓名='虚构员工', 工资=2600, 绩效=0,
            全勤=200, 通勤补贴=300, 普通缺勤天数=2, 迟到=5, 有效加班时长=3.5)]), decimal_output=True).iloc[0]
        self.assertEqual(row['实发通勤补贴'], 280)
        self.assertEqual(row['扣款合计'], Decimal('191.66'))
        self.assertEqual(row['合计'], Decimal('2938.90'))

    def test_ai_commute_setting_never_adds_or_accepts_reduced_amount(self):
        base = dict(员工编号='TEST', 事项='通勤补贴', 单位='元', 处理方式='累计', **{'原文 / 备注': '虚构通勤设置'})
        for amount in (0, 300):
            rows, _ = confirm_rows([dict(base, 数量=amount)], '2026-11', {'TEST':'虚构'}, {'TEST':'虚构店'})
            self.assertEqual(rows[0]['数量'], amount)
        for amount, mode in ((280, '累计'), (300, '新增'), (0, '新增')):
            with self.assertRaisesRegex(EntryError, '通勤补贴'):
                confirm_rows([dict(base, 数量=amount, 处理方式=mode)], '2026-11', {'TEST':'虚构'}, {'TEST':'虚构店'})


class CommuteHistoryTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.repo = PayrollWorkspace(self.connection, schema=self.schema)
        self.selection = dict(store_name='隔离测试门店', payroll_month=date(2026, 10, 1))
        self.change('salary_snapshot=2600,performance_snapshot=0,attendance_bonus_snapshot=200,'
                    'commute_allowance_snapshot=300,ordinary_absence_days=2,late_minutes=0,overtime_hours=3.5')
        self.legacy_id = self.connection.execute('''INSERT INTO payroll_calculations (
            payroll_entry_id, input_version, rule_version, input_snapshot,
            salary_deduction, performance_deduction, attendance_deduction, late_deduction,
            overtime_pay, gross_pay, total_deductions, net_pay)
            SELECT id,input_version,'2026-10-02-v1',to_jsonb(d),173.33,0,13.33,0,50.56,3150.56,186.66,2963.90
            FROM payroll_entries d RETURNING id''').fetchone()[0]
        self.mid = self.repo.months()[0]['id']

    def test_draft_requires_explicit_recalculation_and_preserves_both_exports(self):
        self.assertEqual(self.repo.current_results(self.mid), [])
        self.assertEqual(self.repo.status(**self.selection)[0]['calculation_status'], '待重新核算')
        old = self.repo.history(**self.selection)[0]
        saved = self.repo.save(**self.selection)[0]
        self.assertEqual(saved['net_pay'], Decimal('2943.90'))
        self.assertEqual(saved['input_snapshot']['commute_allowance_snapshot'], '300.00')
        self.assertEqual(saved['input_snapshot']['commute_calculation'],
                         {'标准通勤补贴':'300.00','通勤减发':'20.00','实发通勤补贴':'280.00'})
        self.assertEqual(self.repo.save(**self.selection)[0]['id'], saved['id'])
        self.assertEqual(len(self.repo.history(**self.selection)), 2)
        self.assertEqual(self.repo.history(**self.selection)[0], old)
        for record, payable, reduction, total in [(old,300,0,2963.90),(saved,280,20,2943.90)]:
            frame = result_frame([record])
            exported = pd.read_excel(BytesIO(export_payroll_to_excel(frame)))
            self.assertEqual(exported.iloc[0]['通勤补贴'], 300)
            self.assertEqual(exported.iloc[0]['实发通勤补贴'], payable)
            self.assertEqual(exported.iloc[0]['通勤减发'], reduction)
            self.assertEqual(exported.iloc[0]['合计'], total)

    def test_locked_legacy_result_and_export_never_adopt_new_rule(self):
        with patch.object(config, 'RULE_VERSION', '2026-10-02-v1'):
            self.repo.transition_month(self.mid, 'confirm', 1, calculation_ids=[self.legacy_id])
        results = self.repo.current_results(self.mid)
        self.assertEqual([r['id'] for r in results], [self.legacy_id])
        self.assertEqual(results[0]['net_pay'], Decimal('2963.90'))
        frame = result_frame(results)
        self.assertEqual(frame.iloc[0]['实发通勤补贴'], 300)
        notes = pd.read_excel(BytesIO(export_payroll_to_excel(frame)), sheet_name='核算说明')
        self.assertEqual(notes.loc[notes['项目']=='规则版本', '说明'].iloc[0], '2026-10-02-v1')
        with self.assertRaisesRegex(ValueError, '已确认'):
            self.repo.save(**self.selection)
        self.assertEqual(len(self.repo.history(**self.selection)), 1)
