"""新引擎的明细导出入口，门店预算和个人工资条在第三阶段加入。"""
import streamlit as st
from core.excel_handler import export_payroll_to_excel

from ui.common import header
header("UNION BAKERY / EXCEL", "试算结果与导出", "核对 Excel 试算结果。这部分数据只保存在当前会话中。")
result = st.session_state.payroll_df
if result is None:
    st.warning("尚无当前输入的核算结果，请先前往表格试算。")
    st.page_link("views/editor_page.py", label="前往表格试算")
    st.stop()
st.dataframe(result, hide_index=True, width="stretch")
compared = result["原表合计"].notna() | result["原表加班费"].notna()
if compared.any():
    difference = (result["合计差额"].notna() & result["合计差额"].ne(0)) | (result["加班费差额"].notna() & result["加班费差额"].ne(0))
    if difference.any():
        st.warning("部分结果与原表不同，请核对。差额＝系统计算金额－原表金额。")
    else:
        st.success("原表中已填写的合计、加班费与本次计算一致。")
else:
    st.info("原表未提供合计或加班费，没有外部结果可对账。")
st.caption("这里导出临时试算明细。需要保存并追溯的工资，请使用月度工资页面。")
if result["核算提示"].ne("").any():
    st.error("存在异常核算结果，暂不能导出。请返回核算页面修正。")
else:
    st.download_button("下载本次核算明细", data=export_payroll_to_excel(result),
                       file_name="工资核算明细.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
