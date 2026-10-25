"""Password-protected AI entry, using the same employee and payroll database."""
import os
from copy import deepcopy

import pandas as pd
import psycopg
import streamlit as st

from ai_entry.cloud import AIWorkspace, CloudBudget
from ai_entry.review import COLUMNS, KINDS, MODES, review_rows
from ai_entry.service import EntryError, Settings, extract
from ui import common


def call(method, *args):
    from ui.access import check_access
    check_access()
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=15) as connection:
        connection.execute('SET LOCAL lock_timeout=5000; SET LOCAL statement_timeout=30000')
        common.bind_connection(connection, schema=common.DB_SCHEMA)
        return getattr(AIWorkspace(connection, schema=common.DB_SCHEMA), method)(*args)


def preserve_draft():
    """Retain reviewed edits when Streamlit removes the AI widgets in manual mode."""
    draft = st.session_state.get('ai_draft')
    if draft and 'ai_current_rows' in st.session_state:
        draft['rows'] = st.session_state.ai_current_rows
        st.session_state['ai_generation'] = st.session_state.get('ai_generation', 0) + 1
    st.session_state.pop('ai_preview', None)


def editor_changes(value):
    value = value or {}
    return {key: value.get(key, default) for key, default in
            [('edited_rows', {}), ('added_rows', []), ('deleted_rows', [])]}


def write_review(month, rows, preview):
    """Commit before rendering; refresh the catalog and payroll after a write."""
    try:
        # Widgets may submit edits together with a button click. Never commit
        # the previous render's preview if the source/editor changed meanwhile.
        if st.session_state.get('ai_text') != preview['context'][2]:
            raise ValueError('原文已修改，请重新识别并预览。')
        selected_month = st.session_state.get('monthly_month')
        if selected_month is not None and selected_month.strftime('%Y-%m') != month:
            raise ValueError('月份已修改，请重新识别并预览。')
        if editor_changes(st.session_state.get(preview['editor_key'])) != editor_changes(preview['editor_state']):
            raise ValueError('表格已修改，请重新预览写入变更。')
        if not st.session_state.get('ai_write_checked_' + preview['token']):
            raise ValueError('请先勾选确认，再写入。')
        # Check again in the callback, in addition to the disabled button.
        if any(not b['frame'].equals(b['base']) for b in st.session_state.get('month_buffers', {}).values()):
            raise ValueError('还有未保存的手动修改，请先保存或放弃修改。')
        count = call('apply', month, rows, preview['token'])
        st.session_state.ai_receipt = dict(token=preview['token'], count=count)
        st.session_state.ai_draft['rows'] = deepcopy(rows)
        st.session_state['ai_generation'] += 1
        for month_id in preview['month_ids']:
            st.session_state.get('month_buffers', {}).pop(month_id, None)
        st.session_state.pop('ai_write_error', None)
    except (ValueError, psycopg.Error) as exc:
        st.session_state.ai_write_error = exc
    # A write can affect several stores. One app rerun refreshes both regions,
    # with the reviewed rows retained so the page does not collapse.
    st.rerun()


@st.fragment(key='ai_entry')
def render(month):
    # The fragment owns its container; it never appends to an external container.
    with st.container(border=True, key='monthly_ai_entry'):
        st.subheader('录入本月变化')
        st.caption('可一次粘贴不同门店员工的记录，按姓名或编号自动匹配。下方可建立各门店本月工资；报废事项目前只保存备注。')
        render_editor(month)


