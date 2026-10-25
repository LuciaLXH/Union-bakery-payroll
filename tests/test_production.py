from datetime import date
from io import BytesIO
import os
from pathlib import Path
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from streamlit.testing.v1 import AppTest

import config
from ai_entry.cloud import AIWorkspace
from core.access import password_hash
from core.production import bind_connection, check_owner_login, new_owner_session, owner_session_valid
from core.workspace import PayrollWorkspace, entry_frame, result_frame
from core.excel_handler import export_payroll_to_excel
from test_database_schema import DatabaseFixture


class LifecycleTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.repo = PayrollWorkspace(self.connection, schema=self.schema)
        self.month = self.repo.months()[0]
        self.mid = self.month['id']

    def calculate(self):
        return self.repo.save(store_name='隔离测试门店', payroll_month=date(2026,10,1))

    def confirm(self):
        results = self.calculate()
        return self.repo.transition_month(self.mid, 'confirm', 1, calculation_ids=[r['id'] for r in results])

    def test_confirmation_rejects_missing_stale_or_different_results_and_cap(self):
        with self.assertRaises(ValueError):
            self.repo.transition_month(self.mid, 'confirm', 1)
        result = self.calculate()[0]
        for ids, cap in [([], None), ([result['id']], 123)]:
            with self.assertRaises(ValueError):
                self.repo.transition_month(self.mid, 'confirm', 1, calculation_ids=ids, expected_cap=cap)
        self.change('late_minutes=10')
        with self.assertRaises(ValueError):
            self.repo.transition_month(self.mid, 'confirm', 1, calculation_ids=[result['id']])
        self.assertEqual(self.repo.months()[0]['status'], 'draft')

    def test_locked_manual_ai_direct_sql_and_calculation_history(self):
        self.confirm()
        entries = self.repo.entries(self.mid)
        with self.assertRaises(ValueError):
            self.repo.save_inputs(self.mid, entry_frame(entries,'隔离测试门店'), {e['id']:e['input_version'] for e in entries})
        records = [{'员工编号':'TEST-001', '事项':'迟到', '数量':5, '单位':'分钟',
                    '处理方式':'新增', '原文 / 备注':'10月9日迟到5分钟'}]
        with self.assertRaisesRegex(ValueError, '已确认'):
            AIWorkspace(self.connection, schema=self.schema).prepare('2026-10', records)
        for statement in ['UPDATE payroll_entries SET late_minutes=10',
                          'DELETE FROM payroll_entries',
                          'UPDATE payroll_calculations SET input_snapshot=\'{}\'',
                          'DELETE FROM payroll_calculations']:
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                with self.connection.transaction():
                    self.connection.execute(statement)

    def test_paid_reopen_audit_and_stale_buttons(self):
        confirmed = self.confirm()
        ids = confirmed['confirmed_calculation_ids']
        self.assertEqual(confirmed['lifecycle_version'], 2)
        with self.assertRaises(ValueError):
            self.repo.transition_month(self.mid, 'paid', 1)
        paid = self.repo.transition_month(self.mid, 'paid', 2)
        self.assertIsNotNone(paid['paid_at'])
        with self.assertRaises(ValueError):
            self.repo.transition_month(self.mid, 'reopen', 3, reason=' ')
        reopened = self.repo.transition_month(self.mid, 'reopen', 3, reason='补记10月9日迟到')
        self.assertEqual(reopened['status'], 'draft')
        self.assertIsNone(reopened['paid_at'])
        entries = self.repo.entries(self.mid)
        frame = entry_frame(entries, '隔离测试门店')
        with self.assertRaisesRegex(ValueError, '状态已变化'):
            self.repo.save_inputs(self.mid, frame, {e['id']:e['input_version'] for e in entries}, expected_lifecycle=1)
        frame.at[0,'迟到'] = 12
        self.repo.save_inputs(self.mid, frame, {e['id']:e['input_version'] for e in entries}, calculate=True, expected_lifecycle=4)
        history = self.repo.history(store_name='隔离测试门店', payroll_month=date(2026,10,1))
        self.assertEqual([r['net_pay'] for r in history], [5263,5258])
        old_confirm = self.connection.execute("SELECT after_record FROM audit_log WHERE after_record->>'status'='confirmed'").fetchone()[0]
        self.assertEqual(old_confirm['confirmed_calculation_ids'], ids)
        self.assertIn('补记10月9日迟到', [a[2] for a in self.repo.month_actions(self.mid)])

    def test_confirmed_results_and_export_survive_rule_release(self):
        self.confirm()
        with patch.object(config, 'RULE_VERSION', 'future-rules'):
            results = self.repo.current_results(self.mid)
            self.assertEqual(len(results), 1)
            self.assertEqual(self.repo.status(store_name='隔离测试门店', payroll_month=date(2026,10,1))[0]['net_pay'], 5263)
            frame = result_frame(results)
            frame['确认状态'] = '已确认（未标记发放）'
            notes = pd.read_excel(BytesIO(export_payroll_to_excel(frame)), sheet_name='核算说明')
            self.assertNotIn('future-rules', notes.to_string())
            self.assertIn('已确认', notes.to_string())

    def test_database_rejects_incomplete_confirmation_and_missing_reason(self):
        self.calculate()
        with self.assertRaises(psycopg.Error), self.connection.transaction():
            self.connection.execute("UPDATE payroll_months SET status='confirmed'")
        with self.assertRaises(psycopg.Error), self.connection.transaction():
            self.connection.execute("SELECT set_config('app.reason','核对',true)")
            self.connection.execute("UPDATE payroll_months SET status='confirmed',confirmed_rule_version='v',confirmed_calculation_ids=ARRAY[9999]::bigint[]")


class OwnerAccessTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.role = 'owner_test_' + uuid4().hex
        self.instance = str(uuid4())
        self.password = secrets.token_urlsafe(24)
        self.env = patch.dict(os.environ, dict(APP_MODE='production', OWNER_USERNAME='boss',
            OWNER_PASSWORD_HASH=password_hash(self.password), PRODUCTION_INSTANCE_ID=self.instance,
            DATABASE_URL=os.environ['TEST_DATABASE_URL']))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.connection.execute("UPDATE app_environment SET environment='production',instance_id=%s", (self.instance,))
        r, s = sql.Identifier(self.role), sql.Identifier(self.schema)
        self.connection.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(r))
        self.connection.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(s,r))
        self.connection.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(s,r))
        self.connection.execute(sql.SQL('GRANT UPDATE ON {} TO {}').format(sql.Identifier(self.schema,'owner_login_attempts'), r))

    def restricted(self):
        self.connection.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(self.role)))

    def test_owner_login_throttle_rotation_and_expiry(self):
        self.restricted()
        for _ in range(5):
            self.assertEqual(check_owner_login(self.connection, 'wrong', self.password, schema=self.schema), 'invalid')
        self.assertEqual(check_owner_login(self.connection, 'boss', self.password, schema=self.schema), 'wait')
        self.connection.execute("UPDATE owner_login_attempts SET blocked_until=clock_timestamp()-interval '1 second'")
        self.assertEqual(check_owner_login(self.connection, 'boss', self.password, schema=self.schema), 'ok')
        session = new_owner_session(time.time())
        self.assertTrue(owner_session_valid(session))
        self.assertFalse(owner_session_valid(dict(session, time=0)))
        with patch.dict(os.environ, OWNER_PASSWORD_HASH=password_hash(self.password)):
            self.assertFalse(owner_session_valid(session))

    def test_production_rejects_admin_mismatched_instance_and_test_mode(self):
        with self.assertRaisesRegex(ValueError, '受限'):
            bind_connection(self.connection, schema=self.schema)
        self.restricted()
        bind_connection(self.connection, schema=self.schema)
        with patch.dict(os.environ, PRODUCTION_INSTANCE_ID=str(uuid4())), self.assertRaisesRegex(ValueError, '不一致'):
            bind_connection(self.connection, schema=self.schema)
        with patch.dict(os.environ, APP_MODE='local'), self.assertRaisesRegex(ValueError, '不能连接正式'):
            bind_connection(self.connection, schema=self.schema)

    def test_ui_expired_fragment_callbacks_cannot_write(self):
        from ui.access import check_access
        with patch('ui.access.st.session_state', {'owner_access':new_owner_session(0)}):
            with self.assertRaisesRegex(ValueError, '登录已失效'):
                check_access()


