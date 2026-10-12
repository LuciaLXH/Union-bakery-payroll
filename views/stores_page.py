import pandas as pd
import psycopg
import streamlit as st
from ui.common import header, fetch, db, flash, show_error

header('UNION BAKERY / STORES', '门店设置', '先建立门店，再添加员工。每个月的工资上限，在“月度工资”页面单独设置。')
flash()
stores = fetch('stores')
if stores:
    st.subheader('已有门店')
    st.dataframe(pd.DataFrame([{'门店名称': s['name']} for s in stores]), hide_index=True, width='stretch')
with st.container(border=True, key='store_form'):
    st.subheader('新增一家门店')
    with st.form('new_store', clear_on_submit=True):
        name = st.text_input('门店名称', placeholder='例如：Union Bakery 一号店')
        if st.form_submit_button('保存新门店', type='primary'):
            try:
                db('add_store', name)
                st.session_state.workspace_flash = '新门店已保存。下一步到“员工档案”添加员工。'
                st.rerun()
            except (ValueError, psycopg.Error) as exc:
                show_error(exc)
st.page_link('views/employees_page.py', label='下一步：添加员工', icon=':material/group_add:')
