"""Generate only the selected payroll export, at download time."""
from hashlib import sha256
from threading import Lock

import streamlit as st

from core.excel_handler import export_payroll_to_excel


class DeferredExcel:
    def __init__(self, frame):
        self.frame = frame.copy(deep=True)
        self.content = None
        self.lock = Lock()

    def __call__(self):
        # Streamlit calls this on a download thread: no session or UI access here.
        with self.lock:
            if self.content is None:
                self.content = export_payroll_to_excel(self.frame)
            return self.content


def download_data(frame, scope):
    signature = sha256(frame.to_json(date_format='iso', default_handler=str).encode()).hexdigest()
    key = (scope, signature)
    cached = st.session_state.get('_payroll_excel')
    if cached is None or cached[0] != key:
        cached = (key, DeferredExcel(frame))
        st.session_state['_payroll_excel'] = cached
    return cached[1]
