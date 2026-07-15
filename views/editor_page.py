"""
页面：在线算薪微调
使用 st.data_editor 提供类似 Excel 的在线微调功能，修改加班小时、绩效奖金、手动福利、投诉扣款并实时重算。
同时提供详细的员工报损分摊明细穿透查询。
"""

import streamlit as st
import pandas as pd
from core.payroll_calc import calculate_payroll, get_employee_loss_details
import config

st.markdown("# ✏️ 薪酬核算与微调中心")
st.markdown("在此页面，您可以像使用 Excel 一样在线微调员工的绩效、奖金、手动福利、投诉扣款或加班工时。系统会自动联动重算实发工资。")
st.divider()

if st.session_state.payroll_df is None:
    st.warning("⚠️ 暂未生成核算数据。请先前往左侧导航的【1. 数据导入与配置】上传考勤花名册。")
else:
    # ==================== 1. 顶部核心指标看板 ====================
    df_payroll = st.session_state.payroll_df
    
    total_net = df_payroll["实发工资"].sum()
    total_ot = df_payroll["加班费"].sum()
    total_loss_deduct = df_payroll["报损扣款"].sum()
    total_complaint = df_payroll["投诉扣款"].sum()
    
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("💰 当月薪资实发总额", f"¥{total_net:,.2f}")
    with m_col2:
        st.metric("⏰ 当月加班费总支出", f"¥{total_ot:,.2f}")
    with m_col3:
        st.metric("📉 员工报损扣款总计", f"¥{total_loss_deduct:,.2f}")
    with m_col4:
        st.metric("⚠️ 客诉投诉扣款总计", f"¥{total_complaint:,.2f}")
        
    st.divider()
    
    # ==================== 2. 在线编辑表格 ====================
    st.markdown("### 📝 在线薪资微调表格")
    st.caption("双击下列表格中的数值单元格（标注为 **(可改)** 的列：全勤奖、绩效奖金、加班小时、手动福利、客诉扣款）可以直接修改数值，修改后点击表格外部即可自动级联重算薪资。")
    
    # 定义列的展示配置
    column_configuration = {
        "姓名": st.column_config.TextColumn("姓名", disabled=True, width="medium"),
        "分店": st.column_config.TextColumn("分店", disabled=True, width="small"),
        "职位": st.column_config.TextColumn("职位", disabled=True, width="small"),
        "是否上社保": st.column_config.CheckboxColumn("上社保", disabled=True, width="small"),
        "是否处于试用期": st.column_config.CheckboxColumn("试用期", disabled=True, width="small"),
        "标准底薪": st.column_config.NumberColumn("标准底薪", disabled=True, format="¥%.2f", width="small"),
        "社保扣减额": st.column_config.NumberColumn("社保扣减", disabled=True, format="¥%.2f", width="small"),
        "调整后底薪": st.column_config.NumberColumn("调整后底薪", disabled=True, format="¥%.2f", width="small"),
        "出勤天数": st.column_config.NumberColumn("出勤天数", disabled=True, width="small"),
        
        # 可编辑列 (显式启用编辑)
        "全勤奖": st.column_config.NumberColumn("全勤奖 (可改)", min_value=0.0, format="¥%.2f", width="small", disabled=False),
        "绩效奖金": st.column_config.NumberColumn("绩效奖金 (可改)", min_value=0.0, format="¥%.2f", width="small", disabled=False),
        "加班小时数": st.column_config.NumberColumn("加班小时 (可改)", min_value=0.0, step=0.5, width="small", disabled=False),
        "手动福利": st.column_config.NumberColumn("手动福利 (可改)", min_value=0.0, format="¥%.2f", width="small", disabled=False),
        "投诉扣款": st.column_config.NumberColumn("客诉扣款 (可改)", min_value=0.0, format="¥%.2f", width="small", disabled=False),
        
        # 联动自动计算列
        "店铺平均人效": st.column_config.NumberColumn("日平均人效", disabled=True, format="¥%.1f", width="medium"),
        "加班费": st.column_config.NumberColumn("加班费 (自动)", disabled=True, format="¥%.2f", width="small"),
        "报损扣款_未免责": st.column_config.NumberColumn("分摊未免责", disabled=True, format="¥%.2f", width="small"),
        "报损扣款": st.column_config.NumberColumn("最终报损扣款", disabled=True, format="¥%.2f", width="medium"),
        "实发工资": st.column_config.NumberColumn("实发工资", disabled=True, format="¥%.2f", width="medium"),
    }
    
    # 挑选并排序显示
    cols_order = [
        "姓名", "分店", "职位", "是否上社保", "是否处于试用期", "标准底薪", "调整后底薪", 
        "全勤奖", "绩效奖金", "加班小时数", "加班费", "手动福利", "报损扣款", "投诉扣款", "实发工资"
    ]
    
    # 使用 st.data_editor
    edited_df = st.data_editor(
        df_payroll[cols_order],
        column_config=column_configuration,
        use_container_width=True,
        num_rows="fixed",
        key="payroll_editor"
    )
    
    # 检查是否有改动并重新计算
    # 为避免全量比较可能存在的微小浮点数差异，我们只对允许修改的列进行比较
    editable_cols = ["全勤奖", "绩效奖金", "加班小时数", "手动福利", "投诉扣款"]
    has_changed = False
    for col in editable_cols:
        if not edited_df[col].equals(df_payroll[col]):
            has_changed = True
            break
            
    if has_changed:
        # 将修改后的字段合并回原始花名册（以防有辅助列丢失）
        new_payroll_df = df_payroll.copy()
        for col in editable_cols:
            new_payroll_df[col] = edited_df[col]
            
        # 重新运行算薪逻辑
        recalculated_df = calculate_payroll(
            employees_df=new_payroll_df,
            loss_df=st.session_state.loss_df,
            store_sales=st.session_state.store_sales,
            social_security_std=st.session_state.social_security_std
        )
        
        # 存入全局状态并强制刷新
        st.session_state.payroll_df = recalculated_df
        st.rerun()

    st.divider()

    # ==================== 3. 报损分摊穿透查询 ====================
    st.markdown("### 🔍 个人报损扣款明细与学徒免责判定")
    st.caption("选择一位员工，查看其名下每一笔报损明细（含个人独担、店铺平摊）以及学徒免责条款执行细节。")
    
    # 取得报损明细大字典
    loss_details_dict = get_employee_loss_details(
        employees_df=df_payroll,
        loss_df=st.session_state.loss_df
    )
    
    selected_emp = st.selectbox("请选择要查询的员工姓名：", df_payroll["姓名"].tolist())
    
    if selected_emp:
        emp_row = df_payroll[df_payroll["姓名"] == selected_emp].iloc[0]
        emp_position = emp_row["职位"]
        emp_records = loss_details_dict.get(selected_emp, [])
        
        # 3.1 规则详情说明
        st.markdown(f"##### 👤 员工信息：**{selected_emp}** ({emp_position}) | 所属店铺：**{emp_row['分店']}**")
        
        if emp_position == '学徒':
            total_occurrences = len(emp_records)
            # 是否有一票否决情况
            veto_threshold = config.APPRENTICE_LOSS_RULES["SINGLE_VETO_THRESHOLD"]
            veto_records = [r for r in emp_records if r['gross_event_amount'] > veto_threshold]
            has_veto = len(veto_records) > 0
            
            st.markdown("**🛡️ 学徒免责规则匹配状态：**")
            if total_occurrences == 0:
                st.success("✅ 该学徒本月无报损记录。")
            else:
                st.write(f"- 本月累计报损次数：`{total_occurrences}` 次 (免责上限：`2` 次)")
                if has_veto:
                    st.error(f"- ❌ 触发 **一票否决**：存在单次金额超过 {veto_threshold} 元的巨大损耗（发生于 {veto_records[0]['date']}，金额: ¥{veto_records[0]['gross_event_amount']:.2f}），免责失效，需承担扣款。")
                elif total_occurrences > config.APPRENTICE_LOSS_RULES["MAX_FREE_COUNT"]:
                    st.error(f"- ❌ 累计报损次数（{total_occurrences}次）已超出单月免责上限（2次），免责失效，需全额承担扣款。")
                else:
                    st.success(f"- 🎉 满足免责条件（累计次数 {total_occurrences} <= 2，且单次未超 200 元），本月报损扣款已全额免除（实扣 0.0 元）！")
        else:
            st.info("ℹ️ 该员工职位为非学徒，不享受免责条款，报损扣款将全额照实扣除。")
            
        # 3.2 展现分摊表格
        if emp_records:
            display_records = []
            for r in emp_records:
                display_records.append({
                    "日期": r['date'],
                    "分店": r['store'],
                    "物品名称": r['item_name'],
                    "物品类别": r['item_type'],
                    "原始金额": r['raw_amount'],
                    "折算比例": "50%" if r['item_type'] == '成品' else "100%",
                    "折算后总损失": r['net_loss'],
                    "分摊类型": "店铺公摊 (责任人不明)" if r['is_shared'] else "个人全额自负",
                    "个人承担金额 (元)": r['share_amount']
                })
            rec_df = pd.DataFrame(display_records)
            st.dataframe(rec_df, use_container_width=True)
            
            # 显示合计
            subtotal_raw = sum(r['share_amount'] for r in emp_records)
            st.markdown(f"<div style='text-align: right; font-weight: bold;'>未免责前分摊合计：¥{subtotal_raw:.2f}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align: right; font-weight: bold; color: #C65911;'>免责后最终扣款金额：¥{emp_row['报损扣款']:.2f}</div>", unsafe_allow_html=True)
        else:
            st.write("👍 该员工本月没有产生任何个人或平摊的报损扣款。")
