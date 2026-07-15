"""
Excel/CSV 数据导入与导出模块
提供模板生成、输入数据校验、以及美化排版后的工资表导出功能。
"""

import os
import pandas as pd
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
import config

def generate_blank_templates(target_dir: str):
    """
    在指定目录下生成供用户填写的考勤与报损空白模板 Excel 文件
    """
    os.makedirs(target_dir, exist_ok=True)
    
    # 1. 考勤花名册模板
    attendance_cols = [
        "姓名", "分店", "职位", "是否上社保", "是否处于试用期", 
        "出勤天数", "加班小时数", "全勤奖", "绩效奖金", "手动福利", "投诉扣款"
    ]
    attendance_sample = [
        ["张三", "分店A", "店长", "否", "否", 30, 0, 400, 600, 0, 0],
        ["李四", "分店A", "后厨", "是", "否", 30, 15, 400, 600, 100, 0],
        ["王五", "分店A", "前台", "否", "是", 25, 0, 0, 300, 0, 50],
        ["赵六", "分店A", "学徒", "否", "否", 30, 0, 400, 0, 0, 0],
    ]
    attendance_df = pd.DataFrame(attendance_sample, columns=attendance_cols)
    attendance_path = os.path.join(target_dir, "attendance_template.xlsx")
    
    # 写入并简单调整格式
    with pd.ExcelWriter(attendance_path, engine="xlsxwriter") as writer:
        attendance_df.to_excel(writer, index=False, sheet_name="考勤花名册")
        workbook  = writer.book
        worksheet = writer.sheets["考勤花名册"]
        # 加点批注或提示说明
        worksheet.write_comment("C2", "可选值：店长、后厨、前台、学徒")
        worksheet.write_comment("D2", "只能填：是 / 否")
        worksheet.write_comment("E2", "只能填：是 / 否")
        worksheet.write_comment("G2", "仅后厨职位计加班费")
        # 设置列宽
        worksheet.set_column("A:K", 15)

    # 2. 报损明细模板
    loss_cols = ["日期", "分店", "责任人", "物品名称", "物品类别", "金额"]
    loss_sample = [
        ["2026-07-01", "分店A", "张三", "红豆吐司", "成品", 30.0],
        ["2026-07-02", "分店A", "公摊", "面粉", "原材料", 120.0],
        ["2026-07-03", "分店A", "赵六", "芝士蛋糕", "成品", 45.0],
        ["2026-07-04", "分店A", "赵六", "顶级巧克力", "原材料", 250.0], # 学徒单次 > 200 否决案例
    ]
    loss_df = pd.DataFrame(loss_sample, columns=loss_cols)
    loss_path = os.path.join(target_dir, "loss_template.xlsx")
    
    with pd.ExcelWriter(loss_path, engine="xlsxwriter") as writer:
        loss_df.to_excel(writer, index=False, sheet_name="报损明细登记")
        workbook  = writer.book
        worksheet = writer.sheets["报损明细登记"]
        worksheet.write_comment("C2", "填具体员工姓名；若责任人不明，请填：公摊、所有人、或者留空")
        worksheet.write_comment("E2", "只能填：成品、半成品、原材料")
        worksheet.write_comment("F2", "填写损毁物价值（成品填售价，原材料/半成品填原价）")
        worksheet.set_column("A:F", 15)

    return attendance_path, loss_path

