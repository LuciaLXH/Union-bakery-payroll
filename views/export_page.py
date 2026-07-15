"""
页面：经营分析与报表导出
提供多分店薪酬汇总图表、平均人效可视化、薪资构成分析，以及一键导出格式化 Excel 报表。
"""

import tempfile
import os
import streamlit as st
import pandas as pd
from core.excel_handler import export_payroll_to_excel
from core.payroll_calc import get_employee_loss_details
import config

st.markdown("# 📊 经营分析与报表导出")
st.markdown("在此页面，您可以查看本月各门店的经营与人效数据分析，并导出最终的薪资总表 Excel。")
st.divider()

if st.session_state.payroll_df is None:
    st.warning("⚠️ 暂未生成核算数据。请先前往左侧导航的【1. 数据导入与配置】上传数据并生成薪酬。")
else:
    df_payroll = st.session_state.payroll_df
    
    # ==================== 1. 多分店对比图表 ====================
    st.markdown("### 🏪 多分店经营与薪资对比")
    
    # 计算各店的汇总数据
    store_summary_rows = []
    for store, sales in st.session_state.store_sales.items():
        store_df = df_payroll[df_payroll["分店"] == store]
        emp_count = len(store_df)
        total_net = store_df["实发工资"].sum()
        avg_eff = store_df["店铺平均人效"].iloc[0] if emp_count > 0 else 0.0
        
        store_summary_rows.append({
            "分店": store,
            "营业额": sales,
            "员工数": emp_count,
            "日人效": avg_eff,
            "实发薪资": total_net
        })
    df_summary = pd.DataFrame(store_summary_rows)
    
    col_c1, col_c2 = st.columns(2)
    
    with col_c1:
        st.markdown("**💰 各分店月度薪酬实发总额 (元)**")
        st.bar_chart(
            df_summary, 
            x="分店", 
            y="实发薪资", 
            color="#1F4E78", 
            use_container_width=True
        )
        
    with col_c2:
        st.markdown("**⚡ 各分店日平均人效 (元/人/天)**")
        st.bar_chart(
            df_summary, 
            x="分店", 
            y="日人效", 
            color="#FF9900", 
            use_container_width=True
        )
        
    st.divider()
    
    # ==================== 2. 全局薪酬结构拆解 ====================
    st.markdown("### 🧬 全局薪酬构成拆解")
    st.caption("分析发放给全体员工的各项薪资组成，有利于控制人力成本与结构调整。")
    
    base_sum = df_payroll["调整后底薪"].sum()
    attendance_sum = df_payroll["全勤奖"].sum()
    perf_sum = df_payroll["绩效奖金"].sum()
    ot_sum = df_payroll["加班费"].sum()
    welfare_sum = df_payroll["手动福利"].sum()
    
    loss_sum = df_payroll["报损扣款"].sum()
    complaint_sum = df_payroll["投诉扣款"].sum()
    total_deduct = loss_sum + complaint_sum
    
    # 构造薪资结构 DataFrame
    payroll_components = pd.DataFrame({
        "薪资组成项目": ["底薪部分", "全勤奖", "绩效奖金", "加班费", "手动福利"],
        "总金额 (元)": [base_sum, attendance_sum, perf_sum, ot_sum, welfare_sum]
    })
    
    col_chart, col_stats = st.columns([3, 2])
    with col_chart:
        st.bar_chart(
            payroll_components,
            x="薪资组成项目",
            y="总金额 (元)",
            color="#2E75B6",
            use_container_width=True
        )
        
    with col_stats:
        st.markdown("##### 📈 薪资汇总与扣减细节")
        st.write(f"- **基本应发部分 (底薪+福利+奖金)**: ¥{base_sum + attendance_sum + perf_sum + ot_sum + welfare_sum:,.2f}")
        st.write(f"- **加班工资占比**: `{(ot_sum / (df_payroll['实发工资'].sum() + 0.001) * 100):.1f}%`")
        st.write(f"- **总扣减部分 (报损+客诉)**: ¥{total_deduct:,.2f}")
        st.write(f"  - *成品/原料报损扣除*: ¥{loss_sum:,.2f}")
        st.write(f"  - *客诉扣除*: ¥{complaint_sum:,.2f}")
        
    st.divider()
    
    # ==================== 3. 报表导出区 ====================
    st.markdown("### 💾 导出并下载 Excel 工资单")
    st.info("导出后的 Excel 文件包含三个精心美化排版的页签：\n1. **薪资汇总表** (已带底端自动求和公式)\n2. **分店经营指标汇总** (计算各门店平均人效、是否计发加班费、实发总额)\n3. **报损扣款明细** (记录所有损失发生的明细账，备考查证)")
    
    # 获取报损明细大字典
    loss_details_dict = get_employee_loss_details(
        employees_df=df_payroll,
        loss_df=st.session_state.loss_df
    )
    
    # 使用临时文件进行导出
    temp_dir = tempfile.gettempdir()
    temp_output_path = os.path.join(temp_dir, "bakery_payroll_output.xlsx")
    
    try:
        export_payroll_to_excel(
            payroll_df=df_payroll,
            loss_details_dict=loss_details_dict,
            output_path=temp_output_path,
            store_sales=st.session_state.store_sales
        )
        
        with open(temp_output_path, "rb") as file:
            st.download_button(
                label="📥 导出最终 Excel 薪资汇总表",
                data=file,
                file_name="面包店月度薪酬核算汇总表.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )
    except Exception as e:
        st.error(f"导出 Excel 文件时出错: {e}")
