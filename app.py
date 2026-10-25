"""面包店工资工作区。Excel 试算保留代码，暂不展示导航入口。"""
from pathlib import Path
import streamlit as st
from core.workflow import initialize
from ui.access import require_test_access
import os

st.set_page_config(page_title="Union Bakery · 工资工作台", page_icon="🍞", layout="wide")
st.markdown('<style>' + (Path(__file__).parent / 'ui/style.css').read_text() + '</style>', unsafe_allow_html=True)
require_test_access()
initialize(st.session_state)
pages = [
    st.Page('views/monthly_page.py', title='月度工资', icon=':material/payments:', default=True),
    st.Page('views/employees_page.py', title='员工档案', icon=':material/group:'),
    st.Page('views/stores_page.py', title='门店设置', icon=':material/storefront:'),
    st.Page('views/history_page.py', title='核算历史', icon=':material/history:'),
    st.Page('views/ai_entry_page.py', title='AI 辅助录入', icon=':material/edit_note:'),
    st.Page('views/import_page.py', title='Excel 导入', icon=':material/upload_file:'),
    st.Page('views/editor_page.py', title='表格试算', icon=':material/calculate:'),
    st.Page('views/export_page.py', title='试算导出', icon=':material/download:'),
]
pg = st.navigation(pages, position='hidden')
with st.sidebar:
    st.markdown('<div class="brand"><span class="brand-name">Union</span>'
                '<small>BAKERY</small><span class="brand-label">工资小管家</span></div>', unsafe_allow_html=True)
    st.caption('日常工作')
    for page in pages[:4]:
        st.page_link(page)
    st.divider()
    st.caption('在线测试 · 请勿录入真实工资' if os.environ.get('APP_MODE') == 'online-test' else '本地工作空间 · 开发版')
    st.caption('月度工资保存后可再次打开。尚未保存的表格修改仅留在当前会话。')
pg.run()