def read_attendance_excel(file_path) -> pd.DataFrame:
    """
    读取并校验考勤花名册 Excel 文件
    """
    df = pd.read_excel(file_path)
    
    # 基础字段校验
    required_cols = ["姓名", "分店", "职位"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"考勤表导入失败：缺少必要列 【{col}】")
            
    # 填充默认值与缺失值
    df["是否上社保"] = df.get("是否上社保", "否").fillna("否")
    df["是否处于试用期"] = df.get("是否处于试用期", "否").fillna("否")
    df["出勤天数"] = df.get("出勤天数", 30).fillna(30).astype(float)
    df["加班小时数"] = df.get("加班小时数", 0.0).fillna(0.0).astype(float)
    df["全勤奖"] = df.get("全勤奖", pd.NA) # calculation 模块将处理空值
    df["绩效奖金"] = df.get("绩效奖金", pd.NA) # calculation 模块将处理空值
    df["手动福利"] = df.get("手动福利", 0.0).fillna(0.0).astype(float)
    df["投诉扣款"] = df.get("投诉扣款", 0.0).fillna(0.0).astype(float)
    
    # 校验职位类型
    valid_positions = set(config.DEFAULT_BASE_SALARY.keys())
    invalid_rows = df[~df["职位"].isin(valid_positions)]
    if not invalid_rows.empty:
        invalid_names = invalid_rows["姓名"].tolist()
        invalid_pos = invalid_rows["职位"].tolist()
        raise ValueError(
            f"职位录入有误：员工 {invalid_names} 的职位 {invalid_pos} 不在系统职位库 {list(valid_positions)} 中，请更正后重新上传！"
        )
        
    # 去除姓名两端空格
    df["姓名"] = df["姓名"].astype(str).str.strip()
    df["分店"] = df["分店"].astype(str).str.strip()
    
    # 检查姓名重复
    if df["姓名"].duplicated().any():
        dup_names = df[df["姓名"].duplicated()]["姓名"].tolist()
        raise ValueError(f"考勤表导入失败：存在重复的员工姓名 {dup_names}，请确保花名册名字唯一。")
        
    return df

def read_loss_excel(file_path) -> pd.DataFrame:
    """
    读取并校验报损登记 Excel 文件
    """
    df = pd.read_excel(file_path)
    
    # 基础字段校验
    required_cols = ["日期", "分店", "物品名称", "物品类别", "金额"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"报损表导入失败：缺少必要列 【{col}】")
            
    df["金额"] = df["金额"].fillna(0.0).astype(float)
    df["物品类别"] = df["物品类别"].fillna("成品").astype(str).str.strip()
    df["责任人"] = df.get("责任人", "").fillna("").astype(str).str.strip()
    
    # 过滤金额小于等于0的空记录
    df = df[df["金额"] > 0]
    
    # 校验物品类别
    valid_types = set(config.LOSS_DEDUCTION_RATES.keys())
    invalid_rows = df[~df["物品类别"].isin(valid_types)]
    if not invalid_rows.empty:
        raise ValueError(f"报损品类别录入错误：必须为 {list(valid_types)} 之一，请检查！")
        
    return df

