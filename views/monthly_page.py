"""按员工填写或批量编辑，共用一份月度草稿与保存入口。"""
import streamlit as st

from ui.monthly_payroll import render_payroll
from ui.ai_entry import render as render_ai_entry, preserve_draft
from ui.common import header, fetch, month_options, flash

header('UNION BAKERY / MONTHLY PAYROLL', '月度工资', '只录入本月发生的变化。固定工资自动带入，核对后统一计算。')
st.markdown('<div class="flow-steps"><span><b>01</b> 选择月份</span><span><b>02</b> 录入本月变化</span><span><b>03</b> 核对并计算</span></div>', unsafe_allow_html=True)
flash()
stores = fetch('stores')
if not stores:
    st.info('先建立门店并添加员工，再开始每月核算。')
    st.page_link('views/stores_page.py', label='建立门店')
    st.stop()
options, current = month_options(fetch('months'))
month = st.selectbox('工资月份', options, index=options.index(current),
    format_func=lambda value: value.strftime('%Y 年 %m 月'), key='monthly_month')
entry_mode = st.radio('录入方式', ['AI 识别', '手动填写'], horizontal=True, key='monthly_entry_mode', on_change=preserve_draft)
if entry_mode == 'AI 识别':
    render_ai_entry(month.strftime('%Y-%m'))
render_payroll(month, stores, entry_mode)
