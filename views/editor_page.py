"""编辑输入后结果失效，显式核算；不再把结果反灌进输入。"""
import streamlit as st
import pandas as pd
import config
from core.payroll_calc import PayrollValidationError
from core.workflow import apply_edits, recalculate

from ui.common import header
header("UNION BAKERY / EXCEL", "表格试算", "Excel 辅助工具：临时试算和核对，不会保存到员工档案或月度工资。")
st.caption("工资包含社保补贴；绩效和全勤填写扣缺勤前的金额。普通缺勤与旷工不能重复计天。")
if st.session_state.draft_df is None:
    st.info("请先导入工资表或载入虚构样例。")
    st.page_link("views/import_page.py", label="前往 Excel 导入")
    st.stop()


def on_edit(key):
    apply_edits(st.session_state, st.session_state[key]["edited_rows"])


columns = {}
for col in config.MONEY_COLUMNS:
    columns[col] = st.column_config.NumberColumn(col, min_value=0.0, step=0.01, format="%.2f", required=col != "通勤补贴")
for col in ("普通缺勤天数", "旷工天数"):
    columns[col] = st.column_config.NumberColumn(col, min_value=0, max_value=30, step=1)
columns["迟到"] = st.column_config.NumberColumn("迟到（分钟）", min_value=0, step=1)
columns["有效加班时长"] = st.column_config.NumberColumn("有效加班时长（小时）", min_value=0.0, step=0.5)
columns["工作失误项目"] = st.column_config.TextColumn("工作失误项目（仅备注）", width="large")
key = f"payroll_inputs_{st.session_state.editor_revision}"
st.data_editor(
    st.session_state.draft_df, key=key, on_change=on_edit, args=(key,),
    column_config=columns, column_order=list(config.INPUT_COLUMNS),
    disabled=["员工编号", "姓名", "分店", "入职时间", *config.REFERENCE_COLUMNS],
    num_rows="fixed", hide_index=True, width="stretch",
)
st.caption("修改会保留在当前会话，并使旧结果失效。身份资料暂由导入表提供；岗位、职级和失误备注不自动改变工资。")
if st.button("核算当前工资", type="primary"):
    try:
        recalculate(st.session_state)
    except PayrollValidationError as exc:
        st.error("请修正下列输入后重新核算。")
        st.dataframe(pd.DataFrame(exc.issues), hide_index=True, width="stretch")

result = st.session_state.payroll_df
if result is None:
    st.warning("当前输入待核算，暂不能导出。")
else:
    if result["核算提示"].ne("").any():
        st.error("存在负数实发，请查看下方明细并核对。结果保留原值，不自动归零。")
    else:
        st.success("已按当前输入完成核算。")
    summary = st.columns(3)
    summary[0].metric("实发合计", f"¥{result['合计'].sum():,.2f}")
    summary[1].metric("加班费", f"¥{result['加班费'].sum():,.2f}")
    summary[2].metric("扣款合计", f"¥{result['扣款合计'].sum():,.2f}")
    st.dataframe(result[["员工编号", "姓名", "工资扣款", "绩效扣款", "全勤扣款", "迟到扣款", "加班费", "合计", "核算提示"]], hide_index=True, width="stretch")
    with st.expander("计算口径"):
        st.write("普通缺勤每天扣工资、绩效、全勤各1/30；旷工每天扣3天工资，绩效和全勤各扣当天。")
        st.write("迟到每分钟扣1元；加班费＝工资÷30÷9×1.5×有效加班小时数。")
        st.write("通勤补贴按填写金额发放；本版不自动计算首月折算、试用期折扣或额外社保扣款。")
    st.page_link("views/export_page.py", label="核对原表差额并导出", icon="📊")
