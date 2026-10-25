"""Independently refresh payroll review without rebuilding the AI editor above it."""
from decimal import Decimal
from uuid import uuid4
import pandas as pd
import psycopg
import streamlit as st

from core.workspace import entry_frame, result_frame
from core.excel_handler import export_payroll_to_excel
from ui import common
from ui.common import metrics, money


def save_month(batch_id, calculate):
    buffer = st.session_state['month_buffers'][batch_id]
    try:
        common.db('save_inputs', batch_id, buffer['frame'], buffer['versions'], calculate=calculate,
                  expected_lifecycle=buffer['lifecycle_version'])
        st.session_state['month_buffers'].pop(batch_id, None)
        st.session_state.monthly_notice = ('已保存并计算本月工资。下方“本月计算结果”可查看明细和下载。'
            if calculate else '填写内容已保存。准备好后，再点击“保存并计算本月工资”。')
    except (ValueError, psycopg.Error) as exc:
        st.session_state.monthly_error = exc


def start_month(store_id, month, batch_id=None):
    try:
        _, added = common.db('start_month', store_id, month)
        st.session_state.get('month_buffers', {}).pop(batch_id, None)
        st.session_state.monthly_notice = f'已带入 {added} 名员工，已有填写内容保持不变。'
    except (ValueError, psycopg.Error) as exc:
        st.session_state.monthly_error = exc


def reload_month(batch_id):
    st.session_state.get('month_buffers', {}).pop(batch_id, None)


def transition(batch_id, action, version, ids, cap, checkbox_key, reason_key=None):
    try:
        if not st.session_state.get(checkbox_key):
            raise ValueError('请先勾选核对确认。')
        buffer = st.session_state.get('month_buffers', {}).get(batch_id)
        if buffer and not buffer['frame'].equals(buffer['base']):
            raise ValueError('仍有未保存的修改，请先保存并计算。')
        common.db('transition_month', batch_id, action, version, calculation_ids=ids,
                  expected_cap=cap, reason=st.session_state.get(reason_key, '') if reason_key else '')
        reload_month(batch_id)
        st.session_state.monthly_notice = {'confirm':'本月工资已确认并锁定。尚未标记发放，不会自动转账。',
            'paid':'已记录您确认的发放状态。本系统未执行转账。',
            'reopen':'已记录更正原因并解除锁定。请更正、核算并重新确认；不会撤销已完成的转账。'}[action]
    except (ValueError, psycopg.Error) as exc:
        st.session_state.monthly_error = exc


