"""English presentation boundary. The original payroll rules remain unchanged."""
from io import BytesIO
import pandas as pd

from core.payroll_calc import PayrollValidationError

LABELS = {
    '员工编号': 'Employee ID', '姓名': 'Name', '分店': 'Store', '岗位': 'Role',
    '职级': 'Grade', '入职时间': 'Start date', '工资': 'Base pay incl. allowance',
    '绩效': 'Performance pay', '全勤': 'Attendance bonus', '通勤补贴': 'Travel allowance',
    '普通缺勤天数': 'Absence days', '旷工天数': 'Unauthorised absence days',
    '迟到': 'Late minutes', '有效加班时长': 'Overtime hours', '工作失误项目': 'Performance notes',
    '工资扣款': 'Base pay deduction', '绩效扣款': 'Performance deduction',
    '全勤扣款': 'Attendance deduction', '迟到扣款': 'Late deduction', '加班费': 'Overtime pay',
    '应发合计': 'Gross pay', '扣款合计': 'Total deductions', '合计': 'Net pay',
    '工资月份': 'Pay month', '输入版本': 'Input version', '规则版本': 'Rule version',
    '核算提示': 'Validation note', '入职日期': 'Start date', '在职': 'Active',
    '约定工资': 'Base pay incl. allowance', '月绩效': 'Performance pay', '月全勤': 'Attendance bonus',
}
ISSUES = {
    '不能为空': 'is required', '存在重复编号': 'must be unique',
    '请填写金额；没有该项时请明确填 0': 'enter an amount, or 0 if not applicable',
    '请填写数字，不能填写是或否': 'enter a number, not a boolean',
    '必须是有效数字': 'enter a valid number', '必须是有限数字': 'enter a finite number',
    '不能小于 0': 'cannot be negative', '超出支持范围 999999999.99': 'exceeds 999,999,999.99',
    '金额最多保留两位小数': 'use at most two decimal places',
    '本版请填写整数；半天或不足一分钟的规则尚未确认': 'use whole days or whole minutes',
    '普通缺勤与旷工合计不能超过 30 天，同一天不能重复计入': 'absence types cannot overlap or total more than 30 days',
    '请填写有效日期，例如 2026-09-01': 'enter a valid date, e.g. 2026-09-01',
    '至少需要一名员工': 'add at least one employee', '缺少必填列': 'required column is missing',
    '存在重复列名': 'column names must be unique',
}


def english_frame(frame):
    return frame.rename(columns=LABELS)


def validation_message(exc):
    if isinstance(exc, PayrollValidationError):
        return '\n'.join(f"Row {i['行号'] - 1} · {LABELS.get(i['字段'], 'Input')}: "
                         f"{ISSUES.get(i['问题'], 'check this value')}" for i in exc.issues)
    # Never expose untranslated database errors or technical details in the public demo.
    message = str(exc)
    if any('\u4e00' <= c <= '\u9fff' for c in message):
        return 'The change could not be saved. Check required fields, unique employee IDs, amounts and attendance. Reload if another edit changed this record.'
    return message


def export_english(results):
    from core.workspace import result_frame
    output = BytesIO()
    frame = english_frame(result_frame(results)).drop(columns=['Validation note'], errors='ignore')
    with pd.ExcelWriter(output, engine='xlsxwriter', engine_kwargs={
        'options': {'strings_to_formulas': False, 'strings_to_urls': False}}) as writer:
        frame.to_excel(writer, sheet_name='Payroll detail', index=False)
        pd.DataFrame([
            ('Currency', 'CNY. Translation does not change the original store rules.'),
            ('Purpose', 'Synthetic demonstration. Not a payment instruction or statutory payslip.'),
            ('Rounding', 'Decimal arithmetic; each component rounded half up to two decimal places.'),
            ('Notes', 'Performance notes never create a deduction.'),
        ], columns=['Item', 'Explanation']).to_excel(writer, sheet_name='Read me', index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes(1, 0)
            sheet.set_column(0, len(frame.columns) - 1, 22)
    return output.getvalue()
