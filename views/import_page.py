"""按新表头显式导入，不在页面重跑时覆盖已修改的草稿。"""
import streamlit as st
import pandas as pd
from core.excel_handler import create_attendance_template, example_inputs, read_attendance_excel
from core.payroll_calc import PayrollValidationError
from core.workflow import load_inputs

from ui.common import header
header('UNION BAKERY / EXCEL', 'Excel 导入与试算', '适合检查旧表或验证样例。日常发薪请使用月度工资，不需要每月重新上传。')
st.info('此工具的导入内容仅用于临时试算，不会自动写入员工档案或月度数据库。')
a, b = st.columns(2)
with a:
    st.download_button("下载空白工资模板", create_attendance_template(), "工资录入空白模板.xlsx")
with b:
    st.download_button("下载虚构填写示例", create_attendance_template(sample=True), "虚构工资示例.xlsx")
st.caption("姓名、工资、绩效、全勤必填，没有该项请填0。其余考勤数值和通勤补贴留空按0。迟到单位为分钟，加班单位为小时。")

if st.button("载入8名虚构员工（替换当前草稿）"):
    load_inputs(st.session_state, example_inputs(), "虚构样例")
    st.success("已载入虚构样例，请前往表格试算。")

store = st.text_input("表内未填写分店时使用", value="分店A")
upload = st.file_uploader("上传工资表 Excel", type=["xlsx"])
if st.button("读取文件并替换当前草稿", disabled=upload is None):
    st.session_state.payroll_df = None
    try:
        df = read_attendance_excel(upload, default_store=store)
        load_inputs(st.session_state, df, upload.name)
        st.success(f"成功读取 {len(df)} 名员工。")
        ignored = df.attrs.get("ignored_columns", [])
        if ignored:
            st.warning("以下列不参与本版计算：" + "、".join(ignored))
    except PayrollValidationError as exc:
        st.error("导入未完成。下方为错误行，请修改文件后重试；原草稿保留，旧结果已失效。")
        st.dataframe(pd.DataFrame(exc.issues), hide_index=True, width="stretch")
    except Exception as exc:
        st.error(f"文件读取失败：{exc}。原草稿保留，旧结果已失效。")

if st.session_state.draft_df is not None:
    st.write(f"当前草稿：{st.session_state.input_source}，共 {len(st.session_state.draft_df)} 人")
    st.dataframe(st.session_state.draft_df, hide_index=True, width="stretch")
    st.page_link("views/editor_page.py", label="下一步：表格试算", icon="✏️")