@st.fragment(key='monthly_payroll')
def render_payroll(month, stores, entry_mode):
    st.divider()
    st.subheader('核对门店工资')
    notice_slot = st.empty()
    error_slot = st.empty()
    message = st.session_state.pop('monthly_notice', None)
    if message:
        notice_slot.success(message)
    error = st.session_state.pop('monthly_error', None)
    if error:
        with error_slot.container():
            common.show_error(error)
    store_id = st.selectbox('查看 / 计算的门店', [s['id'] for s in stores],
        format_func=lambda value: next(s['name'] for s in stores if s['id']==value), key='monthly_store')
    store = next(s for s in stores if s['id']==store_id)
    view = common.fetch('month_view', store_id, month)
    batch = view['batch'] if view else None
    if batch is None:
        st.subheader('这个月还没有工资记录')
        st.write('点击下方按钮，自动带入在职员工和常用工资。考勤从 0 开始，请逐人核对。')
        st.caption('入职日期晚于本月的员工不会带入。月中入职的工资需手动核对，不自动折算。')
        st.button('建立本月工资', type='primary', on_click=start_month, args=(store_id, month))
        st.page_link('views/employees_page.py', label='先添加或检查员工档案', icon=':material/group:')
        return

    entries = view['entries']
    buffers = st.session_state.setdefault('month_buffers', {})
    if batch['id'] not in buffers:
        frame = entry_frame(entries, store['name'])
        buffers[batch['id']] = dict(frame=frame, base=frame.copy(deep=True),
            versions={row['id']: row['input_version'] for row in entries}, revision=0,
            lifecycle_version=batch['lifecycle_version'])
    buffer = buffers[batch['id']]
    generation = buffer.setdefault('generation', uuid4().hex)
    dirty = not buffer['frame'].equals(buffer['base'])
    outdated = (buffer['versions'] != {row['id']: row['input_version'] for row in entries}
                or buffer['lifecycle_version'] != batch['lifecycle_version'])
    locked = batch['status'] != 'draft'
    results = view['results']
    complete = (bool(entries) and len(results) == len(entries) and not dirty and not outdated
                and all(row['net_pay'] >= 0 for row in results))
    total = sum((row['net_pay'] for row in results), Decimal(0)) if complete else None
    cap = batch['payroll_cap_snapshot']
    def close_cap_dialog():
        st.session_state.pop('cap_dialog_month', None)


    def save_cap(enabled_key, value_key, expected):
        try:
            enabled = st.session_state[enabled_key]
            value = st.session_state[value_key]
            common.db('set_cap', batch['id'], value if enabled else None, expected)
            buffer['cap_expected'] = Decimal(str(value)) if enabled else None
            st.session_state.monthly_notice = '本月工资上限已保存。'
            close_cap_dialog()
            st.rerun()
        except (ValueError, psycopg.Error) as exc:
            st.session_state.monthly_error = exc
            close_cap_dialog()
            st.rerun()

    @st.dialog('设置本月工资上限', on_dismiss=close_cap_dialog)
    def edit_payroll_cap():
        st.caption('与本店本月全部已录入员工的实发合计比较。只影响本月，达到或超过上限会提示暂无职级晋升空间。')
        cap_expected = buffer.setdefault('cap_expected', cap)
        enabled_key = f'cap_enabled_{batch["id"]}_{generation}'
        value_key = f'cap_value_{batch["id"]}_{generation}'
        with st.form(f'cap_{batch["id"]}_{generation}'):
            st.checkbox('设置本月工资上限', value=cap_expected is not None, disabled=locked, key=enabled_key)
            st.number_input('上限金额（元）', min_value=0.0, max_value=999999999999.99,
                            value=float(cap_expected or 0), step=1000.0, disabled=locked, key=value_key)
            st.caption('取消勾选并保存，可移除本月上限。关闭窗口不会保存修改。')
            st.form_submit_button('保存本月上限', type='primary', disabled=locked, width='stretch',
                                  on_click=save_cap, args=(enabled_key, value_key, cap_expected))


    metrics([
        ('本月员工', str(len(entries)), '请核对名单是否完整'),
        ('本月实发合计', money(total), '计算后更新'),
        ('填写状态', {'confirmed':'已确认 · 已锁定','paid':'已标记发放 · 已锁定'}.get(batch['status'],
            '未保存' if dirty else ('已计算' if complete else '待计算')), '确认后不能直接修改'),
    ])
    cap_info, cap_action = st.columns([3, 1], vertical_alignment='center')
    with cap_info:
        st.caption('本月工资上限已设置 · 金额仅在设置窗口显示' if cap is not None else '本月尚未设置工资上限')
    with cap_action:
        if st.button('设置本月工资上限', icon=':material/tune:', width='stretch'):
            st.session_state.cap_dialog_month = batch['id']
    if st.session_state.get('cap_dialog_month') == batch['id']:
        edit_payroll_cap()

    cap_status = st.empty()
    if complete and cap is not None:
        if total >= cap:
            cap_status.warning('已达到本月工资上限，暂无职级晋升空间。请先核对员工名单是否完整。')
        else:
            cap_status.info('本月实发合计未达到工资上限。低于上限不代表自动晋升。')


    def edit_field(key, batch_id, row, column):
        st.session_state['month_buffers'][batch_id]['frame'].at[row, column] = st.session_state[key]


    def edit_table(key, batch_id):
        item = st.session_state['month_buffers'][batch_id]
        for row, changes in st.session_state[key]['edited_rows'].items():
            for column, value in changes.items():
                item['frame'].at[int(row), column] = value
        item['revision'] += 1


    with st.container(border=True, key='month_entry'):
        st.subheader('手动填写考勤与备注' if entry_mode == '手动填写' else '核对已保存的考勤与备注')
        if locked:
            st.info('这个月已确认，当前页面只供查看。')
        elif outdated:
            st.warning('本月数据已被其他操作更新，请先重新读取；当前页面不会覆盖较新的内容。')
        elif dirty:
            st.warning('有未保存的填写内容。填好所有员工后，点击下方黄色按钮保存并计算。')
        elif complete:
            st.caption('当前填写已保存并计算。需要更正时，修改下方内容后再次保存并计算。')
        else:
            st.caption('填写已保存，但工资尚未计算。核对后点击下方黄色按钮。')
        if entries and entry_mode == '手动填写':
            mode = st.radio('填写方式', ['按员工填写', '表格批量编辑'], horizontal=True, key='monthly_input_mode')
            prefix = f'input_{batch["id"]}_{generation}_{buffer["revision"]}'
            if mode == '按员工填写':
                row_index = st.selectbox('选择要填写的员工', list(buffer['frame'].index),
                    format_func=lambda i: f"{buffer['frame'].at[i,'姓名']} · {buffer['frame'].at[i,'员工编号']}",
                    key=f'month_employee_{batch["id"]}')
                row = buffer['frame'].loc[row_index]
                st.caption('切换员工可以继续填写；所有员工的改动统一用下方按钮保存。没有发生的考勤填 0。')
                cols = st.columns(2)
                fields = [('普通缺勤天数','普通缺勤（天）',0,30,1),
                          ('旷工天数','旷工（天）',0,30,1),
                          ('迟到','累计迟到（分钟）',0,None,1),
                          ('有效加班时长','有效加班（小时）',0.0,None,0.5)]
                for index, (column,label,minimum,maximum,step) in enumerate(fields):
                    key = f'{prefix}_{row_index}_{column}'
                    value = None if pd.isna(row[column]) else (float(row[column]) if isinstance(step,float) else int(row[column]))
                    cols[index%2].number_input(label, min_value=minimum, max_value=maximum,
                        value=value, step=step, key=key, disabled=locked or outdated,
                        on_change=edit_field, args=(key,batch['id'],row_index,column))
                st.caption('普通缺勤与旷工不能重复计天，两项合计不超过 30 天。加班可填写小数小时。')
                key = f'{prefix}_{row_index}_notes'
                st.text_area('工作表现 / 失误备注（不参与扣款）', value=row['工作失误项目'] or '',
                    placeholder='例如：本月学习进度良好；操作失误已提醒。', key=key,
                    disabled=locked or outdated, on_change=edit_field,
                    args=(key,batch['id'],row_index,'工作失误项目'))
                with st.expander('查看或调整这位员工的本月工资金额'):
                    st.caption('以下为扣考勤前的金额。只修改本月；长期调薪请到“员工档案”修改常用工资。')
                    cols = st.columns(2)
                    for index,column in enumerate(['工资','绩效','全勤','通勤补贴']):
                        key = f'{prefix}_{row_index}_{column}'
                        cols[index%2].number_input(f'{column}（元）', min_value=0.0, max_value=999999999.99,
                            value=None if pd.isna(row[column]) else float(row[column]), step=100.0, format='%.2f', key=key,
                            disabled=locked or outdated, on_change=edit_field,
                            args=(key,batch['id'],row_index,column))
                    st.caption('工资已包含社保补贴；通勤补贴填写本月实际应发金额。')
            else:
                st.info('双击单元格填写，按 Enter 完成。右侧金额和备注可横向滚动查看；填写后仍需点击下方按钮保存。')
                order = ['姓名','员工编号','普通缺勤天数','旷工天数','迟到','有效加班时长','工资','绩效','全勤','通勤补贴','工作失误项目']
                columns = {col: st.column_config.NumberColumn(col+'（元）', min_value=0.0, step=.01, format='%.2f', required=True)
                           for col in ['工资','绩效','全勤','通勤补贴']}
                columns.update({col: st.column_config.NumberColumn(col, min_value=0, max_value=30, step=1, required=True)
                                for col in ['普通缺勤天数','旷工天数']})
                columns['迟到'] = st.column_config.NumberColumn('迟到（分钟）', min_value=0, step=1, required=True)
                columns['有效加班时长'] = st.column_config.NumberColumn('加班（小时）', min_value=0.0, step=.5, required=True)
                columns['工作失误项目'] = st.column_config.TextColumn('表现 / 失误备注（不扣款）', width='large')
                st.data_editor(buffer['frame'], key=prefix, on_change=edit_table, args=(prefix,batch['id']),
                    column_order=order, column_config=columns,
                    disabled=True if locked or outdated else ['员工编号','姓名','分店','岗位','职级','入职时间'],
                    hide_index=True, width='stretch', height='auto')
        if entries and entry_mode == 'AI 识别':
            st.caption('下表为本店已保存或手动填写的月度记录。需要直接改数字时，可切换上方“手动填写”。')
            st.dataframe(buffer['frame'][['姓名','员工编号','普通缺勤天数','旷工天数','迟到','有效加班时长','工作失误项目']],
                         hide_index=True, width='stretch')
        if entries:
            st.divider()
            st.write('核对本店本月记录后，保存并计算工资。')
            a,b = st.columns(2)
            a.button('保存并计算本月工资', type='primary', disabled=outdated or locked,
                     width='stretch', icon=':material/calculate:', on_click=save_month, args=(batch['id'], True))
            b.button('仅保存，稍后计算', disabled=outdated or locked, width='stretch',
                     on_click=save_month, args=(batch['id'], False))
            st.caption('黄色按钮会同时保存输入和计算结果，不会转账或确认发薪。仅保存时，修改过的记录仍需重新计算。')
        else:
            st.info('本月还没有员工。先到员工档案添加，再点击下方“将新员工加入本月”。')
            st.page_link('views/employees_page.py', label='添加员工', icon=':material/group_add:')

    if complete:
        with st.container(border=True, key='month_results'):
            st.subheader('本月计算结果')
            st.success(f'已保存 · 本月 {len(entries)} 名员工，实发合计 {money(total)}')
            details = result_frame(results)
            details['确认状态'] = {'draft':'核算草稿（未确认）','confirmed':'已确认（未标记发放）','paid':'已标记发放'}[batch['status']]
            details['确认时间'] = str(batch['confirmed_at'] or '')
            details['发放标记时间'] = str(batch['paid_at'] or '')
            display = details[['姓名','工资扣款','绩效扣款','全勤扣款','迟到扣款','加班费','合计']].rename(columns={'合计':'实发金额'})
            st.dataframe(display, hide_index=True, width='stretch',
                column_config={c:st.column_config.NumberColumn(c,format='%.2f') for c in display.columns if c!='姓名'})
            with st.expander('查看一位员工的工资明细'):
                selected = st.selectbox('查看员工', list(details.index),
                    format_func=lambda i:f"{details.at[i,'姓名']} · {details.at[i,'员工编号']}")
                record = details.loc[selected]
                st.dataframe(pd.DataFrame([{'项目':field,'金额（元）':float(record[field])} for field in
                    ['工资','绩效','全勤','通勤补贴','加班费','工资扣款','绩效扣款','全勤扣款','迟到扣款','合计']]),
                    hide_index=True, width='stretch', column_config={'金额（元）':st.column_config.NumberColumn(format='%.2f')})
                st.write('表现 / 失误备注：',record['工作失误项目'] or '无')
            st.download_button('下载本月工资明细（Excel）', export_payroll_to_excel(details),
                f'{month:%Y-%m}_{batch["status"]}_工资核算明细.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore')
            st.page_link('views/history_page.py',label='查看历次计算记录',icon=':material/history:')
            st.divider()
            version = batch['lifecycle_version']
            ids = [r['id'] for r in results]
            if not locked:
                st.write('核对完成后，确认并锁定本月工资。')
                key = f'confirm_month_{batch["id"]}_{version}_{"_".join(map(str, ids))}_{cap}'
                checked = st.checkbox('我已核对员工名单、考勤、金额及需另行处理的调整，确认本月工资', key=key)
                st.button('确认并锁定本月工资', disabled=not checked, on_click=transition,
                    args=(batch['id'], 'confirm', version, ids, cap, key))
                st.caption('锁定后不能直接修改或 AI 写入。报废、奖罚和月中入离职折算尚未自动处理，请确认已完成必要核对。')
            else:
                st.info('本月工资已锁定，确认时的计算结果会保留。确认和发放标记均不会执行转账。')
                if batch['status'] == 'confirmed':
                    key = f'paid_month_{batch["id"]}_{version}'
                    checked = st.checkbox('我已在系统外完成本月工资发放，只记录状态', key=key)
                    st.button('标记已发放', disabled=not checked, on_click=transition,
                        args=(batch['id'], 'paid', version, ids, cap, key))
                with st.expander('需要更正已确认的工资'):
                    reason_key = f'reopen_reason_{batch["id"]}_{version}'
                    reason = st.text_area('更正原因', key=reason_key, max_chars=1000)
                    key = f'reopen_month_{batch["id"]}_{version}'
                    checked = st.checkbox('我理解旧确认记录会保留，更正不会撤销实际转账，差额需另行核对处理', key=key)
                    st.button('记录原因并解除锁定', disabled=not checked or not reason.strip(), on_click=transition,
                        args=(batch['id'], 'reopen', version, ids, cap, key, reason_key))
    elif entries:
        st.info('结果暂不可下载。保存并计算成功后，这里会显示本月实发金额和下载按钮。')

    with st.expander('员工名单与重新读取数据'):
        st.caption('加入员工只补充缺少的在职人员，不重置本月已有工资或考勤。请先保存尚未提交的修改。')
        st.button('将新员工加入本月', disabled=dirty or locked or outdated, on_click=start_month, args=(store_id, month, batch['id']))
        st.page_link('views/employees_page.py',label='新增或维护员工档案')
        st.button('放弃未保存修改并重新读取' if dirty else '重新读取已保存数据', on_click=reload_month, args=(batch['id'],))
    st.caption('本月已锁定。需要更正时，请在计算结果下填写原因并解除锁定。' if locked else
               '当前为核算草稿。已保存的数据下次打开仍可使用；尚未保存的填写内容仅留在当前网页会话。')
