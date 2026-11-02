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
    if not st.session_state.get('ai_receipt'):
        st.session_state.pop('ai_preview', None)


def editor_changes(value):
    value = value or {}
    return {key: value.get(key, default) for key, default in
            [('edited_rows', {}), ('added_rows', []), ('deleted_rows', [])]}


def require_written_ai(month):
    """Callbacks must check current draft state even when their fragment is stale."""
    draft = st.session_state.get('ai_draft')
    month = month.strftime('%Y-%m') if hasattr(month, 'strftime') else month
    if (draft and draft['context'][0] == month and draft['rows']
            and not st.session_state.get('ai_receipt')):
        raise ValueError('上方还有未确认的记录，尚未计入工资。请先核对并点击“确认以上记录并计算工资”；'
                         '不采用时可点击“取消本批记录”。本次没有计算或确认工资。')


def discard_review():
    for key in ('ai_draft', 'ai_current_rows', 'ai_preview', 'ai_receipt', 'ai_write_error'):
        st.session_state.pop(key, None)
    st.session_state['ai_generation'] = st.session_state.get('ai_generation', 0) + 1
    # Preserve the source text for manual entry; refresh both fragments.
    st.rerun()


def write_review(month, rows, preview, calculate=True):
    """Commit before rendering; refresh the catalog and payroll after a write."""
    try:
        # Widgets may submit edits together with a button click. Never commit
        # the previous render's preview if the source/editor changed meanwhile.
        if st.session_state.get('ai_text') != preview['context'][2]:
            raise ValueError('原文已修改，请重新点击“整理记录”后核对。')
        selected_month = st.session_state.get('monthly_month')
        if selected_month is not None and selected_month.strftime('%Y-%m') != month:
            raise ValueError('月份已修改，请重新整理记录并核对。')
        if editor_changes(st.session_state.get(preview['editor_key'])) != editor_changes(preview['editor_state']):
            raise ValueError('表格刚刚修改，请先核对更新后的工资影响，再点击确认。')
        if st.session_state.get('ai_receipt'):
            raise ValueError('本批记录已经保存，请勿重复确认。')
        # Check again in the callback, in addition to the disabled button.
        if any(not b['frame'].equals(b['base']) for b in st.session_state.get('month_buffers', {}).values()):
            raise ValueError('还有未保存的手动修改，请先保存或放弃修改。')
        count = call('apply', month, rows, preview['token'], calculate)
        st.session_state.ai_receipt = dict(token=preview['token'], count=count,
            calculated=calculate, scope=preview.get('scope', []))
        st.session_state.ai_draft['rows'] = deepcopy(rows)
        st.session_state['ai_generation'] += 1
        for month_id in preview['month_ids']:
            st.session_state.get('month_buffers', {}).pop(month_id, None)
        affected_stores = [s['store_id'] for s in preview.get('scope', [])]
        if affected_stores and st.session_state.get('monthly_store') not in affected_stores:
            st.session_state['monthly_store'] = affected_stores[0]
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
        st.caption('粘贴记录，核对员工和工资变化，再一次确认完成计算。可包含不同门店；报废事项目前只保存备注，不扣款。')
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
    st.caption('默认追加本次记录。要替换本月总数，请明确写“本月累计”或“总共”。建议写上日期，同一事项只录一次；确认前可修改。')
    st.caption(f'全站本月 AI 预算 ¥{settings.budget_cny}；已用及预留约 ¥{spent/1000000:.4f}。超出预算可手动录入。费用按配置单价估算，以百炼账单为准。')
    if mode == 'AI 识别':
        st.caption('识别会将上方文字与月份发送给千问，请只填写本次考勤所需信息。'
                   if os.environ.get('APP_MODE') == 'production' else
                   '识别会将上方文字与月份发送给千问。请使用测试数据；请勿粘贴身份证、银行账户等无关信息。')
        if not settings.key:
            st.warning('AI 密钥尚未配置，可先使用手动录入。')
    context = (month, mode, text)
    if st.button('整理记录', type='primary', disabled=not settings.key):
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
    if draft['rows'] and not st.session_state.get('ai_receipt'):
        st.info('请核对下面的记录和预计工资。点击“确认以上记录并计算工资”后，才会保存这些记录并更新工资。')
        st.button('取消本批记录', on_click=discard_review,
                  help='仅清除本次识别草稿，保留原文；不会删除已保存的工资记录。')
    if draft['context'] != context:
        st.warning('月份、原文或录入方式已变化，请重新生成整理结果。')
        return
    receipt = st.session_state.get('ai_receipt')
    applied = bool(receipt)
    st.subheader('核对记录与工资变化')
    if any(row['事项'] == '备注' for row in draft['rows']):
        st.info('报损目前仅记录、不扣款。标为“备注”的事项不会改变工资；请假、迟到等考勤确认后会参与计算。')
    st.caption('员工、事项或数量不对时可直接修改。“新增”加到已有记录，“累计”替换本月总数。员工不明确时，在“员工”列按姓名选择。')
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
    error = st.session_state.pop('ai_write_error', None)
    if error:
        common.show_error(error)
    if not rows:
        st.info('没有可保存的事项，请补充原文后重新整理。')
        return
    st.button('更新核对结果', disabled=applied, help='已在其他页面修改档案或建立本月工资时，点击重新读取。')
    if applied:
        preview = st.session_state.get('ai_preview')
        if not preview:
            st.info('本批记录已经保存。请在下方查看工资。')
            return
    else:
        st.session_state.pop('ai_preview', None)
        try:
            prepared = call('prepare', month, rows)
            preview = dict(token=prepared['token'], rows=rows, context=context,
                changes=prepared['changes'], impacts=prepared['impacts'], scope=prepared['scope'],
                month_ids=[plan[0] for plan in prepared.get('plans', [])],
                editor_key=editor_key, editor_state=deepcopy(st.session_state.get(editor_key)))
            st.session_state.ai_preview = preview
        except (ValueError, psycopg.Error) as exc:
            common.show_error(exc)
            return
    # Describe each recognized item in business language before showing exact totals.
    rules = {'普通缺勤':'按缺勤天数扣除工资、绩效和全勤', '旷工':'每旷工1天扣3天工资，绩效和全勤各扣1天',
             '迟到':'每分钟扣1元', '加班':'按有效加班小时计算加班费',
             '通勤补贴':'按确认金额计入通勤补贴', '备注':'仅保留文字，本项不扣款'}
    explanations = [{'员工':people.get(r['员工编号'], ''), '事项':r['事项'],
        '本次记录':r['原文 / 备注'], '工资影响':rules.get(r['事项'], '需要核对')} for r in rows]
    st.dataframe(pd.DataFrame(explanations), hide_index=True, width='stretch')
    st.subheader('确认后预计工资')
    st.caption('原实发试算按当前已保存的考勤计算；变化金额比较本次确认前后，不是与上月工资比较。以下扣款列为确认后的月度合计。')
    if applied:
        st.caption('这里保留本批操作核对时的金额；最新工资以下方“本月计算结果”为准。')
    impact_frame = pd.DataFrame(preview['impacts'])
    st.dataframe(impact_frame, hide_index=True, width='stretch', column_config={
        c:st.column_config.NumberColumn(format='%.2f') for c in impact_frame.columns if c not in ('员工','门店')})
    scope = pd.DataFrame(preview['scope'])
    st.write(f'本次确认月份：{month}；计算范围为以下门店的全部当月员工。')
    st.dataframe(scope[['门店','计算人数','预计全店实发']], hide_index=True, width='stretch',
        column_config={'预计全店实发':st.column_config.NumberColumn(format='%.2f')})
    dirty = any(not b['frame'].equals(b['base']) for b in st.session_state.get('month_buffers', {}).values())
    if dirty:
        st.warning('还有未保存的手动修改，请先切换到手动填写并保存，或放弃手动修改，再确认本批记录。')
    st.button('确认以上记录并计算工资', type='primary', disabled=dirty or applied,
              on_click=write_review, args=(month, rows, preview, True))
    st.button('先保存，稍后计算', disabled=dirty or applied,
              on_click=write_review, args=(month, rows, preview, False))
    st.caption('点击主按钮会保存以上记录并计算工资；不会锁定工资、转账或发薪。')
    if applied:
        if receipt['calculated']:
            st.success(f"已保存 {receipt['count']} 条记录，并完成相关门店本月工资计算。下方可查看结果、下载明细，无需再点一次计算。")
        else:
            st.success(f"已保存 {receipt['count']} 条记录，尚未计算工资。准备好后，在下方选择门店并计算。")
        st.caption('本次操作没有锁定工资或发薪。录入下一批时，修改上方文字，再点击“整理记录”。')
