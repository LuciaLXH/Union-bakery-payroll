import pandas as pd
import psycopg
import streamlit as st
from ui.common import header, db, fetch, show_error, metrics, flash
from ui.employee_editor import render_employee_editor

header('UNION BAKERY / OUR TEAM', '员工档案', '保存常用工资与职级，新月份自动带入。以后调薪，不会改变已经建立的月度工资。')
flash()
stores = fetch('stores')
if not stores:
    st.info('先建立一家门店，再添加员工。')
    st.page_link('views/stores_page.py',label='去建立第一家门店')
    st.stop()
store_id = st.selectbox('门店', [s['id'] for s in stores],
    format_func=lambda value: next(s['name'] for s in stores if s['id']==value), key='people_store')
employees = fetch('employees', store_id)
metrics([('员工档案', str(len(employees)), '支持同名员工，以编号区分'),
         ('在职员工', str(sum(e['is_active'] for e in employees)), '新月份从在职名单带入'),
         ('已停用', str(sum(not e['is_active'] for e in employees)), '保留历史，不再自动带入新月份')])
mode = st.radio('填写方式', ['按员工填写', '表格批量编辑'], horizontal=True, key='employee_input_mode')
if mode == '表格批量编辑':
    render_employee_editor(store_id, employees)
    st.stop()
if employees:
    with st.container(border=True):
        st.subheader('员工名册')
        search = st.text_input('搜索姓名或编号', placeholder='输入姓名或员工编号')
        table = pd.DataFrame([{'员工编号': e['employee_code'], '姓名': e['name'],
            '岗位': e['job_title'], '职级': e['grade'], '约定工资': float(e['monthly_salary']),
            '绩效': float(e['monthly_performance']), '全勤': float(e['monthly_attendance_bonus']),
            '状态': '在职' if e['is_active'] else '已停用'} for e in employees
            if search.casefold() in (e['name']+' '+e['employee_code']).casefold()])
        st.dataframe(table, hide_index=True, width='stretch')

with st.container(border=True, key='people_form'):
    st.subheader('新增员工或修改已有档案')
    st.caption('下拉框选择“新增员工”可建立档案；选择已有员工可修改常用工资。保存后到月度工资填写考勤。')
    selected = st.selectbox('新增员工 / 选择已有员工', [None]+[e['id'] for e in employees],
        format_func=lambda value: '＋ 新增员工' if value is None else next(
            f"{e['name']} · {e['employee_code']}" for e in employees if e['id']==value))
    identity = (store_id, selected)
    if st.session_state.get('employee_form_identity') != identity:
        st.session_state.employee_form_identity = identity
        st.session_state.employee_original = next((e for e in employees if e['id']==selected), {})
    original = st.session_state.employee_original
    with st.form(f'employee_{store_id}_{selected}', clear_on_submit=selected is None):
        a, b = st.columns(2)
        code = a.text_input('员工编号', value=original.get('employee_code',''), placeholder='例如 B001')
        name = b.text_input('姓名', value=original.get('name',''))
        a, b, c = st.columns(3)
        title = a.text_input('岗位', value=original.get('job_title') or '')
        grade = b.text_input('职级', value=original.get('grade') or '', placeholder='例如：有基础学徒')
        hire = c.date_input('入职日期（可留空）', value=original.get('hire_date'))
        columns = st.columns(4)
        amounts = {}
        for col, field, label in zip(columns,
            ['monthly_salary','monthly_performance','monthly_attendance_bonus','monthly_commute_allowance'],
            ['约定工资（含社保补贴）','月绩效','月全勤','通勤补贴']):
            amounts[field] = col.number_input(label, min_value=0.0, max_value=999999999.99,
                value=float(original.get(field,0)), step=100.0, format='%.2f')
        active = st.checkbox('在职，参与新月份工资', value=original.get('is_active',True))
        st.caption('没有绩效或补贴请填 0。修改档案只影响以后新建的明细；本月调薪请到月度工资中调整。')
        if st.form_submit_button('保存员工档案', type='primary'):
            try:
                db('save_employee', store_id, dict(employee_code=code,name=name,job_title=title,
                    grade=grade,hire_date=hire,is_active=active,**amounts),
                    employee_id=selected, expected=original)
                st.session_state.pop('employee_form_identity',None)
                st.session_state.workspace_flash = '员工档案已保存。新员工可在月度工资中补入。'
                st.rerun()
            except (ValueError, psycopg.Error) as exc:
                show_error(exc)
    if st.button('重新读取员工档案'):
        st.session_state.pop('employee_form_identity',None)
        st.rerun()
