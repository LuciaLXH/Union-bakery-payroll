"""员工档案表格草稿与批量保存。"""
from uuid import uuid4

import pandas as pd
import psycopg
import streamlit as st

from core.workspace import employee_frame, EMPLOYEE_DEFAULTS
from ui.common import db, show_error


def apply_employee_table(key, store_id):
    buffer = st.session_state['employee_buffers'][store_id]
    changes = st.session_state[key]
    frame = buffer['frame'].copy(deep=True)
    for index, values in changes['edited_rows'].items():
        for column, value in values.items():
            if column != '_id':
                frame.at[int(index), column] = value
    frame = frame.drop(index=changes['deleted_rows']).reset_index(drop=True)
    rows = frame.to_dict('records')
    for values in changes['added_rows']:
        row = dict(EMPLOYEE_DEFAULTS, **values)
        row['_id'] = None
        rows.append(row)
    buffer['frame'] = pd.DataFrame(rows, columns=frame.columns).reset_index(drop=True)
    buffer['revision'] += 1


def render_employee_editor(store_id, employees):
    buffers = st.session_state.setdefault('employee_buffers', {})
    if store_id not in buffers:
        frame = employee_frame(employees)
        buffers[store_id] = dict(frame=frame, base=frame.copy(deep=True),
                                expected=employees, revision=0, generation=uuid4().hex)
    buffer = buffers[store_id]
    with st.container(border=True):
        st.subheader('批量新增或修改员工')
        st.caption('直接点击单元格修改，点击表格底部“＋”添加员工。金额单位为元，约定工资包含社保补贴；没有的项目请填 0。表格可横向滚动。')
        st.caption('所有行归属上方所选门店。离职请取消勾选“在职”，不要删除已有员工行。修改档案不改变已经建立的月份。')
        dirty = not buffer['frame'].equals(buffer['base'])
        if dirty:
            st.warning('有未保存的员工资料，请点击下方“保存全部员工档案”。')
        key = f'employee_table_{store_id}_{buffer["generation"]}_{buffer["revision"]}'
        configs = {
            '_id': None,
            '员工编号': st.column_config.TextColumn('员工编号', help='每位员工使用唯一编号，例如 B001。'),
            '姓名': st.column_config.TextColumn('姓名'),
            '岗位': st.column_config.TextColumn('岗位'),
            '职级': st.column_config.TextColumn('职级'),
            '入职日期': st.column_config.DateColumn('入职日期', format='YYYY-MM-DD'),
            '在职': st.column_config.CheckboxColumn('在职', default=True),
        }
        for column in ('约定工资', '月绩效', '月全勤', '通勤补贴'):
            configs[column] = st.column_config.NumberColumn(column + '（元）',
                min_value=0.0, max_value=999999999.99, step=100.0, format='%.2f', default=0.0)
        st.data_editor(buffer['frame'], column_config=configs, disabled=['_id'],
                       hide_index=True, num_rows='dynamic', width='stretch', key=key,
                       on_change=apply_employee_table, args=(key, store_id))
        a, b = st.columns(2)
        if a.button('保存全部员工档案', type='primary', disabled=buffer['frame'].empty):
            try:
                saved = db('save_employees', store_id, buffer['frame'], buffer['expected'])
                buffers.pop(store_id)
                st.session_state.pop('employee_form_identity', None)
                st.session_state.workspace_flash = f'已保存 {len(saved)} 名员工的档案。新员工可到月度工资中补入；已有月份金额不会自动改变。'
                st.rerun()
            except (ValueError, psycopg.Error) as exc:
                show_error(exc)
        if b.button('放弃表格修改并重新读取' if dirty else '重新读取员工表格'):
            buffers.pop(store_id)
            st.rerun()
        st.page_link('views/monthly_page.py', label='去月度工资填写考勤')