def render_editor(month):
    """Render inline without stopping the enclosing monthly payroll page."""
    try:
        catalog = call('catalog')
        settings = Settings.from_env()
        budget = CloudBudget(schema=common.DB_SCHEMA)
        spent = budget.spent()
    except (ValueError, psycopg.Error, KeyError) as exc:
        common.show_error(exc)
        return
    people = {e['employee_code']: e['name'] for e in catalog}
    stores = {e['employee_code']: e['store_name'] for e in catalog}
    if not people:
        st.info('请先在员工档案添加员工。')
        return
    with st.expander('查看员工姓名、编号及所属门店'):
        st.dataframe(pd.DataFrame([{'编号': c, '姓名': n, '门店': stores[c]} for c,n in people.items()]), hide_index=True)
    mode = 'AI 识别'
    text = st.text_area('粘贴本月记录', max_chars=3000, height=140, key='ai_text', value=st.session_state.get('ai_saved_text', ''),
                        placeholder='例如：小面包10月8日请假1天；杨新宁10月8日迟到10分钟。要替换总数可写：小面包本月累计缺勤3天。')
    st.session_state.ai_saved_text = text
    st.caption('默认“新增”：本次数量加到已保存记录。要替换本月总数，请明确写“本月累计”或“总共”。建议写上日期，同一事项只录一次；写入前可修改。')
    st.caption(f'全站本月 AI 预算 ¥{settings.budget_cny}；已用及预留约 ¥{spent/1000000:.4f}。超出预算可手动录入。费用按配置单价估算，以百炼账单为准。')
    if mode == 'AI 识别':
        st.caption('识别会将上方文字与月份发送给千问，请只填写本次考勤所需信息。'
                   if os.environ.get('APP_MODE') == 'production' else
                   '识别会将上方文字与月份发送给千问。请使用测试数据；请勿粘贴身份证、银行账户等无关信息。')
        if not settings.key:
            st.warning('AI 密钥尚未配置，可先使用手动录入。')
    context = (month, mode, text)
    if st.button('识别文字', type='primary', disabled=not settings.key):
        st.session_state.pop('ai_receipt', None)
        st.session_state.pop('ai_write_error', None)
        st.session_state.pop('ai_draft', None)
        st.session_state.pop('ai_current_rows', None)
        st.session_state.pop('ai_preview', None)
        st.session_state['ai_generation'] = st.session_state.get('ai_generation', 0) + 1
        try:
            with st.spinner('正在整理记录…'):
                rows = review_rows(extract(text, '按员工档案自动匹配', month, settings, budget), people, stores)
            st.session_state.ai_draft = dict(context=context, rows=rows)
        except (EntryError, psycopg.Error) as exc:
            common.show_error(exc)
    draft = st.session_state.get('ai_draft')
    if not draft:
        return
    if draft['context'] != context:
        st.warning('月份、原文或录入方式已变化，请重新生成整理结果。')
        return
    receipt = st.session_state.get('ai_receipt')
    applied = bool(receipt)
    st.subheader('核对整理结果')
    st.caption('先看员工是否对应，再看事项和数量。“累计”替换本月总数，“新增”加到已有记录；写入前会显示修改前后的数字。员工不明确时，点击“员工”单元格按姓名选择，无需查编号。')
    frame = pd.DataFrame(draft['rows'], columns=COLUMNS)
    employee_labels = {c: f'{n} · {stores[c]} · {c}' for c,n in people.items()}
    label_codes = {label:c for c,label in employee_labels.items()}
    frame['员工编号'] = frame['员工编号'].map(employee_labels)
    frame = frame.rename(columns={'员工编号':'员工'})
    for i, row in enumerate(draft['rows'], 1):
        if row.get('待核对问题'):
            st.info(f"第 {i} 条（{row['原文姓名']}）：{row['待核对问题']}")
    frame['数量'] = pd.to_numeric(frame['数量'], errors='coerce').astype(float)
    editor_key = f"ai_editor_{st.session_state.ai_generation}"
    rows = st.data_editor(frame, key=editor_key, num_rows='dynamic',
        hide_index=True, width='stretch', disabled=True if applied else ['所属门店', '原文姓名', '待核对问题'], column_config={
            '员工': st.column_config.SelectboxColumn(options=list(employee_labels.values()), required=True, width='large'),
            '事项': st.column_config.SelectboxColumn(options=KINDS, required=True),
            '数量': st.column_config.NumberColumn(min_value=0, max_value=1000000),
            '单位': st.column_config.SelectboxColumn(options=['分钟','天','小时','元','']),
            '处理方式': st.column_config.SelectboxColumn(options=MODES, required=True, default='新增'),
            '原文 / 备注': st.column_config.TextColumn(width='large'),
        }).to_dict('records')
    for row in rows:
        row['员工编号'] = label_codes.get(row.pop('员工', None), '')
        row['所属门店'] = stores.get(row['员工编号'], '')
    st.session_state.ai_current_rows = rows
    if st.button('预览写入变更', disabled=applied):
        st.session_state.pop('ai_preview', None)
        try:
            preview = call('prepare', month, rows)
            st.session_state.ai_preview = dict(token=preview['token'], rows=rows, context=context,
                changes=preview['changes'], month_ids=[plan[0] for plan in preview.get('plans', [])],
                editor_key=editor_key, editor_state=deepcopy(st.session_state.get(editor_key)))
        except (ValueError, psycopg.Error) as exc:
            common.show_error(exc)
    preview = st.session_state.get('ai_preview')
    # Data-editor NaN values are normalized to None for stable draft comparisons.
    def canonical(value):
        return pd.DataFrame(value).fillna('').to_json(force_ascii=False)
    if preview and preview['context']==context and canonical(preview['rows'])==canonical(rows):
        st.subheader('即将写入的变更')
        st.dataframe(pd.DataFrame(preview['changes']), hide_index=True, width='stretch')
        checked = st.checkbox('我已核对所属门店、员工、月份、数量及新增/累计方式', key='ai_write_checked_'+preview['token'], disabled=applied)
        dirty = any(not b['frame'].equals(b['base']) for b in st.session_state.get('month_buffers', {}).values())
        if dirty:
            st.warning('还有未保存的手动修改，请先切换到手动填写并保存，或明确放弃修改，再写入 AI 结果。')
        st.button('写入当月工资记录', type='primary', disabled=not checked or dirty or applied,
                  on_click=write_review, args=(month, rows, preview))
        if applied:
            st.success(f"已写入 {receipt['count']} 条事项及原文。下方选择门店，检查并计算即可；本操作没有发薪。")
            st.caption('本批次已完成，不会再次写入。录入下一批时，修改上方文字，再点击“识别文字”。')
        error = st.session_state.pop('ai_write_error', None)
        if error:
            common.show_error(error)
    elif preview:
        st.warning('表格已修改，请重新预览写入变更。')
