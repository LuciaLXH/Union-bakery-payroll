from datetime import date
from decimal import Decimal
from pathlib import Path
import os
from unittest.mock import patch

import psycopg
from psycopg import sql
from streamlit.testing.v1 import AppTest

from core.workspace import PayrollWorkspace, entry_frame, employee_frame, EMPLOYEE_DEFAULTS
from test_database_schema import DatabaseFixture


class WorkspaceTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.repo = PayrollWorkspace(self.connection, schema=self.schema)
        self.store = self.repo.stores()[0]
        self.month = self.repo.months(self.store['id'])[0]

    def test_new_month_copies_employee_without_overwriting_old_month(self):
        batch, count = self.repo.start_month(self.store['id'], date(2026,11,1))
        self.assertEqual(count,1)
        self.assertEqual(self.repo.entries(batch['id'])[0]['late_minutes'],0)
        self.connection.execute('UPDATE employees SET monthly_salary=4800')
        _, count = self.repo.start_month(self.store['id'], date(2026,11,1))
        self.assertEqual(count,0)
        self.assertEqual(self.repo.entries(batch['id'])[0]['salary_snapshot'],4500)
        self.assertEqual(self.repo.entries(self.month['id'])[0]['late_minutes'],7)

    def test_save_inputs_and_calculate_is_atomic(self):
        entries = self.repo.entries(self.month['id'])
        frame = entry_frame(entries,self.store['name'])
        versions={e['id']:e['input_version'] for e in entries}
        frame.at[0,'迟到']=10
        saved=self.repo.save_inputs(self.month['id'],frame,versions,calculate=True)
        self.assertEqual(saved[0]['net_pay'],5260)
        self.assertEqual(saved[0]['input_version'],2)
        entries=self.repo.entries(self.month['id'])
        frame=entry_frame(entries,self.store['name'])
        versions={e['id']:e['input_version'] for e in entries}
        frame.at[0,'普通缺勤天数']=0
        frame.at[0,'旷工天数']=30
        with self.assertRaisesRegex(ValueError,'实发金额为负'):
            self.repo.save_inputs(self.month['id'],frame,versions,calculate=True)
        self.assertEqual(self.repo.entries(self.month['id'])[0]['unauthorized_absence_days'],0)
        self.assertEqual(len(self.repo.current_results(self.month['id'])),1)

    def test_old_editor_cannot_overwrite_newer_input(self):
        entries=self.repo.entries(self.month['id'])
        frame=entry_frame(entries,self.store['name'])
        versions={e['id']:e['input_version'] for e in entries}
        self.change('late_minutes=12')
        frame.at[0,'迟到']=10
        with self.assertRaisesRegex(ValueError,'已被其他'):
            self.repo.save_inputs(self.month['id'],frame,versions)
        self.assertEqual(self.repo.entries(self.month['id'])[0]['late_minutes'],12)

    def test_employee_edit_does_not_change_month_snapshot_and_conflicts_checked(self):
        old=self.repo.employees(self.store['id'])[0]
        payload=dict(old,monthly_salary=4800,job_title='烘焙',grade='有基础学徒')
        self.repo.save_employee(self.store['id'],payload,employee_id=old['id'],expected=old)
        self.assertEqual(self.repo.entries(self.month['id'])[0]['salary_snapshot'],4500)
        with self.assertRaisesRegex(ValueError,'已被其他'):
            self.repo.save_employee(self.store['id'],payload,employee_id=old['id'],expected=old)

    def test_inactive_and_future_employees_not_added(self):
        original=self.repo.employees(self.store['id'])[0]
        for code,active,hire in [('INACTIVE',False,None),('FUTURE',True,date(2027,1,1))]:
            self.repo.save_employee(self.store['id'],dict(original,employee_code=code,
                is_active=active,hire_date=hire,job_title='',grade=''))
        batch,count=self.repo.start_month(self.store['id'],date(2026,11,1))
        self.assertEqual(count,1)

    def test_cap_null_zero_and_conflict(self):
        self.repo.set_cap(self.month['id'],0,None)
        self.assertEqual(self.repo.months(self.store['id'])[0]['payroll_cap_snapshot'],Decimal(0))
        with self.assertRaisesRegex(ValueError,'已更新'):
            self.repo.set_cap(self.month['id'],5000,None)
        self.repo.set_cap(self.month['id'],None,Decimal(0))
        self.assertIsNone(self.repo.months(self.store['id'])[0]['payroll_cap_snapshot'])

    def test_batch_employee_add_update_and_month_snapshot(self):
        expected = self.repo.employees(self.store['id'])
        frame = employee_frame(expected)
        frame.at[0, '约定工资'] = 4800.0
        frame.loc[len(frame)] = dict(EMPLOYEE_DEFAULTS, 员工编号='BATCH-002', 姓名='批量新员工', 约定工资=4000.0)
        saved = self.repo.save_employees(self.store['id'], frame, expected)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]['monthly_salary'], 4800)
        self.assertTrue(saved[1]['is_active'])
        self.assertEqual(self.repo.entries(self.month['id'])[0]['salary_snapshot'], 4500)

    def test_batch_error_rolls_back_prior_employee_update(self):
        expected = self.repo.employees(self.store['id'])
        frame = employee_frame(expected)
        frame.at[0, '约定工资'] = 4800.0
        frame.loc[len(frame)] = dict(EMPLOYEE_DEFAULTS, 员工编号='BATCH-002', 姓名='错误员工', 约定工资=-1.0)
        with self.assertRaisesRegex(ValueError, '第 2 行'):
            self.repo.save_employees(self.store['id'], frame, expected)
        self.assertEqual(self.repo.employees(self.store['id'])[0]['monthly_salary'], 4500)
        self.assertEqual(len(self.repo.employees(self.store['id'])), 1)

    def test_batch_duplicate_elsewhere_rolls_back_and_stale_draft_rejected(self):
        expected = self.repo.employees(self.store['id'])
        other = self.repo.add_store('另一门店')
        self.repo.save_employee(other['id'], dict(expected[0], employee_code='TAKEN'))
        frame = employee_frame(expected)
        frame.at[0, '约定工资'] = 4800.0
        frame.loc[len(frame)] = dict(EMPLOYEE_DEFAULTS, 员工编号='TAKEN', 姓名='重复编号')
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.repo.save_employees(self.store['id'], frame, expected)
        self.assertEqual(self.repo.employees(self.store['id'])[0]['monthly_salary'], 4500)
        self.repo.save_employee(self.store['id'], dict(expected[0], monthly_salary=4700),
                                employee_id=expected[0]['id'], expected=expected[0])
        with self.assertRaisesRegex(ValueError, '其他操作更新'):
            self.repo.save_employees(self.store['id'], frame.iloc[:1], expected)
        self.assertEqual(self.repo.employees(self.store['id'])[0]['monthly_salary'], 4700)

    def test_batch_rejects_deleted_employee_duplicate_and_blank_amount(self):
        expected = self.repo.employees(self.store['id'])
        frame = employee_frame(expected)
        with self.assertRaisesRegex(ValueError, '不能通过删行'):
            self.repo.save_employees(self.store['id'], frame.iloc[:0], expected)
        frame.loc[len(frame)] = dict(EMPLOYEE_DEFAULTS, 员工编号=expected[0]['employee_code'], 姓名='重复员工')
        with self.assertRaisesRegex(ValueError, '不能重复'):
            self.repo.save_employees(self.store['id'], frame, expected)
        frame = frame.iloc[:1].copy()
        frame.at[0, '通勤补贴'] = None
        with self.assertRaisesRegex(ValueError, '金额不能留空'):
            self.repo.save_employees(self.store['id'], frame, expected)


class WorkspacePageTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.connection.commit()
        self.addCleanup(self.drop_schema)
        self.schema_patch=patch('ui.common.DB_SCHEMA',self.schema)
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)
        self.env_patch=patch.dict(os.environ,{'DATABASE_URL':os.environ['TEST_DATABASE_URL']})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
        self.assertEqual(len(self.at.exception),0)

    def drop_schema(self):
        self.connection.rollback()  # Release verification reads before DROP on another connection.
        with psycopg.connect(os.environ['TEST_DATABASE_URL'],autocommit=True) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def button(self,label):
        return next(b for b in self.at.button if b.label==label)

    def test_month_save_history_and_reopen(self):
        self.button('保存并计算本月工资').click().run()
        self.assertEqual(len(self.at.exception),0)
        self.assertEqual(len(self.at.get('download_button')),1)
        self.at.switch_page('views/history_page.py').run()
        self.assertEqual(len(self.at.exception),0)
        self.at.switch_page('views/monthly_page.py').run()
        self.assertEqual(len(self.at.get('download_button')),1)
        fresh=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run()
        self.assertEqual(len(fresh.exception),0)
        self.assertEqual(len(fresh.get('download_button')),1)

    def test_unsaved_changes_hide_export_then_save(self):
        self.button('保存并计算本月工资').click().run()
        buffer=next(iter(self.at.session_state['month_buffers'].values()))
        buffer['frame'].at[0,'迟到']=10
        buffer['revision']+=1
        self.at.run()
        self.assertEqual(len(self.at.get('download_button')),0)
        self.button('仅保存，稍后计算').click().run()
        self.assertEqual(len(self.at.get('download_button')),0)
        self.button('保存并计算本月工资').click().run()
        self.assertEqual(len(self.at.exception),0)
        self.assertTrue(any('5,260' in metric.value for metric in self.at.metric))

    def test_add_employee_and_start_new_month_in_ui(self):
        self.at.switch_page('views/employees_page.py').run()
        next(w for w in self.at.text_input if w.label=='员工编号').set_value('UI-002')
        next(w for w in self.at.text_input if w.label=='姓名').set_value('界面测试员工')
        next(w for w in self.at.number_input if w.label=='约定工资（含社保补贴）').set_value(4000.0)
        self.button('保存员工档案').click().run()
        self.assertEqual(len(self.at.exception),0)
        self.at.switch_page('views/monthly_page.py').run()
        next(w for w in self.at.selectbox if w.label=='工资月份').select(date(2026,11,1)).run()
        self.button('建立本月工资').click().run()
        self.assertEqual(len(self.at.exception),0)
        buffer=next(v for k,v in self.at.session_state['month_buffers'].items() if len(v['frame'])==2)
        self.assertIn('UI-002',buffer['frame']['员工编号'].tolist())

    def test_budget_equal_boundary_and_incomplete_results(self):
        self.button('保存并计算本月工资').click().run()
        self.assertFalse(any(w.label == '上限金额（元）' for w in self.at.number_input))
        self.button('设置本月工资上限').click().run()
        next(w for w in self.at.checkbox if w.label=='设置本月工资上限').check()
        next(w for w in self.at.number_input if w.label=='上限金额（元）').set_value(5263.0)
        self.button('保存本月上限').click().run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertTrue(any('已达到本月工资上限' in w.value for w in self.at.warning))
        self.assertFalse(any(w.label == '上限金额（元）' for w in self.at.number_input))
        self.button('设置本月工资上限').click().run()
        next(w for w in self.at.number_input if w.label=='上限金额（元）').set_value(5263.01)
        self.button('保存本月上限').click().run()
        self.assertTrue(any('未达到工资上限' in w.value for w in self.at.info))
        self.assertFalse(any('5,263.01' in w.value for w in self.at.metric))
        self.assertFalse(any(w.label == '上限金额（元）' for w in self.at.number_input))
        buffer=next(iter(self.at.session_state['month_buffers'].values()))
        buffer['frame'].at[0,'迟到']=10
        buffer['revision']+=1
        self.at.run()
        self.assertFalse(any('未达到工资上限' in w.value for w in self.at.info))
        self.assertEqual(len(self.at.get('download_button')),0)

    def test_person_inputs_save_and_calculate_through_widgets(self):
        self.button('保存并计算本月工资').click().run()
        next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').set_value(10).run()
        next(w for w in self.at.text_area if '工作表现' in w.label).set_value('学徒失误已提醒，不扣款').run()
        self.assertEqual(len(self.at.get('download_button')),0)
        self.button('保存并计算本月工资').click().run()
        self.assertEqual(len(self.at.exception),0)
        repo=PayrollWorkspace(self.connection,schema=self.schema)
        result=repo.current_results(repo.months(repo.stores()[0]['id'])[0]['id'])[0]
        self.assertEqual(result['net_pay'],5260)
        self.assertEqual(result['input_snapshot']['work_error_notes'],'学徒失误已提醒，不扣款')

    def test_person_switch_and_table_roundtrip_keep_unsaved_edits(self):
        repo=PayrollWorkspace(self.connection,schema=self.schema)
        store=repo.stores()[0]
        original=repo.employees(store['id'])[0]
        repo.save_employee(store['id'],dict(original,employee_code='SECOND',name='另一位员工'))
        repo.start_month(store['id'],date(2026,10,1))
        self.connection.commit()
        self.button('重新读取已保存数据').click().run()
        next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').set_value(10).run()
        next(w for w in self.at.selectbox if w.label=='选择要填写的员工').select(1).run()
        next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').set_value(20).run()
        next(w for w in self.at.radio if w.label=='填写方式').set_value('表格批量编辑').run()
        frame=next(iter(self.at.session_state['month_buffers'].values()))['frame']
        self.assertEqual(frame['迟到'].tolist(),[10,20])
        next(w for w in self.at.radio if w.label=='填写方式').set_value('按员工填写').run()
        self.assertEqual(next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').value,10)
        self.button('仅保存，稍后计算').click().run()
        self.assertEqual([e['late_minutes'] for e in repo.entries(repo.months(store['id'])[0]['id'])],[10,20])

    def test_discard_restores_input_widgets_and_empty_input_does_not_crash(self):
        next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').set_value(10).run()
        self.button('放弃未保存修改并重新读取').click().run()
        self.assertEqual(next(w for w in self.at.number_input if w.label=='累计迟到（分钟）').value,7)
        # A blank from the batch editor must remain blank in the person form.
        buffer=next(iter(self.at.session_state['month_buffers'].values()))
        buffer['frame'].at[0,'普通缺勤天数']=None
        buffer['revision']+=1
        self.at.run()
        self.assertEqual(len(self.at.exception),0)
        self.button('保存并计算本月工资').click().run()
        self.assertEqual(len(self.at.exception),0)
        self.assertTrue(len(self.at.error)>0)
        self.assertEqual(len(self.at.get('download_button')),0)

    def test_create_store_through_dedicated_page(self):
        self.at.switch_page('views/stores_page.py').run()
        next(w for w in self.at.text_input if w.label=='门店名称').set_value('Union 测试二店')
        self.button('保存新门店').click().run()
        self.assertEqual(len(self.at.exception),0)
        repo=PayrollWorkspace(self.connection,schema=self.schema)
        self.assertIn('Union 测试二店',[s['name'] for s in repo.stores()])

    def test_employee_batch_table_callback_save_and_reload(self):
        from ui.employee_editor import apply_employee_table
        self.at.switch_page('views/employees_page.py').run()
        next(w for w in self.at.radio if w.label == '填写方式').set_value('表格批量编辑').run()
        self.assertEqual(len(self.at.exception), 0)
        store_id, buffer = next(iter(self.at.session_state['employee_buffers'].items()))
        key = f'employee_table_{store_id}_{buffer["generation"]}_{buffer["revision"]}'
        self.at.session_state[key] = dict(edited_rows={0: {'约定工资': 4800.0}},
            added_rows=[{'员工编号': 'UI-BATCH', '姓名': '表格新增', '约定工资': 4000.0}], deleted_rows=[])
        with patch('ui.employee_editor.st.session_state', self.at.session_state):
            apply_employee_table(key, store_id)
        self.at.run()
        self.assertEqual(len(self.at.exception), 0)
        next(w for w in self.at.radio if w.label == '填写方式').set_value('按员工填写').run()
        next(w for w in self.at.radio if w.label == '填写方式').set_value('表格批量编辑').run()
        self.button('保存全部员工档案').click().run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertTrue(any('已保存 2 名' in w.value for w in self.at.success))
        repo = PayrollWorkspace(self.connection, schema=self.schema)
        rows = repo.employees(store_id)
        self.assertEqual([e['monthly_salary'] for e in rows], [4800, 4000])
        self.assertEqual(repo.entries(repo.months(store_id)[0]['id'])[0]['salary_snapshot'], 4500)

    def test_empty_store_employee_table_renders(self):
        repo = PayrollWorkspace(self.connection, schema=self.schema)
        store = repo.add_store('空门店')
        self.connection.commit()
        self.at.switch_page('views/employees_page.py').run()
        next(w for w in self.at.selectbox if w.label == '门店').select(store['id']).run()
        next(w for w in self.at.radio if w.label == '填写方式').set_value('表格批量编辑').run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertTrue(self.button('保存全部员工档案').disabled)