class LifecyclePageTests(DatabaseFixture):
    def test_confirm_paid_reopen_in_real_page(self):
        self.connection.commit()
        def cleanup():
            self.connection.rollback()
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as c:
                c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.addCleanup(cleanup)
        with patch('ui.common.DB_SCHEMA',self.schema), patch.dict(os.environ, DATABASE_URL=os.environ['TEST_DATABASE_URL'], APP_MODE='local', RENDER=''):
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=20).run()
            button=lambda label: next(b for b in at.button if b.label==label)
            checkbox=lambda text: next(b for b in at.checkbox if text in b.label)
            button('保存并计算本月工资').click().run()
            checkbox('我已核对员工名单').check().run()
            button('确认并锁定本月工资').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertTrue(button('保存并计算本月工资').disabled)
            checkbox('我已在系统外').check().run()
            button('标记已发放').click().run()
            next(t for t in at.text_area if t.label=='更正原因').set_value('补记迟到').run()
            checkbox('我理解旧确认记录').check().run()
            button('记录原因并解除锁定').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertFalse(button('保存并计算本月工资').disabled)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL') and os.environ.get('PG_BIN'), '需要显式本地测试库及 PG_BIN')
class BackupRestoreTests(unittest.TestCase):
    def test_empty_initialize_permissions_backup_restore_and_reject_overwrite(self):
        from scripts.production_database import initialize
        from scripts.backup_database import backup, restore_check
        params = conninfo_to_dict(os.environ['TEST_DATABASE_URL'])
        if params.get('host') not in ('localhost','127.0.0.1'):
            self.skipTest('恢复演练只自动创建本地临时库')
        suffix=uuid4().hex
        source, target='bakery_restore_source_'+suffix, 'bakery_restore_target_'+suffix
        role='backup_test_'+suffix
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as admin:
            try:
                for name in (source,target):
                    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                src=make_conninfo(**dict(params,dbname=source))
                dst=make_conninfo(**dict(params,dbname=target))
                with psycopg.connect(src) as c:
                    initialize(c,secrets.token_urlsafe(32),role=role)
                    repo=PayrollWorkspace(c)
                    s=repo.add_store('备份演练虚构店')
                    repo.save_employee(s['id'],dict(employee_code='RESTORE-001',name='虚构员工',monthly_salary=4500,
                        monthly_performance=600,monthly_attendance_bonus=300,monthly_commute_allowance=0))
                    m,_=repo.start_month(s['id'],date(2026,10,1))
                    results=repo.save(store_name=s['name'],payroll_month=date(2026,10,1))
                    repo.transition_month(m['id'],'confirm',1,calculation_ids=[r['id'] for r in results])
                    # Verify least privileges of the actual initializer-created role.
                    c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
                    for table in ('audit_log','payroll_calculations','app_environment'):
                        column = {'audit_log':'reason','payroll_calculations':'input_snapshot','app_environment':'environment'}[table]
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege), c.transaction():
                            c.execute(sql.SQL('UPDATE {} SET {}={}').format(sql.Identifier('public',table),
                                sql.Identifier(column), sql.Identifier(column)))
                with tempfile.TemporaryDirectory() as tmp:
                    location=Path(tmp)/'backup'
                    manifest=backup(src,location)
                    self.assertEqual(restore_check(dst,location),manifest['tables'])
                    self.assertEqual((location/'payroll.dump').stat().st_mode & 0o777,0o600)
                    with self.assertRaisesRegex(ValueError,'非空'):
                        restore_check(dst,location)
                    with psycopg.connect(dst) as c:
                        repo=PayrollWorkspace(c)
                        self.assertEqual(repo.current_results(m['id'])[0]['net_pay'],5400)
                        with self.assertRaises(psycopg.Error), c.transaction():
                            c.execute('UPDATE payroll_entries SET late_minutes=5')
            finally:
                for name in (target,source):
                    admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
                admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))
