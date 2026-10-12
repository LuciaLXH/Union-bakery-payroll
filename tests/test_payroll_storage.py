"""真实 PostgreSQL 隔离测试，覆盖保存、过期、回滚及并发。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import os
from threading import Barrier, Event
from unittest.mock import patch

import psycopg
from psycopg import sql

import config
from core.payroll_storage import PayrollStorage
from core.payroll_calc import calculate_payroll
from test_database_schema import DatabaseFixture


class PayrollStorageTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.storage = PayrollStorage(self.connection, schema=self.schema)
        self.selection = dict(store_name='隔离测试门店', payroll_month=date(2026, 10, 1))

    def test_save_and_repeat_keep_same_record_and_snapshot(self):
        saved = self.storage.save(**self.selection)[0]
        self.assertEqual(saved['net_pay'], Decimal('5263.00'))
        self.assertEqual(saved['total_deductions'], Decimal('187.00'))
        self.assertEqual(saved['input_snapshot']['salary_snapshot'], '4500.00')
        self.assertEqual(saved['input_snapshot']['late_minutes'], 7)
        repeated = self.storage.save(**self.selection)[0]
        self.assertFalse(saved['reused'])
        self.assertTrue(repeated['reused'])
        self.assertEqual(saved['id'], repeated['id'])
        self.assertEqual(saved['calculated_at'], repeated['calculated_at'])
        self.assertEqual(len(self.storage.history(**self.selection)), 1)

    def test_edit_invalidates_and_recalculate_preserves_history(self):
        self.assertEqual(self.storage.status(**self.selection)[0]['calculation_status'], '待核算')
        self.storage.save(**self.selection)
        self.change('late_minutes = 10')
        current = self.storage.status(**self.selection)[0]
        self.assertEqual(current['calculation_status'], '待重新核算')
        self.assertIsNone(current['net_pay'])
        saved = self.storage.save(**self.selection)[0]
        self.assertEqual(saved['input_version'], 2)
        self.assertEqual(saved['net_pay'], Decimal('5260.00'))
        history = self.storage.history(**self.selection)
        self.assertEqual([row['input_snapshot']['late_minutes'] for row in history], [7, 10])
        self.assertEqual([row['net_pay'] for row in history], [5263, 5260])
        self.assertEqual(self.storage.status(**self.selection)[0]['calculation_status'], '已核算')

    def test_employee_master_raise_does_not_change_monthly_result(self):
        self.connection.execute('UPDATE employees SET monthly_salary=4800')
        result = self.storage.save(**self.selection)[0]
        self.assertEqual(result['net_pay'], 5263)
        self.assertEqual(result['input_snapshot']['salary_snapshot'], '4500.00')

    def test_rule_version_change_requires_new_calculation(self):
        self.storage.save(**self.selection)
        with patch.object(config, 'RULE_VERSION', 'test-next-rule'):
            current = self.storage.status(**self.selection)[0]
            self.assertEqual(current['calculation_status'], '待重新核算')
            self.assertIsNone(current['net_pay'])
            result = self.storage.save(**self.selection)[0]
            self.assertEqual(result['rule_version'], 'test-next-rule')
            self.assertFalse(result['reused'])
        self.assertEqual(len(self.storage.history(**self.selection)), 2)

    def test_confirmed_month_cannot_be_saved(self):
        self.connection.execute("UPDATE payroll_months SET status='confirmed'")
        with self.assertRaisesRegex(ValueError, '已确认'):
            self.storage.save(**self.selection)
        self.assertEqual(self.storage.history(**self.selection), [])

    def test_missing_and_special_character_selection_do_not_write(self):
        for code in ('missing', "TEST-001' OR TRUE --"):
            with self.assertRaisesRegex(ValueError, '未找到'):
                self.storage.save(**self.selection, employee_code=code)
        with self.assertRaisesRegex(ValueError, '未找到'):
            self.storage.save(store_name='隔离测试门店', payroll_month=date(2026, 11, 1))
        self.assertEqual(self.storage.history(**self.selection), [])

    def test_batch_rolls_back_first_employee_when_second_is_invalid(self):
        employee_id = self.connection.execute('''INSERT INTO employees (
            employee_code,name,store_id,monthly_salary,monthly_performance,monthly_attendance_bonus)
            SELECT 'TEST-002','第二名员工',id,4500,600,300 FROM stores RETURNING id''').fetchone()[0]
        self.connection.execute('''INSERT INTO payroll_entries (
            payroll_month_id,employee_id,employee_code_snapshot,employee_name_snapshot,
            salary_snapshot,performance_snapshot,attendance_bonus_snapshot,commute_allowance_snapshot,
            unauthorized_absence_days)
            SELECT id,%s,'TEST-002','第二名员工',4500,600,300,0,30 FROM payroll_months''', (employee_id,))
        with self.assertRaisesRegex(ValueError, '实发金额为负'):
            self.storage.save(**self.selection)
        self.assertEqual(self.storage.history(**self.selection), [])
        result = self.storage.save(**self.selection, employee_code='TEST-001')
        self.assertEqual(len(result), 1)

    def test_note_change_updates_snapshot_without_changing_pay(self):
        self.storage.save(**self.selection)
        self.change("work_error_notes = '已指导，不扣款'")
        saved = self.storage.save(**self.selection)[0]
        self.assertEqual(saved['input_version'], 2)
        self.assertEqual(saved['net_pay'], 5263)
        self.assertEqual(saved['input_snapshot']['work_error_notes'], '已指导，不扣款')

    def test_large_amount_retains_exact_cents(self):
        self.change('salary_snapshot=999999999.99, overtime_hours=999999999.99')
        saved = self.storage.save(**self.selection)[0]
        expected_overtime = (Decimal('999999999.99') ** 2 / Decimal(180)).quantize(Decimal('.01'))
        self.assertEqual(saved['overtime_pay'], expected_overtime)
        self.assertEqual(saved['net_pay'], saved['gross_pay'] - saved['total_deductions'])


class ConcurrentStorageTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        # 并发连接需要看见隔离结构，所以提交此随机 schema，测试后单独删除。
        self.connection.commit()
        self.addCleanup(self.drop_schema)

    def drop_schema(self):
        with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as cleanup:
            cleanup.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_simultaneous_saves_create_only_one_result(self):
        barrier = Barrier(2)

        def save():
            with psycopg.connect(os.environ['TEST_DATABASE_URL'],
                                 options='-c lock_timeout=5000') as connection:
                barrier.wait(timeout=5)
                return PayrollStorage(connection, schema=self.schema).save(
                    store_name='隔离测试门店', payroll_month=date(2026, 10, 1))[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: save(), range(2)))
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(sorted([first['reused'], second['reused']]), [False, True])

    def test_input_edit_cannot_slip_between_read_and_save(self):
        calculating = Event()
        release = Event()

        def paused_calculation(*args, **kwargs):
            calculating.set()
            if not release.wait(timeout=5):
                raise TimeoutError('测试未释放核算线程')
            return calculate_payroll(*args, **kwargs)

        def save():
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
                return PayrollStorage(connection, schema=self.schema).save(
                    store_name='隔离测试门店', payroll_month=date(2026, 10, 1))[0]

        with patch('core.payroll_storage.calculate_payroll', side_effect=paused_calculation):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(save)
                try:
                    self.assertTrue(calculating.wait(timeout=5))
                    with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True,
                                         options='-c lock_timeout=100') as editor:
                        with self.assertRaises(psycopg.errors.LockNotAvailable):
                            editor.execute(sql.SQL('UPDATE {} SET late_minutes=10').format(
                                sql.Identifier(self.schema, 'payroll_entries')))
                finally:
                    release.set()
                saved = future.result(timeout=5)
        self.assertEqual(saved['net_pay'], 5263)
        self.assertEqual(saved['input_version'], 1)
