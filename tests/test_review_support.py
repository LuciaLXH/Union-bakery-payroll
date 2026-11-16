from decimal import Decimal
from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd

from ui.read_cache import read, clear_reads
from ui.payroll_download import download_data


class DisplayCacheTests(TestCase):
    def setUp(self):
        self.state = {}
        self.patch = patch('streamlit.session_state', self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_display_cache_expires_invalidates_and_isolates_environment(self):
        loader = Mock(return_value=[{'name':'test'}])
        with patch('ui.access.check_access'), patch('ui.read_cache.monotonic', return_value=10):
            result = read('catalog', loader, schema='test_a')
            result[0]['name'] = 'local change'
            self.assertEqual(read('catalog', loader, schema='test_a')[0]['name'], 'test')
            loader.assert_called_once()
            read('catalog', loader, schema='test_b')
            self.assertEqual(loader.call_count, 2)
            clear_reads()
            read('catalog', loader, schema='test_b')
            self.assertEqual(loader.call_count, 3)
        with patch('ui.access.check_access'), patch('ui.read_cache.monotonic', return_value=41):
            read('catalog', loader, schema='test_b')
            self.assertEqual(loader.call_count, 4)
        with patch('ui.access.check_access', side_effect=ValueError('登录已失效')):
            with self.assertRaisesRegex(ValueError, '登录已失效'):
                read('catalog', loader, schema='test_b')

    def test_excel_is_deferred_reused_and_replaced_after_change(self):
        frame = pd.DataFrame({'姓名':['测试'], '合计':[Decimal('2963.90')]})
        with patch('ui.payroll_download.export_payroll_to_excel', return_value=b'file') as export:
            first = download_data(frame, ('test', 1))
            export.assert_not_called()
            self.assertEqual(first(), b'file')
            self.assertIs(download_data(frame, ('test', 1)), first)
            first()
            export.assert_called_once()
            frame.at[0, '合计'] = Decimal('2663.90')
            second = download_data(frame, ('test', 1))
            self.assertIsNot(second, first)
            second()
            self.assertEqual(export.call_count, 2)
            self.assertEqual(first.frame.at[0, '合计'], Decimal('2963.90'))
            self.assertIsNot(download_data(frame, ('other_environment', 1)), second)
