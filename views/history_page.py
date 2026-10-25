import pandas as pd
import streamlit as st
import config
from core.workspace import result_frame
from ui.common import header, fetch, select_store, money

header('UNION BAKERY / RECORDS', '核算历史', '查看当时的工资、考勤和计算结果。历史版本保留原值，当前有效版本单独标记。')
with st.container(border=True):
    a,b = st.columns(2)
    with a:
        store = select_store('history_store')
    months = fetch('months', store['id'])
    if not months:
        st.info('还没有月度记录，先到月度工资开启第一个月份。')
        st.stop()
    with b:
        selected = st.selectbox('工资月份', [m['payroll_month'] for m in months],
            format_func=lambda value: value.strftime('%Y 年 %m 月'), key='history_month')
batch = next(m for m in months if m['payroll_month']==selected)
with st.expander('查看确认、发放与更正记录'):
    actions = fetch('month_actions', batch['id'])
    if actions:
        labels = {'draft':'草稿','confirmed':'已确认','paid':'已标记发放'}
        st.dataframe(pd.DataFrame([{'时间':str(a[0]), '操作账号':a[1], '说明':a[2],
            '原状态':labels.get(a[3], '新建'), '新状态':labels.get(a[4], a[4])} for a in actions]), hide_index=True)
entries = fetch('entries', batch['id'])
versions = {e['id']:e['input_version'] for e in entries}
history = fetch('history', store_name=store['name'], payroll_month=selected)
if not history:
    st.info('这个月还没有已保存的核算结果。完成“保存并计算本月工资”后，会自动出现在这里。')
    st.page_link('views/monthly_page.py', label='前往月度工资')
    st.stop()
codes = sorted({r['input_snapshot'].get('employee_code_snapshot','') for r in history})
employee = st.selectbox('员工', ['全部员工']+codes)
filtered = [r for r in history if employee=='全部员工' or r['input_snapshot'].get('employee_code_snapshot')==employee]
rows=[]
for r in reversed(filtered):
    current = (r['id'] in (batch['confirmed_calculation_ids'] or []) if batch['status'] != 'draft' else
               versions.get(r['payroll_entry_id'])==r['input_version'] and r['rule_version']==config.RULE_VERSION)
    rows.append({'结果编号':r['id'], '姓名':r['input_snapshot'].get('employee_name_snapshot'),
        '员工编号':r['input_snapshot'].get('employee_code_snapshot'), '输入版本':r['input_version'],
        '实发金额':float(r['net_pay']), '状态':'当前有效' if current else '历史版本',
        '保存时间':r['calculated_at'].strftime('%Y-%m-%d %H:%M %Z')})
st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
st.caption('历史版本不能相加作为本月工资总额。保存时间按数据库返回的时区显示。')
selected_id = st.selectbox('查看一条核算记录', [r['结果编号'] for r in rows],
    format_func=lambda value: next(f"{r['姓名']} · 版本 {r['输入版本']} · {r['状态']} · {money(r['实发金额'])}"
                                  for r in rows if r['结果编号']==value))
record = next(r for r in history if r['id']==selected_id)
with st.container(border=True):
    st.subheader('当时的输入与结果')
    details = result_frame([record]).iloc[0]
    a,b = st.columns(2)
    with a:
        st.dataframe(pd.DataFrame([{'输入项目':key,'当时填写':str(details[key])} for key in
            ['工资','绩效','全勤','通勤补贴','普通缺勤天数','旷工天数','迟到','有效加班时长']]),
            hide_index=True, width='stretch')
    with b:
        st.dataframe(pd.DataFrame([{'核算项目':key,'金额':money(details[key])} for key in
            ['工资扣款','绩效扣款','全勤扣款','迟到扣款','加班费','应发合计','扣款合计','合计']]),
            hide_index=True, width='stretch')
    st.write('表现 / 失误备注：', details['工作失误项目'] or '无')
    st.caption(f"规则版本：{record['rule_version']} · 结果编号：{record['id']}")
