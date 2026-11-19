"""Original source references: shared dates, abbreviated hours, fail-closed validation."""
from copy import deepcopy
from decimal import Decimal
import json
from unittest import TestCase

from ai_entry.service import EntryError, resolve_sources, source_fragments
from ai_entry.review import review_rows
from ai_entry.cloud import AIWorkspace
from test_database_schema import DatabaseFixture

TEXT = '10.7乙同事病假1天，10.8甲同事迟到15分钟，10.11甲同事加班1h，10.20乙同事加班2.5h，10.23乙同事加班1h、甲同事加班2h，10.24甲同事请假一天'


def extracted_items():
    return [dict(name=name,kind=kind,amount=amount,unit=unit,mode='新增',source_id=source,question='')
            for name,kind,amount,unit,source in [
                ('乙同事','普通缺勤',1,'天','1'), ('甲同事','迟到',15,'分钟','2'),
                ('甲同事','加班',1,'小时','3'), ('乙同事','加班',2.5,'小时','4'),
                ('乙同事','加班',1,'小时','5'), ('甲同事','加班',2,'小时','5'),
                ('甲同事','普通缺勤',1,'天','6')]]


class SourceReferenceTests(TestCase):
    def test_shared_date_and_decimal_hours_keep_exact_original(self):
        sources = source_fragments(TEXT)
        self.assertEqual(len(sources), 6)
        self.assertEqual(sources['4'], '10.20乙同事加班2.5h')
        self.assertEqual(sources['5'], '10.23乙同事加班1h、甲同事加班2h')
        raw = {'items': extracted_items()}
        original = deepcopy(raw)
        items = resolve_sources(raw, sources, TEXT)
        self.assertEqual(raw, original)
        self.assertEqual(len(items), 7)
        self.assertEqual(items[4]['source'], items[5]['source'])
        self.assertTrue(all(item['source'] in TEXT for item in items))
        self.assertEqual(items[3]['amount'], 2.5)
        self.assertNotIn('source_id', items[0])

    def test_unknown_reference_and_rewritten_source_cannot_pass(self):
        for source_id in ('missing', '', 1, None, True, ['1']):
            raw = {'items':[dict(extracted_items()[0], source_id=source_id)]}
            with self.subTest(source=source_id), self.assertRaises(EntryError):
                resolve_sources(raw, source_fragments(TEXT), TEXT)
        raw = {'items':[dict(extracted_items()[0], source='自行补写原文')]}
        with self.assertRaises(EntryError):
            resolve_sources(raw, source_fragments(TEXT), TEXT)
        with self.assertRaises(EntryError):
            resolve_sources({'items':extracted_items()}, {'1':'编造文字'}, TEXT)

    def test_existing_item_checks_and_limit_remain_enforced(self):
        for field, value in [('amount', float('nan')), ('amount', True), ('kind','invalid'), ('mode','invalid')]:
            raw = {'items':[dict(extracted_items()[0], **{field:value})]}
            with self.subTest(field=field), self.assertRaises(EntryError):
                resolve_sources(raw, source_fragments(TEXT), TEXT)
        with self.assertRaises(EntryError):
            resolve_sources({'items':[extracted_items()[0]]*51}, source_fragments(TEXT), TEXT)
        self.assertEqual(resolve_sources({'items':[]}, source_fragments(TEXT), TEXT), [])

    def test_spacing_and_separators_preserve_literal_slices(self):
        text = ' 10.7小甲加班2.5h；\n10.8小乙迟到15分钟,10.9小甲请假一天。 '
        self.assertEqual(list(source_fragments(text).values()),
                         ['10.7小甲加班2.5h','10.8小乙迟到15分钟','10.9小甲请假一天'])
        self.assertEqual(source_fragments(' ，。；\n '), {})


class SourcePayrollTests(DatabaseFixture):
    def test_seven_items_preview_and_save_without_merging_shared_date_people(self):
        from datetime import date
        self.repo = AIWorkspace(self.connection, schema=self.schema)
        store = self.repo.stores()[0]
        employee = self.repo.employees(store['id'])[0]
        self.repo.save_employee(store['id'], dict(employee, name='甲同事'), employee_id=employee['id'], expected=employee)
        self.repo.save_employee(store['id'], dict(employee, employee_code='TEST-002', name='乙同事'))
        self.repo.start_month(store['id'],date(2026,10,1))
        self.connection.execute('UPDATE payroll_entries SET ordinary_absence_days=0,late_minutes=0,overtime_hours=0')
        items = resolve_sources({'items':extracted_items()}, source_fragments(TEXT), TEXT)
        rows = review_rows(items, {'TEST-001':'甲同事','TEST-002':'乙同事'},
                           {'TEST-001':store['name'],'TEST-002':store['name']})
        preview = self.repo.prepare('2026-10', rows)
        self.assertEqual([row['本次确认后预计实发'] for row in preview['impacts']], [Decimal('5280'),Decimal('5307.50')])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ai_entry_events').fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT sum(overtime_hours) FROM payroll_entries').fetchone()[0],0)
        self.repo.apply('2026-10',rows,preview['token'],calculate=True)
        entries = self.repo.entries(self.repo.months()[0]['id'])
        self.assertEqual([(e['ordinary_absence_days'], e['late_minutes'], e['overtime_hours']) for e in entries],
                         [(1,15,Decimal('3')), (1,0,Decimal('3.5'))])
        self.assertTrue(all('10.23' in e['work_error_notes'] for e in entries))
        with self.assertRaisesRegex(ValueError,'相同事项'):
            self.repo.prepare('2026-10',rows)
