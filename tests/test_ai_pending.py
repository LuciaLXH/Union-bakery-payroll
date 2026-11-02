from datetime import date
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

from ui.ai_entry import require_written_ai, discard_review
from ui.monthly_payroll import save_month, transition


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class PendingAIWorkflowTests(TestCase):
    def setUp(self):
        frame = pd.DataFrame({'迟到':[0]})
        self.state = State(monthly_month=date(2026,10,1), ai_text='待写入原文', ai_generation=1,
            ai_draft={'context':('2026-10','AI 识别','待写入原文'), 'rows':[{'事项':'迟到','数量':15}]},
            month_buffers={1:dict(frame=frame,base=frame.copy(),versions={1:1},lifecycle_version=1)},
            checked=True)
        self.patch = patch('streamlit.session_state', self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_calculate_confirm_and_paid_callbacks_reject_unwritten_ai(self):
        # The payroll fragment can predate the AI recognition; guard the callback itself.
        with patch('ui.monthly_payroll.common.db') as db:
            save_month(1,True)
            self.assertIn('未确认的记录',str(self.state.monthly_error))
            for action in ('confirm','paid'):
                transition(1,action,1,[1],None,'checked')
                self.assertIn('未确认的记录',str(self.state.monthly_error))
            db.assert_not_called()
            # Saving manual work is allowed so it can be resolved before AI writes.
            save_month(1,False)
            db.assert_called_once()

    def test_guard_is_month_scoped_and_allows_applied_or_empty_drafts(self):
        require_written_ai(date(2026,11,1))
        self.state.ai_receipt={'token':'written','count':1}
        require_written_ai(date(2026,10,1))
        self.state.pop('ai_receipt')
        self.state.ai_draft['rows']=[]
        require_written_ai(date(2026,10,1))

    def test_explicit_discard_preserves_source_and_saved_buffers(self):
        self.state.ai_preview={'token':'old'}
        with patch('streamlit.rerun') as rerun:
            discard_review()
        rerun.assert_called_once()
        self.assertEqual(self.state.ai_text,'待写入原文')
        self.assertNotIn('ai_preview',self.state)
        self.assertNotIn('ai_draft',self.state)
        self.assertIn(1,self.state.month_buffers)
        with patch('ui.monthly_payroll.common.db') as db:
            save_month(1,True)
        self.assertTrue(db.call_args.kwargs['calculate'])
