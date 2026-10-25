from ai_entry.cloud import AIWorkspace
from ai_entry.review import review_rows
from test_database_schema import DatabaseFixture, ROOT


def record(amount=10, mode='新增', source='10月8日迟到10分钟', kind='迟到'):
    item = dict(name='TEST-001', kind=kind, amount=amount, unit='分钟', mode=mode, source=source, question='')
    return review_rows([item], {'TEST-001':'虚构员工'}, {'TEST-001':'隔离测试门店'})[0]


class AICloudTests(DatabaseFixture):
    def setUp(self):
        super().setUp()
        self.connection.execute((ROOT/'database/004_ai_entry.sql').read_text().replace('public.', self.schema+'.'))
        self.repo = AIWorkspace(self.connection, schema=self.schema)

    def test_add_events_preserve_dates_and_duplicate_blocked(self):
        rows = [record(), record(5, source='10月15日迟到5分钟')]
        p = self.repo.prepare('2026-10', rows)
        self.repo.apply('2026-10', rows, p['token'])
        late, notes = self.connection.execute('SELECT late_minutes,work_error_notes FROM payroll_entries').fetchone()
        self.assertEqual(late, 22)
        self.assertIn('10月8日', notes)
        self.assertIn('10月15日', notes)
        with self.assertRaisesRegex(ValueError,'相同事项'):
            self.repo.apply('2026-10', rows, p['token'])

    def test_stale_preview_blocks_write(self):
        rows = [record()]
        p = self.repo.prepare('2026-10', rows)
        self.change('late_minutes=25')
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.repo.apply('2026-10', rows, p['token'])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ai_entry_events').fetchone()[0],0)

    def test_cumulative_replaces_and_notes_preserved(self):
        rows = [record(12,'累计')]
        p = self.repo.prepare('2026-10', rows)
        self.repo.apply('2026-10', rows,p['token'])
        self.assertEqual(self.connection.execute('SELECT late_minutes FROM payroll_entries').fetchone()[0],12)

    def test_mixed_modes_rejected(self):
        with self.assertRaisesRegex(ValueError,'全部'):
            self.repo.prepare('2026-10',[record(),record(15,'累计','本月累计迟到15分钟')])

    def test_missing_month_no_mutation(self):
        with self.assertRaisesRegex(ValueError,'尚未建立'):
            self.repo.prepare('2026-12',[record()])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM payroll_months').fetchone()[0],1)

    def test_deduction_not_applied(self):
        row=record(20,kind='扣款');row['单位']='元'
        with self.assertRaisesRegex(ValueError,'尚未接入'):
            self.repo.prepare('2026-10',[row])

    def test_catalog_change_invalidates_preview(self):
        rows=[record()];p=self.repo.prepare('2026-10',rows)
        self.connection.execute("UPDATE employees SET name='改名员工'")
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.repo.apply('2026-10',rows,p['token'])

    def test_cross_store_failure_rolls_back_first_store(self):
        from core.workspace import entry_frame
        other = self.repo.add_store('另一门店')
        employee = self.repo.employees(self.repo.stores()[0]['id'])[0]
        self.repo.save_employee(other['id'],dict(employee,employee_code='TEST-002',name='另一员工'))
        from datetime import date
        self.repo.start_month(other['id'],date(2026,10,1))
        row2 = dict(record(), 员工编号='TEST-002', 数量=1.123, 事项='加班', 单位='小时')
        rows = [record(),row2]
        p = self.repo.prepare('2026-10',rows)
        # A save failure in the second store must not partially commit the first store.
        from unittest.mock import patch
        original=self.repo.save_inputs
        calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:
                raise ValueError('模拟第二门店失败')
            return original(*args,**kwargs)
        with patch.object(self.repo,'save_inputs',side_effect=fail_second):
            with self.assertRaisesRegex(ValueError,'模拟'):
                self.repo.apply('2026-10',rows,p['token'])
        self.assertEqual(self.connection.execute('SELECT late_minutes FROM payroll_entries WHERE id=%s',(self.entry_id,)).fetchone()[0],7)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ai_entry_events').fetchone()[0],0)


import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from ai_entry.service import Settings


class AIPageTests(unittest.TestCase):
    def test_review_preview_write_and_stale_input(self):
        employee=dict(id=1,employee_code='TEST-001',name='虚构员工',store_id=1,store_name='隔离测试门店')
        item=dict(name='TEST-001',kind='迟到',amount=10,unit='分钟',mode='新增',source='TEST-001迟到10分钟',question='')
        with patch.dict('os.environ', {'APP_MODE':'local','RENDER':'','DATABASE_URL':'test-placeholder'}), \
             patch('ui.common.db',return_value=[]), patch('psycopg.connect'), \
             patch('ai_entry.cloud.AIWorkspace') as repo, patch('ai_entry.cloud.CloudBudget') as budget, \
             patch('ai_entry.service.Settings.from_env',return_value=Settings(key='test',provider='qwen')), \
             patch('ai_entry.service.extract',return_value=[item]):
            repo.return_value.catalog.return_value=[employee]
            repo.return_value.prepare.return_value=dict(token='preview-token',changes=[{'员工':'虚构员工','写入后':'10'}])
            repo.return_value.apply.return_value=1
            budget.return_value.spent.return_value=0
            at=AppTest.from_file(str(ROOT/'app.py')).run()
            at.switch_page('views/ai_entry_page.py').run()
            self.assertEqual(len(at.exception),0)
            self.assertEqual(at.radio[0].value,'AI 识别')
            at.text_area[0].set_value('TEST-001迟到10分钟')
            next(b for b in at.button if b.label=='识别文字').click().run()
            next(b for b in at.button if b.label=='预览写入变更').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertTrue(next(b for b in at.button if b.label=='写入当月工资记录').disabled)
            at.checkbox[0].check().run()
            next(b for b in at.button if b.label=='写入当月工资记录').click().run()
            repo.return_value.apply.assert_called_once()
            self.assertTrue(any('已写入 1' in s.value for s in at.success))
            next(b for b in at.button if b.label=='识别文字').click().run()
            at.text_area[0].set_value('修改后的原文').run()
            self.assertFalse(any(b.label=='写入当月工资记录' for b in at.button))
            self.assertEqual(len(at.exception),0)
