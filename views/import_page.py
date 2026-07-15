"""
页面：数据导入与配置
提供模板下载、社保及营业额参数配置、考勤与报损 Excel 表上传，并触发核算。
"""

import os
import streamlit as st
import pandas as pd
from core.excel_handler import generate_blank_templates, read_attendance_excel, read_loss_excel
from core.payroll_calc import calculate_payroll

st.markdown("# 📥 数据导入与配置")
st.markdown("请在此页面配置本月基本核算参数，并导入员工考勤表与报损登记表。")
st.divider()

# ==================== 1. 模板下载区 ====================
st.markdown("### 📋 第一步：获取数据导入模板")
st.caption("下载空白模板，并按照模板格式填写本月的员工考勤花名册与报损单明细。")

# 检查并自动生成空白模板
templates_dir = "templates"
attendance_tpl_path = os.path.join(templates_dir, "attendance_template.xlsx")
loss_tpl_path = os.path.join(templates_dir, "loss_template.xlsx")

if not os.path.exists(attendance_tpl_path) or not os.path.exists(loss_tpl_path):
    try:
        generate_blank_templates(templates_dir)
    except Exception as e:
        st.error(f"生成模板时出错: {e}")

col1, col2 = st.columns(2)

with col1:
    st.info("💡 **考勤花名册模板**：填写员工底薪信息、社保意向、出勤天数与加班时长。")
    if os.path.exists(attendance_tpl_path):
        with open(attendance_tpl_path, "rb") as f:
            st.download_button(
                label="📥 下载《员工考勤模板.xlsx》",
                data=f,
                file_name="员工考勤花名册模板.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

with col2:
    st.info("💡 **每日报损登记模板**：填写客诉成品/半成品/原材料损耗、指定责任人或分店平摊。")
    if os.path.exists(loss_tpl_path):
        with open(loss_tpl_path, "rb") as f:
            st.download_button(
                label="📥 下载《报损登记模板.xlsx》",
                data=f,
                file_name="报损登记明细模板.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

st.divider()

# ==================== 2. 核算参数配置 ====================
st.markdown("### ⚙️ 第二步：配置月度全局参数")

col_ss, col_blank = st.columns([1, 2])
with col_ss:
    # 动态配置当月实际社保扣除金额
    st.session_state.social_security_std = st.number_input(
        "当月社保实际缴纳标准 (元/人/月)",
        min_value=0.0,
        value=st.session_state.social_security_std,
        step=50.0,
        help="上社保的员工将从底薪中扣除此金额"
    )

st.markdown("##### 🏪 各分店当月总营业额录入")
st.caption("营业额将用于计算各门店日均人效，以判定后厨加班费发放门槛（日人效需 > 1800 元/人/天）。")

# 动态获取已加载花名册中的分店列表，若未导入则采用默认分店
available_stores = ["分店A", "分店B", "分店C"]
if st.session_state.employees_df is not None:
    available_stores = sorted(st.session_state.employees_df["分店"].unique().tolist())

# 重新同步 store_sales 字典的键
updated_sales = {}
sales_cols = st.columns(len(available_stores))

for idx, store in enumerate(available_stores):
    with sales_cols[idx]:
        # 取得旧值作为默认值
        default_val = st.session_state.store_sales.get(store, 100000.0)
        val = st.number_input(
            f"【{store}】月营业额 (元)",
            min_value=0.0,
            value=float(default_val),
            step=1000.0,
            key=f"sales_{store}"
        )
        updated_sales[store] = val

# 更新全局状态
st.session_state.store_sales = updated_sales

st.divider()

# ==================== 3. 文件上传与解析 ====================
st.markdown("### 📤 第三步：上传月度核算表格")

upload_col1, upload_col2 = st.columns(2)

# 用于标示上传状态的变量
has_attendance = False
has_loss = False

with upload_col1:
    attendance_file = st.file_uploader(
        "上传 考勤花名册 Excel (.xlsx)",
        type=["xlsx"],
        help="必须包含姓名、分店、职位、社保状态、出勤等列"
    )
    if attendance_file is not None:
        try:
            # 解析并保存到 session_state
            emp_df = read_attendance_excel(attendance_file)
            st.session_state.employees_df = emp_df
            has_attendance = True
            st.success(f"✅ 考勤表解析成功！共读取到 {len(emp_df)} 名员工数据。")
            
            # 简易预览
            with st.expander("👁️ 预览已导入考勤数据"):
                st.dataframe(emp_df.head(10), use_container_width=True)
        except Exception as e:
            st.error(f"❌ 考勤表解析失败: {e}")
            st.session_state.employees_df = None

with upload_col2:
    loss_file = st.file_uploader(
        "上传 报损明细登记 Excel (.xlsx) [可选]",
        type=["xlsx"],
        help="如本月无报损记录可不上传"
    )
    if loss_file is not None:
        try:
            l_df = read_loss_excel(loss_file)
            st.session_state.loss_df = l_df
            has_loss = True
            st.success(f"✅ 报损表解析成功！共读取到 {len(l_df)} 条有效报损流水。")
            
            with st.expander("👁️ 预览已导入报损明细"):
                st.dataframe(l_df.head(10), use_container_width=True)
        except Exception as e:
            st.error(f"❌ 报损表解析失败: {e}")
            st.session_state.loss_df = None
    else:
        # 未上传时，清空之前上传的报损数据
        st.session_state.loss_df = None

st.divider()

# ==================== 4. 触发计算 ====================
st.markdown("### 🚀 第四步：执行薪酬核算")

if st.session_state.employees_df is not None:
    # 提醒用户是否已经配置好营业额
    st.info("提示：请核对上方分店营业额无误后，点击下方按钮开始算薪计算。")
    
    if st.button("📊 开始执行月度算薪", type="primary", use_container_width=True):
        with st.spinner("算薪引擎运行中，正在计算复杂分摊与免责规则..."):
            try:
                # 运行计算
                result_df = calculate_payroll(
                    employees_df=st.session_state.employees_df,
                    loss_df=st.session_state.loss_df,
                    store_sales=st.session_state.store_sales,
                    social_security_std=st.session_state.social_security_std
                )
                
                # 存入全局 session_state
                st.session_state.payroll_df = result_df
                
                st.balloons()
                st.success("🎉 工资核算完成！请点击左侧侧边栏导航进入【2. 在线算薪微调】核对与修改细节。")
                
                # 简易成效展示
                st.markdown("#### 📊 门店人效达标情况速览")
                sum_cols = st.columns(len(st.session_state.store_sales))
                for i, (store, sales) in enumerate(st.session_state.store_sales.items()):
                    store_emp_count = len(result_df[result_df["分店"] == store])
                    avg_eff = result_df[result_df["分店"] == store]["店铺平均人效"].iloc[0] if store_emp_count > 0 else 0.0
                    threshold = 1800.0
                    
                    with sum_cols[i]:
                        if avg_eff > threshold:
                            st.metric(
                                label=f"{store} 人效 (已达标)", 
                                value=f"¥{avg_eff:.1f} /人/天", 
                                delta=f"+{avg_eff-threshold:.1f} (发加班费)",
                                delta_color="normal"
                            )
                        else:
                            st.metric(
                                label=f"{store} 人效 (未达标)", 
                                value=f"¥{avg_eff:.1f} /人/天", 
                                delta=f"{avg_eff-threshold:.1f} (无加班费)",
                                delta_color="inverse"
                            )
            except Exception as e:
                st.error(f"❌ 算薪核心计算失败，请检查配置或数据格式：{e}")
else:
    st.warning("⚠️ 请先在【第三步】中成功上传考勤花名册文件，以激活算薪计算。")
