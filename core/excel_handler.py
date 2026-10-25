"""新表头的 Excel 适配。导入金额只作对账，导出在内存中生成。"""
from io import BytesIO
import pandas as pd
import config
from core.payroll_calc import normalize_inputs


def example_inputs():
    """八个虚构算例，不读取或混入真实员工资料。"""
    changes = [
        {}, {"普通缺勤天数": 1}, {"旷工天数": 1}, {"迟到": 7},
        {"有效加班时长": 2}, {"通勤补贴": 200},
        {"职级": "零基础学徒", "工作失误项目": "虚构示例：整形需加强练习，仅作备注"},
        {"普通缺勤天数": 1, "旷工天数": 1},
    ]
    expected = [5400, 5220, 4920, 5393, 5450, 5600, 5400, 4740]
    records = []
    for i, (change, total) in enumerate(zip(changes, expected), 1):
        record = {"员工编号": f"DEMO-{i:03d}", "姓名": f"虚构员工{i}", "分店": "示例门店",
                  "工资": 4500, "绩效": 600, "全勤": 300, "原表合计": total}
        record.update(change)
        records.append(record)
    return normalize_inputs(pd.DataFrame(records))


def read_attendance_excel(file, default_store="未分店"):
    """读取第一个工作表。行号含表头；不自动删除错误行。"""
    with pd.ExcelFile(file) as workbook:
        header = pd.read_excel(workbook, header=None, nrows=1)
        names = [] if header.empty else [str(v).strip() for v in header.iloc[0] if pd.notna(v)]
        if len(names) != len(set(names)):
            raise ValueError("第一行存在重复表头，请修改后重新导入")
        df = pd.read_excel(workbook, dtype={"员工编号": str})
    df.columns = [str(c).strip() for c in df.columns]
    ignored = [str(c) for c in df if c not in config.TEMPLATE_COLUMNS]
    # 原表输出永远不能成为下一次核算的输入金额。
    if any(c in df for c in config.REFERENCE_COLUMNS):
        raise ValueError("请使用工资录入模板；原表加班费、原表合计为系统保留列")
    df = df.rename(columns={"加班费": "原表加班费", "合计": "原表合计"})
    normalized = normalize_inputs(df, default_store=default_store)
    normalized.attrs["ignored_columns"] = ignored
    return normalized


def _write_sheet(writer, df, sheet_name):
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    sheet = writer.sheets[sheet_name]
    book = writer.book
    header = book.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "text_wrap": True})
    money = book.add_format({"num_format": "0.00"})
    text = book.add_format({"num_format": "@"})
    money_cols = set(config.MONEY_COLUMNS) | {"加班费", "合计", "应发合计", "扣款合计", "工资扣款", "绩效扣款", "全勤扣款", "迟到扣款", "原表合计", "原表加班费", "合计差额", "加班费差额"}
    for col, name in enumerate(df.columns):
        sheet.write(0, col, name, header)
        width = 42 if name in ("工作失误项目", "核算提示") else 18
        sheet.set_column(col, col, width, money if name in money_cols else text if name == "员工编号" else None)
    sheet.set_row(0, 32)
    sheet.freeze_panes(1, 2)
    if len(df):
        sheet.autofilter(0, 0, len(df), len(df.columns) - 1)
    return sheet


def _writer(buffer):
    return pd.ExcelWriter(buffer, engine="xlsxwriter", engine_kwargs={"options": {
        "strings_to_formulas": False, "strings_to_urls": False,
    }})


def create_attendance_template(sample=False):
    """每次按新表头生成，不复用磁盘上的旧版模板。"""
    buffer = BytesIO()
    if sample:
        df = example_inputs().rename(columns={"原表合计": "合计", "原表加班费": "加班费"})
        df = df.reindex(columns=config.TEMPLATE_COLUMNS)
    else:
        df = pd.DataFrame(columns=config.TEMPLATE_COLUMNS)
    with _writer(buffer) as writer:
        sheet = _write_sheet(writer, df, "工资录入")
        comments = {
            "工资": "必填，基本工资加社保补贴，不要先扣考勤。金额最多两位小数。",
            "绩效": "必填，扣缺勤前的月绩效；没有请填0。",
            "全勤": "必填，扣缺勤前的月全勤；没有请填0。",
            "通勤补贴": "本月实际应发金额，不再自动按缺勤折算。空白按0。",
            "普通缺勤天数": "整天数；工资、绩效、全勤每天各扣1/30。空白按0。",
            "旷工天数": "整天数；每天扣3天工资，绩效及全勤扣当天。不与普通缺勤重复。空白按0。",
            "迟到": "单位为分钟，整数；每分钟扣1元。空白按0。",
            "有效加班时长": "单位为小时，可填小数；空白按0。工资÷30÷9×1.5×小时数。",
            "加班费": "可留空；如填写，只与系统计算金额对账。",
            "合计": "可留空；如填写，只与系统最终实发对账。",
            "工作失误项目": "只记录文字，不自动扣款。",
            "员工编号": "可选，建议填唯一编号；缺失时仅生成当前批次临时编号。",
            "入职时间": "可选，日期如2026-09-01；本版不自动折算首月工资。",
        }
        for col, name in enumerate(df.columns):
            if name in comments:
                sheet.write_comment(0, col, comments[name])
        instructions = pd.DataFrame([
            {"项目": "用途", "说明": "此文件全部为虚构示例，请勿混入正式员工数据。" if sample else "第一行是表头，从第二行开始填写；姓名、工资、绩效、全勤必填。"},
            {"项目": "考勤", "说明": "普通缺勤和旷工不重复录入，合计不超过30天。本版只支持整天。"},
            {"项目": "可选金额", "说明": "通勤补贴、考勤数值未填按0。工资、绩效、全勤空白会报错，不自动补奖金。"},
            {"项目": "范围", "说明": "不计算独立社保扣款、试用期折扣、报损扣款或营业额提成。"},
        ])
        _write_sheet(writer, instructions, "填写说明").set_column(1, 1, 100)
    return buffer.getvalue()


def export_payroll_to_excel(payroll_df):
    """Export the result's own rules and confirmation state, including locked history."""
    if payroll_df is None or payroll_df.empty:
        raise ValueError("没有可导出的核算结果")
    if payroll_df["核算提示"].ne("").any():
        raise ValueError("存在异常核算结果，请修正后再导出")
    buffer = BytesIO()
    with _writer(buffer) as writer:
        _write_sheet(writer, payroll_df, "核算明细")
        _write_sheet(writer, pd.DataFrame([
            {"项目": "规则版本", "说明": '、'.join(map(str, payroll_df['规则版本'].unique()))
             if '规则版本' in payroll_df else config.RULE_VERSION},
            {"项目": "金额口径", "说明": "工资已含社保补贴；绩效和全勤为扣缺勤前金额；合计为实发。"},
            {"项目": "状态", "说明": ('、'.join(payroll_df['确认状态'].unique())
             if '确认状态' in payroll_df else '核算草稿（未确认）') + '。本系统不执行转账，发放状态仅为人工标记。'},
        ]), "核算说明").set_column(1, 1, 90)
    return buffer.getvalue()
