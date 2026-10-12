"""与界面、数据库无关的输入校验和十进制工资核算。"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import pandas as pd
import config


class PayrollValidationError(ValueError):
    """保留所有行级错误，供页面一次展示。"""
    def __init__(self, issues):
        self.issues = issues
        super().__init__("；".join(
            f"第{i['行号']}行【{i['字段']}】{i['问题']}" for i in issues
        ))


def _empty(value):
    return pd.isna(value) or (isinstance(value, str) and not value.strip())


def _text(value):
    return "" if _empty(value) else str(value).strip()


def _number(value, *, required=False, money=False, integer=False):
    if _empty(value):
        if required:
            raise ValueError("请填写金额；没有该项时请明确填 0")
        return Decimal(0)
    if isinstance(value, bool):
        raise ValueError("请填写数字，不能填写是或否")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("必须是有效数字") from None
    if not number.is_finite():
        raise ValueError("必须是有限数字")
    if number < 0:
        raise ValueError("不能小于 0")
    # 表格支持的数值范围，不是业务发薪上限。
    if number > Decimal("999999999.99"):
        raise ValueError("超出支持范围 999999999.99")
    if money and number != number.quantize(config.CENT):
        raise ValueError("金额最多保留两位小数")
    if integer and number != number.to_integral_value():
        raise ValueError("本版请填写整数；半天或不足一分钟的规则尚未确认")
    return number


def normalize_inputs(employees_df: pd.DataFrame, default_store="未分店") -> pd.DataFrame:
    """仅保留新规则输入；可选数值缺省为 0，工资绩效全勤必须明确。"""
    if not isinstance(employees_df, pd.DataFrame):
        raise TypeError("员工输入必须是 DataFrame")
    df = employees_df.copy(deep=True)
    df.columns = [str(c).strip() for c in df.columns]
    issues = []
    if df.columns.duplicated().any():
        raise PayrollValidationError([{"行号": 1, "字段": "表头", "问题": "存在重复列名"}])
    for col in config.REQUIRED_COLUMNS:
        if col not in df:
            issues.append({"行号": 1, "字段": col, "问题": "缺少必填列"})
    if df.empty:
        issues.append({"行号": 2, "字段": "数据", "问题": "至少需要一名员工"})
    if issues:
        raise PayrollValidationError(issues)

    rows = []
    seen_ids = set()
    supplied_ids = {_text(v) for v in df.get("员工编号", pd.Series(dtype=str)) if not _empty(v)}
    for offset, (_, row) in enumerate(df.iterrows(), start=2):
        record = {}
        for col in ("姓名", "分店", "岗位", "职级", "工作失误项目"):
            record[col] = _text(row.get(col, ""))
        if not record["姓名"]:
            issues.append({"行号": offset, "字段": "姓名", "问题": "不能为空"})
        record["分店"] = record["分店"] or _text(default_store) or "未分店"
        employee_id = _text(row.get("员工编号", ""))
        if not employee_id:
            employee_id = f"临时-{offset - 1:04d}"
            while employee_id in supplied_ids or employee_id in seen_ids:
                employee_id += "-"
        if employee_id in seen_ids:
            issues.append({"行号": offset, "字段": "员工编号", "问题": "存在重复编号"})
        seen_ids.add(employee_id)
        record["员工编号"] = employee_id

        date = row.get("入职时间", "")
        record["入职时间"] = ""
        if not _empty(date):
            try:
                if isinstance(date, (int, float)):
                    raise ValueError
                parsed = pd.to_datetime(str(date), errors="raise")
                if pd.isna(parsed):
                    raise ValueError
                record["入职时间"] = parsed.strftime("%Y-%m-%d")
            except (ValueError, TypeError, OverflowError):
                issues.append({"行号": offset, "字段": "入职时间", "问题": "请填写有效日期，例如 2026-09-01"})

        for col in config.MONEY_COLUMNS + config.COUNT_COLUMNS:
            try:
                number = _number(
                    row.get(col, None), required=col in ("工资", "绩效", "全勤"),
                    money=col in config.MONEY_COLUMNS,
                    integer=col in ("普通缺勤天数", "旷工天数", "迟到"),
                )
                record[col] = float(number)
            except ValueError as exc:
                record[col] = 0.0
                issues.append({"行号": offset, "字段": col, "问题": str(exc)})
        if record["普通缺勤天数"] + record["旷工天数"] > 30:
            issues.append({"行号": offset, "字段": "缺勤天数", "问题": "普通缺勤与旷工合计不能超过 30 天，同一天不能重复计入"})

        for col in config.REFERENCE_COLUMNS:
            record[col] = None
            if col in df and not _empty(row[col]):
                try:
                    record[col] = float(_number(row[col], money=True))
                except ValueError as exc:
                    issues.append({"行号": offset, "字段": col, "问题": str(exc)})
        rows.append(record)
    if issues:
        raise PayrollValidationError(issues)
    return pd.DataFrame(rows, columns=config.INPUT_COLUMNS + config.REFERENCE_COLUMNS)


def _money(value):
    return value.quantize(config.CENT, rounding=ROUND_HALF_UP)


def calculate_payroll(employees_df: pd.DataFrame, *, decimal_output=False) -> pd.DataFrame:
    """输出逐项扣款及合计；数据库保存可选择 Decimal 金额，网页默认 float。"""
    df = normalize_inputs(employees_df)
    results = []
    for _, row in df.iterrows():
        with localcontext() as context:
            context.prec = 40
            w, p, a, c = (Decimal(str(row[col])) for col in config.MONEY_COLUMNS)
            n, m, t, h = (Decimal(str(row[col])) for col in config.COUNT_COLUMNS)
            amounts = {
                "工资扣款": _money(w * (n + config.ABSENCE_SALARY_MULTIPLIER * m) / config.MONTH_DAYS),
                "绩效扣款": _money(p * (n + m) / config.MONTH_DAYS),
                "全勤扣款": _money(a * (n + m) / config.MONTH_DAYS),
                "迟到扣款": _money(t * config.LATE_PER_MINUTE),
                "加班费": _money(w * config.OVERTIME_MULTIPLIER * h / (config.MONTH_DAYS * config.DAILY_HOURS)),
            }
            amounts["应发合计"] = _money(w + p + a + c + amounts["加班费"])
            amounts["扣款合计"] = sum(amounts[col] for col in ("工资扣款", "绩效扣款", "全勤扣款", "迟到扣款"))
            amounts["合计"] = amounts["应发合计"] - amounts["扣款合计"]
            record = row.to_dict()
            record.update({col: value if decimal_output else float(value)
                           for col, value in amounts.items()})
            record["核算提示"] = "实发金额为负，请核对输入和扣款，暂不能导出" if amounts["合计"] < 0 else ""
            for original, computed, difference in (
                ("原表合计", "合计", "合计差额"),
                ("原表加班费", "加班费", "加班费差额"),
            ):
                record[difference] = None if pd.isna(row[original]) else float(amounts[computed] - Decimal(str(row[original])))
            results.append(record)
    result = pd.DataFrame(results)
    result.attrs["rule_version"] = config.RULE_VERSION
    return result
