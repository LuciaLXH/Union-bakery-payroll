"""Compact, read-only pay cards. Details render only when requested."""
from decimal import Decimal
from html import escape

import pandas as pd
import streamlit as st

from ui.common import money


def attendance_summary(record):
    fields = [('普通缺勤天数', '请假', '天'), ('旷工天数', '旷工', '天'),
              ('迟到', '迟到', '分钟'), ('有效加班时长', '加班', '小时')]
    items = [f'{label} {Decimal(str(record[key])).normalize():f} {unit}'
             for key, label, unit in fields if record.get(key)]
    return ' · '.join(items) or '暂无缺勤、迟到或加班'


def render_breakdown(record):
    fixed = ['工资', '绩效', '全勤', '通勤补贴']
    deductions = ['工资扣款', '绩效扣款', '全勤扣款', '迟到扣款']
    rows = [{'项目': '工资（含社保补贴）' if k == '工资' else k, '金额': money(record[k])} for k in fixed]
    base = sum((Decimal(str(record[k])) for k in fixed), Decimal(0))
    rows.append({'项目': '考勤调整前合计', '金额': money(base)})
    rows.extend({'项目': k, '金额': '−' + money(record[k])} for k in deductions)
    rows.extend([{'项目': '加班费', '金额': '+' + money(record['加班费'])},
                 {'项目': '预计实发', '金额': money(record['合计'])}])
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', height='auto')
    st.caption('工资已含社保补贴；通勤补贴按本月填写金额发放，不随缺勤折算。')
    if record.get('工作失误项目'):
        st.text('备注：' + str(record['工作失误项目']))


@st.fragment
def render_cards(records, *, key, impacts=None, sources=None, on_edit=None, edit_disabled=False):
    from ui.access import check_access
    check_access()
    for record in records:
        code = record['员工编号']
        impact = (impacts or {}).get(code)
        with st.container(border=True):
            st.markdown(f"**{escape(str(record['姓名']))}** · {escape(str(record['分店']))}")
            st.markdown(f"### 预计实发 {money(record['合计'])}")
            if impact:
                delta = impact['实发变化']
                st.caption('本次无金额变化' if not delta else
                           f"本次{'增加' if delta > 0 else '减少'} {money(abs(delta))}")
            st.caption('本月考勤：' + attendance_summary(record))
            for source in (sources or {}).get(code, []):
                st.text('本次记录：' + source)
            detail = st.expander('查看金额明细', key=f'{key}_{code}_detail', on_change='rerun')
            if detail.open:
                with detail:
                    if impact:
                        st.write('本次录入前预计实发：', money(impact['原实发试算']))
                        st.write('本次录入后预计实发：', money(impact['本次确认后预计实发']))
                        st.caption('录入前金额包含此前保存的本月考勤；不是上月工资。以下为录入后的月度合计。')
                    render_breakdown(record)
            if on_edit is not None:
                st.button('修改此员工考勤', key=f'{key}_{code}_edit', disabled=edit_disabled,
                          on_click=on_edit, args=(code,))

    full = st.expander('查看完整表格', key=f'{key}_table', on_change='rerun')
    if full.open:
        with full:
            columns = ['姓名', '分店', '工资', '绩效', '全勤', '通勤补贴',
                       '工资扣款', '绩效扣款', '全勤扣款', '迟到扣款', '加班费', '合计']
            frame = pd.DataFrame(records)[columns].rename(columns={'合计': '预计实发'})
            st.dataframe(frame, hide_index=True, width='stretch')
