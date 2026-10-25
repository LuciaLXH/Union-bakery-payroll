"""显式设置 TEST_DATABASE_URL 后测试真实 PostgreSQL，全部在隔离 schema 中回滚。"""
import os
from pathlib import Path
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), '未设置 TEST_DATABASE_URL，跳过数据库集成测试')
class DatabaseFixture(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(os.environ['TEST_DATABASE_URL'], connect_timeout=5)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.schema = 'payroll_test_' + uuid4().hex
        self.connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        for filename in ('001_schema_baseline.sql', '002_add_attendance.sql',
                         '003_calculation_history.sql', '004_ai_entry.sql'):
            content = (ROOT / 'database' / filename).read_text()
            # 基线含 psql 元命令；测试用外层事务代替脚本自身 BEGIN/COMMIT。
            content = '\n'.join(line for line in content.splitlines()
                                if not line.startswith('\\')
                                and line.strip() not in ('BEGIN;', 'COMMIT;'))
            content = content.replace('public.', self.schema + '.')
            self.connection.execute(content, prepare=False)
        self.connection.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(
            sql.Identifier(self.schema)))
        store_id = self.connection.execute(
            "INSERT INTO stores(name) VALUES ('隔离测试门店') RETURNING id").fetchone()[0]
        employee_id = self.connection.execute("""
            INSERT INTO employees(employee_code, name, store_id, monthly_salary,
                                  monthly_performance, monthly_attendance_bonus)
            VALUES ('TEST-001', '虚构员工', %s, 4500, 600, 300) RETURNING id
        """, (store_id,)).fetchone()[0]
        month_id = self.connection.execute("""
            INSERT INTO payroll_months(store_id, payroll_month)
            VALUES (%s, '2026-10-01') RETURNING id
        """, (store_id,)).fetchone()[0]
        self.entry_id = self.connection.execute("""
            INSERT INTO payroll_entries(payroll_month_id, employee_id,
                employee_code_snapshot, employee_name_snapshot, salary_snapshot,
                performance_snapshot, attendance_bonus_snapshot,
                commute_allowance_snapshot, ordinary_absence_days, late_minutes,
                overtime_hours)
            VALUES (%s, %s, 'TEST-001', '虚构员工', 4500, 600, 300, 0, 1, 7, 2)
            RETURNING id
        """, (month_id, employee_id)).fetchone()[0]

    def change(self, assignment):
        return self.connection.execute(
            'UPDATE payroll_entries SET ' + assignment + ' RETURNING input_version'
        ).fetchone()[0]

    def save_example(self, net_pay=5263):
        return self.connection.execute("""
            INSERT INTO payroll_calculations (
                payroll_entry_id, input_version, rule_version, input_snapshot,
                salary_deduction, performance_deduction, attendance_deduction,
                late_deduction, overtime_pay, gross_pay, total_deductions, net_pay)
            SELECT d.id, d.input_version, '2026-10-02-v1', to_jsonb(d),
                   150, 20, 10, 7, 50, 5450, 187, %s
            FROM payroll_entries d WHERE d.id = %s
            RETURNING id
        """, (net_pay, self.entry_id)).fetchone()[0]

class CalculationSchemaTests(DatabaseFixture):
    def test_changed_inputs_increment_and_noop_does_not(self):
        self.assertEqual(self.change('late_minutes = 7'), 1)
        self.assertEqual(self.change('late_minutes = 10'), 2)
        self.assertEqual(self.change("work_error_notes = '仅备注也需更新工资条'"), 3)
        self.assertEqual(self.change('salary_snapshot = 4800'), 4)
        self.assertEqual(self.change('input_version = 1'), 4)
        self.assertEqual(self.change('salary_snapshot = 4500'), 5)

    def test_old_result_preserved_but_no_longer_current(self):
        self.save_example()
        self.change('late_minutes = 10')
        old = self.connection.execute(
            "SELECT input_version, input_snapshot->>'late_minutes', net_pay "
            'FROM payroll_calculations').fetchone()
        self.assertEqual(old, (1, '7', 5263))
        count = self.connection.execute("""
            SELECT count(*) FROM payroll_calculations c
            JOIN payroll_entries d ON d.id = c.payroll_entry_id
            WHERE c.input_version = d.input_version AND c.rule_version = '2026-10-02-v1'
        """).fetchone()[0]
        self.assertEqual(count, 0)

    def test_duplicate_calculation_rejected(self):
        self.save_example()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.connection.transaction():
                self.save_example()

    def test_bad_totals_and_nan_rejected(self):
        for net_pay in (9999, 'NaN'):
            with self.subTest(net_pay=net_pay):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.connection.transaction():
                        self.save_example(net_pay)


if __name__ == '__main__':
    unittest.main()
