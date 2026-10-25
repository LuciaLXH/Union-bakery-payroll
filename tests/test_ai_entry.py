from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ai_entry.service import Budget, EntryError, Settings, extract, validate_items
from ai_entry.review import EXAMPLE_ITEMS, SAMPLE, confirm_rows, review_rows
from ai_entry.monthly import MonthlyStore


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'usage.sqlite3'
        self.budget = Budget(self.path)
        self.settings = Settings(key='test-placeholder')

    def response(self, settings, payload):
        body = json.loads(payload)
        self.assertEqual(body['model'], 'gpt-5.4-mini')
        self.assertFalse(body['store'])
        self.assertNotIn('test-placeholder', payload.decode())
        self.assertTrue(body['response_format']['json_schema']['strict'])
        return {'usage': {'prompt_tokens': 3000, 'completion_tokens': 1000},
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': EXAMPLE_ITEMS})}}]}

    def run_extract(self, transport=None):
        return extract(SAMPLE, '虚构门店 A', '2026-10', self.settings, self.budget, transport or self.response)

    def test_structured_result_and_usage_survive_reopen(self):
        self.assertEqual(self.run_extract(), EXAMPLE_ITEMS)
        self.assertEqual(Budget(self.path).spent(), 54000)  # USD .00675 * assumed 8 CNY/USD.

    def test_budget_exhaustion_does_not_call_provider(self):
        self.settings = replace(self.settings, budget_cny=Decimal('.001'))
        with self.assertRaisesRegex(EntryError, '预算'):
            self.run_extract(lambda *args: self.fail('Must not call API'))
        self.assertEqual(self.budget.spent(), 0)

    def test_reservations_are_shared_between_instances(self):
        settings = replace(self.settings, budget_cny=Decimal('1'))
        self.budget.reserve(600000, settings)
        with self.assertRaises(EntryError):
            Budget(self.path).reserve(600000, settings)
        self.assertEqual(self.budget.spent(), 600000)

    def test_timeout_keeps_reservation_and_redacts_provider_exception(self):
        def fail(*args):
            raise RuntimeError('test-placeholder secret error')
        with self.assertRaises(EntryError) as error:
            self.run_extract(fail)
        self.assertNotIn('test-placeholder', str(error.exception))
        self.assertGreater(self.budget.spent(), 0)

    def test_incomplete_or_refused_output_not_accepted_but_charged(self):
        for finish, refusal in [('length', None), ('stop', 'refused')]:
            def response(*args):
                data = self.response(*args)
                data['choices'][0]['finish_reason'] = finish
                data['choices'][0]['message']['refusal'] = refusal
                return data
            with self.assertRaises(EntryError):
                self.run_extract(response)
        self.assertEqual(self.budget.spent(), 108000)

    def test_missing_key_and_empty_text_do_not_spend(self):
        with self.assertRaises(EntryError):
            extract(SAMPLE, 'A', '2026-10', replace(self.settings, key=''), self.budget)
        with self.assertRaises(EntryError):
            extract('', 'A', '2026-10', self.settings, self.budget)
        self.assertEqual(self.budget.spent(), 0)

    def test_switching_model_requires_explicit_prices(self):
        with patch.dict(os.environ, {'AI_ENTRY_MODEL': 'another-model'}, clear=True):
            with self.assertRaises(EntryError):
                Settings.from_env()

    def test_qwen_adapter_and_cny_accounting(self):
        with patch.dict(os.environ, {'AI_ENTRY_PROVIDER': 'qwen', 'DASHSCOPE_API_KEY': 'qwen-test-key',
                                     'OPENAI_API_KEY': 'must-not-use'}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.key, 'qwen-test-key')
        self.assertEqual(settings.model, 'qwen3.7-flash')
        def response(config, payload):
            body = json.loads(payload)
            self.assertFalse(body['enable_thinking'])
            self.assertEqual(body['max_tokens'], 2000)
            self.assertNotIn('max_completion_tokens', body)
            self.assertNotIn('reasoning_effort', body)
            self.assertNotIn('store', body)
            self.assertTrue(body['response_format']['json_schema']['strict'])
            return {'usage': {'prompt_tokens': 3000, 'completion_tokens': 1000},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': EXAMPLE_ITEMS})}}]}
        self.assertEqual(extract(SAMPLE, '自动匹配', '2026-10', settings, self.budget, response), EXAMPLE_ITEMS)
        self.assertEqual(self.budget.spent(), 1400)

    def test_qwen_does_not_reuse_openai_key_or_exchange_rate(self):
        with patch.dict(os.environ, {'AI_ENTRY_PROVIDER': 'qwen', 'OPENAI_API_KEY': 'must-not-use'}, clear=True):
            self.assertFalse(Settings.from_env().key)
        with patch.dict(os.environ, {'AI_ENTRY_PROVIDER': 'qwen', 'AI_ENTRY_USD_CNY': '8'}, clear=True):
            with self.assertRaises(EntryError):
                Settings.from_env()

    def test_nonfinite_and_fabricated_source_are_rejected(self):
        for field, value in [('amount', float('nan')), ('amount', -1), ('source', '编造内容')]:
            items = deepcopy(EXAMPLE_ITEMS)
            items[0][field] = value
            with self.assertRaises(EntryError):
                validate_items({'items': items}, SAMPLE)

    def test_waste_is_never_converted_into_a_deduction(self):
        items = deepcopy(EXAMPLE_ITEMS)
        items[-1]['amount'] = 100
        validated = validate_items({'items': items}, SAMPLE)
        self.assertIsNone(validated[-1]['amount'])


class ReviewTests(unittest.TestCase):
    def test_duplicate_and_unknown_names_require_manual_matching(self):
        for name in ['张三', '杨欣宁']:
            item = dict(EXAMPLE_ITEMS[0], name=name)
            rows = review_rows([item])
            self.assertEqual(rows[0]['员工编号'], '')
            with self.assertRaises(EntryError):
                confirm_rows(rows, ('A', '2026-10'))

    def test_ambiguous_addition_and_fractional_days_block_confirmation(self):
        rows = review_rows(EXAMPLE_ITEMS)
        rows[0]['处理方式'] = '待确认'
        with self.assertRaises(EntryError):
            confirm_rows(rows, ('A', '2026-10'))
        rows[0]['处理方式'] = '累计'
        rows[1]['数量'] = .5
        with self.assertRaises(EntryError):
            confirm_rows(rows, ('A', '2026-10'))

    def test_duplicate_digest_is_order_independent_and_context_specific(self):
        rows = review_rows(EXAMPLE_ITEMS)
        _, first = confirm_rows(rows, ('A', '2026-10'))
        _, second = confirm_rows(list(reversed(rows)), ('A', '2026-10'))
        _, third = confirm_rows(rows, ('A', '2026-11'))
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        with self.assertRaises(EntryError):
            confirm_rows(rows + rows, ('A', '2026-10'))


class PrototypeUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'AI_ENTRY_LEDGER': str(Path(self.tmp.name) / 'usage.sqlite3')}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'ai_entry_app.py')).run()

    def button(self, label):
        return next(b for b in self.app.button if b.label == label)

    def test_sample_confirmation_duplicate_and_stale_preview(self):
        self.assertFalse(self.app.exception)
        self.button('载入固定示例').click().run()
        self.app.checkbox[0].check().run()
        self.button('确认并保留本次测试记录').click().run()
        self.assertEqual(len(self.app.session_state['confirmed_batches']), 1)
        self.button('写入当月工资记录（测试）').click().run()
        self.assertFalse(self.app.exception)
        repo = MonthlyStore(Path(self.tmp.name) / 'test-monthly.sqlite3')
        self.assertEqual(repo.read('2026-10')[1]['TEST-002']['迟到分钟'], 10)
        self.button('确认并保留本次测试记录').click().run()
        self.assertIn('已经确认', self.app.error[0].value)
        self.app.text_area[0].set_value('不同文字').run()
        self.assertTrue(self.button('确认并保留本次测试记录').disabled)
        self.assertFalse(self.app.exception)

    def test_no_key_disables_ai_but_manual_entry_is_available(self):
        self.assertEqual(self.app.radio[0].value, 'AI 识别')
        self.assertEqual(len(self.app.selectbox), 0)
        self.assertTrue(self.button('识别文字').disabled)
        self.app.radio[0].set_value('手动录入').run()
        self.button('建立空白确认表').click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state['review_draft']['rows'], [])


class MonthlyWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'monthly.sqlite3'
        self.repo = MonthlyStore(self.path)
        self.rows = review_rows(EXAMPLE_ITEMS)

    def test_routes_stores_and_persists_without_other_month_changes(self):
        self.repo.write('2026-10', self.rows, 0)
        rev, data = MonthlyStore(self.path).read('2026-10')
        self.assertEqual(rev, 1)
        self.assertEqual(data['TEST-002']['门店'], '虚构门店 A')
        self.assertEqual(data['TEST-005']['门店'], '虚构门店 B')
        self.assertEqual(data['TEST-002']['迟到分钟'], 10)
        self.assertIn('乳酪', data['TEST-003']['备注'])
        self.assertEqual(self.repo.read('2026-11'), (0, {}))

    def test_add_total_duplicate_and_concurrency(self):
        self.repo.write('2026-10', self.rows, 0)
        extra = [dict(self.rows[0], 数量=5, 处理方式='新增', **{'原文 / 备注': '李四今天又迟到5分钟'})]
        self.repo.write('2026-10', extra, 1)
        self.assertEqual(self.repo.read('2026-10')[1]['TEST-002']['迟到分钟'], 15)
        with self.assertRaisesRegex(EntryError, '已经写入'):
            self.repo.write('2026-10', extra, 2)
        with self.assertRaisesRegex(EntryError, '已更新'):
            self.repo.write('2026-10', self.rows, 0)
        total = [dict(extra[0], 数量=12, 处理方式='累计', **{'原文 / 备注': '李四更正本月共12分钟'})]
        self.repo.write('2026-10', total, 2)
        self.assertEqual(self.repo.read('2026-10')[1]['TEST-002']['迟到分钟'], 12)

    def test_unsupported_reward_and_absence_overflow_roll_back_whole_batch(self):
        reward = dict(self.rows[0], 事项='奖励', 数量=50, 单位='元')
        with self.assertRaises(EntryError):
            self.repo.write('2026-10', self.rows + [reward], 0)
        self.assertEqual(self.repo.read('2026-10'), (0, {}))
        self.repo.write('2026-10', [dict(self.rows[1], 数量=30)], 0)
        bad = dict(self.rows[1], 事项='旷工', 数量=1)
        with self.assertRaises(EntryError):
            self.repo.write('2026-10', [self.rows[0], bad], 1)
        self.assertNotIn('TEST-002', self.repo.read('2026-10')[1])

    def test_employee_code_matching(self):
        rows = review_rows([dict(EXAMPLE_ITEMS[0], name='TEST-005')])
        self.assertEqual(rows[0]['员工编号'], 'TEST-005')
        self.assertEqual(rows[0]['所属门店'], '虚构门店 B')


if __name__ == '__main__':
    unittest.main()
