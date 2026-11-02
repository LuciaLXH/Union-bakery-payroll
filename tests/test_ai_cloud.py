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

    def test_mixed_leave_waste_and_lateness_reach_results_and_excel(self):
        from datetime import date
        from io import BytesIO
        import pandas as pd
        from core.workspace import result_frame
        from core.excel_handler import export_payroll_to_excel
        store = self.repo.stores()[0]
        first = self.repo.employees(store['id'])[0]
        self.repo.save_employee(store['id'], dict(first, employee_code='TEST-002', name='虚构小杨'))
        self.repo.start_month(store['id'], date(2026,10,1))
        self.connection.execute('UPDATE payroll_entries SET ordinary_absence_days=0,late_minutes=0,overtime_hours=0')
        items = [dict(name='TEST-001',kind='普通缺勤',amount=1,unit='天',mode='新增',source='TEST-001 10月1日请假一天',question=''),
                 dict(name='TEST-002',kind='备注',amount=None,unit='',mode='不适用',source='TEST-002报废200g面粉',question=''),
                 dict(name='TEST-002',kind='迟到',amount=15,unit='分钟',mode='新增',source='TEST-002 10月21日迟到15分钟',question='')]
        catalog=self.repo.catalog()
        rows=review_rows(items,{e['employee_code']:e['name'] for e in catalog},
                         {e['employee_code']:e['store_name'] for e in catalog})
        before=self.repo.save(store_name=store['name'],payroll_month=date(2026,10,1))
        self.assertEqual([r['net_pay'] for r in before],[5400,5400])
        preview=self.repo.prepare('2026-10',rows)
        self.repo.apply('2026-10',rows,preview['token'],calculate=True)
        mid=self.repo.months()[0]['id']
        self.assertEqual([r['net_pay'] for r in self.repo.current_results(mid)],[5220,5385])
        self.assertEqual([r['本次确认后预计实发'] for r in preview['impacts']],[5220,5385])
        results=self.repo.save(store_name=store['name'],payroll_month=date(2026,10,1))
        self.assertEqual([r['net_pay'] for r in results],[5220,5385])
        self.assertEqual(results[0]['total_deductions'],180)
        self.assertEqual(results[1]['late_deduction'],15)
        export=pd.read_excel(BytesIO(export_payroll_to_excel(result_frame(self.repo.current_results(mid)))))
        self.assertEqual(export['合计'].tolist(),[5220,5385])
        self.assertIn('200g面粉',export.iloc[1]['工作失误项目'])
        self.assertIn('10月21日',export.iloc[1]['工作失误项目'])

    def test_catalog_change_invalidates_preview(self):
        rows=[record()];p=self.repo.prepare('2026-10',rows)
        self.connection.execute("UPDATE employees SET name='改名员工'")
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.repo.apply('2026-10',rows,p['token'])

    def test_combined_calculate_failure_rolls_back_every_store_and_event(self):
        from datetime import date
        from unittest.mock import patch
        other=self.repo.add_store('另一门店')
        first=self.repo.employees(self.repo.stores()[0]['id'])[0]
        self.repo.save_employee(other['id'],dict(first,employee_code='TEST-002',name='另一员工'))
        self.repo.start_month(other['id'],date(2026,10,1))
        rows=[record(),dict(record(),员工编号='TEST-002')]
        preview=self.repo.prepare('2026-10',rows)
        original=self.repo.save
        def fail_second(**kwargs):
            if kwargs['store_name']=='另一门店':
                raise ValueError('模拟第二店计算失败')
            return original(**kwargs)
        with patch.object(self.repo,'save',side_effect=fail_second):
            with self.assertRaisesRegex(ValueError,'计算失败'):
                self.repo.apply('2026-10',rows,preview['token'],True)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM payroll_calculations').fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ai_entry_events').fetchone()[0],0)
        self.assertEqual(self.repo.entries(self.repo.months()[0]['id'])[0]['input_version'],1)
        self.repo.apply('2026-10',rows,preview['token'],True)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM payroll_calculations').fetchone()[0],2)
        with self.assertRaisesRegex(ValueError,'相同事项'):
            self.repo.apply('2026-10',rows,preview['token'],True)

    def test_combined_calculate_rejects_negative_pay_without_saving(self):
        row=dict(record(),事项='旷工',数量=30,单位='天',处理方式='累计')
        self.change('ordinary_absence_days=0')
        preview=self.repo.prepare('2026-10',[row])
        with self.assertRaisesRegex(ValueError,'负'):
            self.repo.apply('2026-10',[row],preview['token'],True)
        self.assertEqual(self.connection.execute('SELECT unauthorized_absence_days FROM payroll_entries').fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ai_entry_events').fetchone()[0],0)

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
             patch('ui.common.db',side_effect=lambda method,*args,**kw: [{'id':1,'name':'隔离测试门店'}] if method=='stores' else []), patch('psycopg.connect'), \
             patch('ui.ai_entry.AIWorkspace') as repo, patch('ui.ai_entry.CloudBudget') as budget, \
             patch('ai_entry.service.Settings.from_env',return_value=Settings(key='test',provider='qwen')), \
             patch('ui.ai_entry.extract',return_value=[item]):
            repo.return_value.catalog.return_value=[employee]
            repo.return_value.prepare.return_value=dict(token='preview-token',changes=[],impacts=[{'员工':'虚构员工','实发变化':-10}],scope=[{'门店':'隔离测试门店','store_id':1,'month_id':1,'计算人数':1,'预计全店实发':5390}])
            repo.return_value.apply.return_value=1
            budget.return_value.spent.return_value=0
            at=AppTest.from_file(str(ROOT/'app.py')).run()
            self.assertEqual(len(at.exception),0)
            self.assertEqual(at.radio[0].value,'AI 识别')
            at.text_area[0].set_value('TEST-001迟到10分钟')
            next(b for b in at.button if b.label=='整理记录').click().run()
            self.assertTrue(any('核对下面' in w.value for w in at.info))
            self.assertEqual(len(at.exception),0)
            self.assertFalse(next(b for b in at.button if b.label=='确认以上记录并计算工资').disabled)
            import pandas as pd
            at.session_state['month_buffers'] = {99: {'frame':pd.DataFrame({'迟到':[20]}), 'base':pd.DataFrame({'迟到':[0]})}}
            at.run()
            self.assertTrue(next(b for b in at.button if b.label=='确认以上记录并计算工资').disabled)
            repo.return_value.apply.assert_not_called()
            at.session_state['month_buffers'] = {}
            at.run()
            next(b for b in at.button if b.label=='确认以上记录并计算工资').click().run()
            repo.return_value.apply.assert_called_once()
            self.assertTrue(any('已保存 1' in s.value for s in at.success))
            next(b for b in at.button if b.label=='整理记录').click().run()
            at.session_state['ai_current_rows'][0]['数量'] = 15.0
            next(r for r in at.radio if r.label=='录入方式').set_value('手动填写').run()
            next(r for r in at.radio if r.label=='录入方式').set_value('AI 识别').run()
            self.assertEqual(at.text_area[0].value,'TEST-001迟到10分钟')
            self.assertEqual(at.session_state['ai_draft']['rows'][0]['数量'],15.0)
            at.text_area[0].set_value('修改后的原文').run()
            self.assertFalse(any(b.label=='确认以上记录并计算工资' for b in at.button))
            self.assertEqual(len(at.exception),0)


class IdentityLabelTests(unittest.TestCase):
    def test_exact_number_with_field_label_matches(self):
        item = dict(name='员工编号： AI-CHECK-abc123',kind='迟到',amount=10,unit='分钟',
                    mode='新增',source='员工编号 AI-CHECK-abc123，10月8日迟到10分钟',question='')
        people={'AI-CHECK-abc123':'虚构员工'};stores={'AI-CHECK-abc123':'虚构门店'}
        self.assertEqual(review_rows([item],people,stores)[0]['员工编号'],'AI-CHECK-abc123')
        item['source']='员工编号 AI-CHECK-abc1234，迟到10分钟'
        self.assertEqual(review_rows([item],people,stores)[0]['员工编号'],'')
        item['name']='员工编号： AI-CHECK-abc124'
        self.assertEqual(review_rows([item],people,stores)[0]['员工编号'],'')


class NaturalIdentityTests(unittest.TestCase):
    def item(self, name):
        return dict(name=name,kind='普通缺勤',amount=3,unit='天',mode='累计',
                    source=f'员工{name}本月缺勤3天',question='')

    def test_unique_short_name_fills_employee_with_visible_explanation(self):
        people={'D1':'示例小面包','D2':'杨锡宁'}
        row=review_rows([self.item('小面包')],people,{'D1':'一店','D2':'二店'})[0]
        self.assertEqual(row['员工编号'],'D1')
        self.assertEqual(row['数量'],3)
        self.assertIn('简称',row['待核对问题'])
        self.assertIn('示例小面包',row['待核对问题'])

    def test_shared_short_names_and_typos_offer_candidates_without_guessing(self):
        people={'D1':'示例小面包','D2':'小面包同学','D3':'杨新宁','D4':'杨西宁'}
        stores={c:'一店' for c in people}
        for name, candidates in [('小面包',['示例小面包','小面包同学']),('杨欣宁',['杨新宁','杨西宁'])]:
            row=review_rows([self.item(name)],people,stores)[0]
            self.assertEqual(row['员工编号'],'')
            for candidate in candidates:
                self.assertIn(candidate,row['待核对问题'])

    def test_exact_name_wins_over_partial_and_single_char_does_not_match(self):
        people={'D1':'小面包','D2':'示例小面包'};stores={c:'一店' for c in people}
        self.assertEqual(review_rows([self.item('小面包')],people,stores)[0]['员工编号'],'D1')
        self.assertEqual(review_rows([self.item('包')],people,stores)[0]['员工编号'],'')

    def test_nickname_ui_uses_readable_employee_labels_and_submits_ids(self):
        employee=dict(id=1,employee_code='D1',name='示例小面包',store_id=1,store_name='一店')
        item=self.item('小面包')
        with patch.dict('os.environ', {'APP_MODE':'local','RENDER':'','DATABASE_URL':'test-placeholder'}), \
             patch('ui.common.db',side_effect=lambda method,*args,**kw: [{'id':1,'name':'一店'}] if method=='stores' else []), \
             patch('psycopg.connect'), patch('ui.ai_entry.AIWorkspace') as repo, \
             patch('ui.ai_entry.CloudBudget') as budget, \
             patch('ai_entry.service.Settings.from_env',return_value=Settings(key='test',provider='qwen')), \
             patch('ui.ai_entry.extract',return_value=[item]):
            repo.return_value.catalog.return_value=[employee, dict(employee, employee_code='D2', name='杨锡宁', store_name='二店')]
            repo.return_value.prepare.return_value=dict(token='preview-token',changes=[],impacts=[{'员工':'示例小面包','实发变化':-540}],scope=[{'门店':'一店','store_id':1,'month_id':1,'计算人数':1,'预计全店实发':4860}])
            budget.return_value.spent.return_value=0
            at=AppTest.from_file(str(ROOT/'app.py')).run()
            at.text_area[0].set_value(item['source'])
            next(b for b in at.button if b.label=='整理记录').click().run()
            self.assertFalse(at.exception)
            editor=next(d for d in at.dataframe if '员工' in d.value.columns)
            self.assertIn('示例小面包 · 一店',editor.value.iloc[0]['员工'])
            rows=repo.return_value.prepare.call_args.args[1]
            self.assertEqual(rows[0]['员工编号'],'D1')
            self.assertEqual(rows[0]['所属门店'],'一店')
            self.assertFalse(at.exception)
            at.session_state['ai_editor_1'] = {'edited_rows':{0:{'员工':'杨锡宁 · 二店 · D2'}}, 'added_rows':[], 'deleted_rows':[]}
            at.run()
            self.assertTrue(any(b.label=='确认以上记录并计算工资' for b in at.button))
            # AppTest has no editable-dataframe client; supply its widget delta for this rerun too.
            at.session_state['ai_editor_1'] = {'edited_rows':{0:{'员工':'杨锡宁 · 二店 · D2'}}, 'added_rows':[], 'deleted_rows':[]}
            changed=repo.return_value.prepare.call_args.args[1][0]
            self.assertEqual(changed['员工编号'],'D2')
            self.assertEqual(changed['所属门店'],'二店')


class WriteCallbackTests(unittest.TestCase):
    def test_pending_widget_edits_cannot_commit_an_old_preview(self):
        from datetime import date
        from ui.ai_entry import write_review
        preview = dict(token='token', context=('2026-10', 'AI 识别', '原文'),
                       editor_key='editor', editor_state={'edited_rows': {}}, month_ids=[1])
        for change in ({'ai_text': '新原文'}, {'monthly_month': date(2026, 11, 1)},
                       {'editor': {'edited_rows': {0: {'数量': 99}}}}, {'ai_receipt': {'count':1}}):
            with self.subTest(change=change):
                state = dict(ai_text='原文', monthly_month=date(2026, 10, 1),
                             editor={'edited_rows': {}}, ai_write_checked_token=True)
                state.update(change)
                # Streamlit's SessionState supports attribute as well as dictionary access.
                class State(dict):
                    __getattr__ = dict.__getitem__
                    __setattr__ = dict.__setitem__
                state = State(state)
                with patch('ui.ai_entry.st.session_state', state), patch('ui.ai_entry.call') as call, \
                     patch('ui.ai_entry.st.rerun'):
                    write_review('2026-10', [record()], preview)
                    call.assert_not_called()
                    self.assertIn('ai_write_error', state)