def export_payroll_to_excel(
    payroll_df: pd.DataFrame, 
    loss_details_dict: dict, 
    output_path: str,
    store_sales: dict
):
    """
    导出精美格式化、多分包汇总的工资单 Excel
    包含：'薪资汇总表', '分店经营指标汇总', '报损扣款明细' 三个页签
    """
    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        workbook  = writer.book
        
        # -------------------- 常用单元格样式定义 --------------------
        # 头部样式 (深海蓝底，白字粗体)
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'vcenter',
            'align': 'center',
            'fg_color': '#1F4E78',
            'font_color': '#FFFFFF',
            'border': 1
        })
        
        # 普通数据行常规文本
        cell_format = workbook.add_format({
            'valign': 'vcenter',
            'align': 'center',
            'border': 1
        })
        
        # 数值型格式 (含千分位，保留两位小数)
        money_format = workbook.add_format({
            'num_format': '¥#,##0.00',
            'valign': 'vcenter',
            'align': 'right',
            'border': 1
        })
        
        # 实发工资高亮高对比样式 (淡橙底，暗红字，加粗)
        net_pay_format = workbook.add_format({
            'num_format': '¥#,##0.00',
            'bold': True,
            'valign': 'vcenter',
            'align': 'right',
            'fg_color': '#FCE4D6',
            'font_color': '#C65911',
            'border': 1
        })
        
        # 汇总行样式 (灰底粗体)
        total_format = workbook.add_format({
            'bold': True,
            'fg_color': '#F2F2F2',
            'border': 1,
            'valign': 'vcenter'
        })
        
        total_money_format = workbook.add_format({
            'bold': True,
            'num_format': '¥#,##0.00',
            'fg_color': '#F2F2F2',
            'border': 1,
            'valign': 'vcenter',
            'align': 'right'
        })
        
        # -------------------- 页签 1：薪资汇总表 --------------------
        # 挑选输出列并重新排列，隐藏过于底层的字段，凸显关键核算步骤
        cols_to_export = [
            "姓名", "分店", "职位", "是否上社保", "是否处于试用期", 
            "标准底薪", "社保扣减额", "调整后底薪", "出勤天数", 
            "全勤奖", "绩效奖金", "加班小时数", "加班费", 
            "手动福利", "报损扣款", "投诉扣款", "实发工资"
        ]
        
        # 转换布尔值为可读的“是/否”
        display_df = payroll_df[cols_to_export].copy()
        display_df["是否上社保"] = display_df["是否上社保"].map({True: "是", False: "否"})
        display_df["是否处于试用期"] = display_df["是否处于试用期"].map({True: "是", False: "否"})
        
        # 写入 Sheet
        sheet_name_summary = "薪资汇总表"
        display_df.to_excel(writer, sheet_name=sheet_name_summary, index=False)
        worksheet_summary = writer.sheets[sheet_name_summary]
        
        # 应用样式
        num_rows = len(display_df)
        num_cols = len(display_df.columns)
        
        # 覆盖表头
        for col_num, col_name in enumerate(display_df.columns):
            worksheet_summary.write(0, col_num, col_name, header_format)
            
        # 填充数据区样式
        for r in range(num_rows):
            row_idx = r + 1
            for c in range(num_cols):
                val = display_df.iloc[r, c]
                col_name = display_df.columns[c]
                
                # 如果是实发工资那一列，高亮显示
                if col_name == "实发工资":
                    worksheet_summary.write(row_idx, c, val, net_pay_format)
                # 金额数值型列应用货币格式
                elif col_name in ["标准底薪", "社保扣减额", "调整后底薪", "全勤奖", "绩效奖金", "加班费", "手动福利", "报损扣款", "投诉扣款"]:
                    worksheet_summary.write(row_idx, c, val, money_format)
                else:
                    worksheet_summary.write(row_idx, c, val, cell_format)
                    
        # 写入底部“总计”行
        total_row_idx = num_rows + 1
        worksheet_summary.write(total_row_idx, 0, "总计", total_format)
        for c in range(1, num_cols):
            col_name = display_df.columns[c]
            # 对金额类列进行求和
            if col_name in ["标准底薪", "社保扣减额", "调整后底薪", "全勤奖", "绩效奖金", "加班费", "手动福利", "报损扣款", "投诉扣款", "实发工资"]:
                col_letter = chr(65 + c) # 仅针对 A-Z (本列共17列在A-Q范围，无溢出)
                formula = f"=SUM({col_letter}2:{col_letter}{total_row_idx})"
                worksheet_summary.write_formula(total_row_idx, c, formula, total_money_format)
            elif col_name in ["加班小时数", "出勤天数"]:
                col_letter = chr(65 + c)
                formula = f"=SUM({col_letter}2:{col_letter}{total_row_idx})"
                worksheet_summary.write_formula(total_row_idx, c, formula, total_format)
            else:
                worksheet_summary.write(total_row_idx, c, "", total_format)
                
        # 设置行高与列宽
        worksheet_summary.set_row(0, 26)
        worksheet_summary.set_column("A:Q", 14)
        
        # -------------------- 页签 2：分店经营与人效汇总 --------------------
        store_summary_data = []
        for store, sales in store_sales.items():
            store_df = payroll_df[payroll_df["分店"] == store]
            emp_count = len(store_df)
            total_net_payroll = store_df["实发工资"].sum()
            total_loss = store_df["报损扣款"].sum()
            avg_eff = store_df["店铺平均人效"].iloc[0] if emp_count > 0 else 0.0
            
            store_summary_data.append({
                "分店名称": store,
                "当月总营业额 (元)": sales,
                "在职员工人数 (人)": emp_count,
                "店铺日平均人效 (元/人/天)": avg_eff,
                "是否发放后厨加班费": "是" if avg_eff > config.OVERTIME_RULES["EFFICIENCY_THRESHOLD"] else "否 (人效未达标)",
                "分店薪资实发总额 (元)": total_net_payroll,
                "分店总承担报损扣款 (元)": total_loss
            })
            
        store_summary_df = pd.DataFrame(store_summary_data)
        sheet_name_store = "分店经营指标汇总"
        store_summary_df.to_excel(writer, sheet_name=sheet_name_store, index=False)
        worksheet_store = writer.sheets[sheet_name_store]
        
        # 渲染样式
        for col_num, col_name in enumerate(store_summary_df.columns):
            worksheet_store.write(0, col_num, col_name, header_format)
            
        for r in range(len(store_summary_df)):
            row_idx = r + 1
            for c in range(len(store_summary_df.columns)):
                val = store_summary_df.iloc[r, c]
                col_name = store_summary_df.columns[c]
                
                if col_name in ["当月总营业额 (元)", "店铺日平均人效 (元/人/天)", "分店薪资实发总额 (元)", "分店总承担报损扣款 (元)"]:
                    worksheet_store.write(row_idx, c, val, money_format)
                else:
                    worksheet_store.write(row_idx, c, val, cell_format)
                    
        worksheet_store.set_row(0, 26)
        worksheet_store.set_column("A:G", 22)
        
        # -------------------- 页签 3：报损分摊明细表 --------------------
        loss_details_rows = []
        for emp_name, records in loss_details_dict.items():
            for rec in records:
                loss_details_rows.append({
                    "被分摊员工": emp_name,
                    "日期": rec['date'],
                    "所属分店": rec['store'],
                    "物品名称": rec['item_name'],
                    "物品类别": rec['item_type'],
                    "原始金额": rec['raw_amount'],
                    "折算后总损失": rec['net_loss'],
                    "分摊承担金额": rec['share_amount'],
                    "是否公摊": "公摊 (责任人不明)" if rec['is_shared'] else f"个人自负 (责任人: {emp_name})"
                })
                
        loss_details_df = pd.DataFrame(loss_details_rows)
        sheet_name_loss = "报损扣款明细"
        
        if not loss_details_df.empty:
            loss_details_df.to_excel(writer, sheet_name=sheet_name_loss, index=False)
            worksheet_loss = writer.sheets[sheet_name_loss]
            
            # 格式化
            for col_num, col_name in enumerate(loss_details_df.columns):
                worksheet_loss.write(0, col_num, col_name, header_format)
                
            for r in range(len(loss_details_df)):
                row_idx = r + 1
                for c in range(len(loss_details_df.columns)):
                    val = loss_details_df.iloc[r, c]
                    col_name = loss_details_df.columns[c]
                    
                    if col_name in ["原始金额", "折算后总损失", "分摊承担金额"]:
                        worksheet_loss.write(row_idx, c, val, money_format)
                    else:
                        worksheet_loss.write(row_idx, c, val, cell_format)
                        
            worksheet_loss.set_row(0, 26)
            worksheet_loss.set_column("A:I", 16)
        else:
            # 写入空白提醒
            worksheet_loss = workbook.add_worksheet(sheet_name_loss)
            worksheet_loss.write(0, 0, "当月无报损记录发生，扣款为零。", cell_format)
            worksheet_loss.set_column("A:A", 30)
            
    return output_path
