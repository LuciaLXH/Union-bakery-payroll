"""Password-protected AI entry, using the same employee and payroll database."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os

import pandas as pd
import psycopg
import streamlit as st

from ai_entry.cloud import AIWorkspace, CloudBudget
from ai_entry.review import COLUMNS, KINDS, MODES, review_rows
from ai_entry.service import EntryError, Settings, extract
from ui import common


def call(method, *args):
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=15) as connection:
        connection.execute('SET LOCAL lock_timeout=5000; SET LOCAL statement_timeout=30000')
        return getattr(AIWorkspace(connection, schema=common.DB_SCHEMA), method)(*args)


common.header('MONTHLY NOTES', 'AI 辅助录入', '粘贴记录 → 核对员工与事项 → 预览变更 → 写入当月工资')
st.info('测试版：支持考勤、加班、通勤补贴及备注。报废扣款暂只保存备注，个人工资条尚未上线。')
st.caption('请先在“月度工资”建立本月工资；写入后回到该页检查并计算。姓名或编号自动对应门店，无需选择门店。')
st.page_link('views/monthly_page.py', label='打开月度工资')
try:
    catalog = call('catalog')
    settings = Settings.from_env()
    budget = CloudBudget(schema=common.DB_SCHEMA)
    spent = budget.spent()
except (ValueError, psycopg.Error, KeyError) as exc:
    common.show_error(exc)
    st.stop()
people = {e['employee_code']: e['name'] for e in catalog}
stores = {e['employee_code']: e['store_name'] for e in catalog}
if not people:
    st.info('请先在员工档案添加员工。')
    st.stop()
with st.expander('查看员工姓名、编号及所属门店'):
    st.dataframe(pd.DataFrame([{'编号': c, '姓名': n, '门店': stores[c]} for c,n in people.items()]), hide_index=True)
month = st.date_input('工资月份（按所选日期所在月份）', datetime.now(ZoneInfo('Asia/Shanghai')).date().replace(day=1), key='ai_month').strftime('%Y-%m')
mode = st.radio('录入方式', ['AI 识别', '手动录入'], horizontal=True, key='ai_mode')
text = st.text_area('粘贴本月记录', max_chars=3000, height=140, key='ai_text',
                    placeholder='例如：员工编号 E001，10月8日迟到10分钟，新增一次；10月15日迟到5分钟，新增一次。')
st.caption(f'全站本月 AI 预算 ¥{settings.budget_cny}；已用及预留约 ¥{spent/1000000:.4f}。超出预算可手动录入。费用按配置单价估算，以百炼账单为准。')
if mode == 'AI 识别':
    st.caption('识别会将上方文字与月份发送给千问。请使用测试数据；请勿粘贴身份证、银行账户等无关信息。')
    if not settings.key:
        st.warning('AI 密钥尚未配置，可先使用手动录入。')
context = (month, mode, text)
if st.button('识别文字' if mode == 'AI 识别' else '建立手动录入表', type='primary', disabled=mode == 'AI 识别' and not settings.key):
    st.session_state.pop('ai_draft', None)
    st.session_state.pop('ai_preview', None)
    st.session_state['ai_generation'] = st.session_state.get('ai_generation', 0) + 1
    try:
        with st.spinner('正在整理记录…'):
            rows = review_rows(extract(text, '按员工档案自动匹配', month, settings, budget), people, stores) if mode == 'AI 识别' else []
        st.session_state.ai_draft = dict(context=context, rows=rows)
    except (EntryError, psycopg.Error) as exc:
        common.show_error(exc)
draft = st.session_state.get('ai_draft')
if not draft:
    st.stop()
if draft['context'] != context:
    st.warning('月份、原文或录入方式已变化，请重新生成整理结果。')
    st.stop()
st.subheader('核对整理结果')
st.caption('“新增”加到已有记录；“累计”替换本月总数。日期与时间保留在原文中。请核对所有待确认问题，必要时修改事项或原文。')
frame = pd.DataFrame(draft['rows'], columns=COLUMNS)
frame['数量'] = pd.to_numeric(frame['数量'], errors='coerce').astype(float)
rows = st.data_editor(frame, key=f"ai_editor_{st.session_state.ai_generation}", num_rows='dynamic',
    hide_index=True, width='stretch', disabled=['所属门店'], column_config={
        '员工编号': st.column_config.SelectboxColumn(options=list(people), required=True,
            format_func=lambda c: f'{people[c]} · {c} · {stores[c]}' if c in people else c),
        '事项': st.column_config.SelectboxColumn(options=KINDS, required=True),
        '数量': st.column_config.NumberColumn(min_value=0, max_value=1000000),
        '单位': st.column_config.SelectboxColumn(options=['分钟','天','小时','元','']),
        '处理方式': st.column_config.SelectboxColumn(options=MODES, required=True),
        '原文 / 备注': st.column_config.TextColumn(width='large'),
    }).to_dict('records')
if st.button('预览写入变更'):
    st.session_state.pop('ai_preview', None)
    try:
        preview = call('prepare', month, rows)
        st.session_state.ai_preview = dict(token=preview['token'], rows=rows, context=context, changes=preview['changes'])
    except (ValueError, psycopg.Error) as exc:
        common.show_error(exc)
preview = st.session_state.get('ai_preview')
# Data-editor NaN values are normalized to None for stable draft comparisons.
def canonical(value):
    return pd.DataFrame(value).fillna('').to_json(force_ascii=False)
if preview and preview['context']==context and canonical(preview['rows'])==canonical(rows):
    st.subheader('即将写入的变更')
    st.dataframe(pd.DataFrame(preview['changes']), hide_index=True, width='stretch')
    checked = st.checkbox('我已核对所属门店、员工、月份、数量及新增/累计方式', key='ai_write_checked_'+preview['token'])
    if st.button('写入当月工资记录', type='primary', disabled=not checked):
        try:
            count = call('apply', month, rows, preview['token'])
            st.session_state.pop('ai_preview', None)
            st.session_state.pop('ai_draft', None)
            st.success(f'已写入 {count} 条事项及原文。请到“月度工资”检查并计算；本操作没有发薪。')
            st.page_link('views/monthly_page.py', label='检查并计算当月工资')
        except (ValueError, psycopg.Error) as exc:
            common.show_error(exc)
elif preview:
    st.warning('表格已修改，请重新预览写入变更。')
