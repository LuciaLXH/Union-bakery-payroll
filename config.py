"""本轮已确认的核算常量；员工金额由输入提供。"""
from decimal import Decimal

RULE_VERSION = "2026-10-02-v1"
MONTH_DAYS = Decimal("30")
DAILY_HOURS = Decimal("9")
OVERTIME_MULTIPLIER = Decimal("1.5")
ABSENCE_SALARY_MULTIPLIER = Decimal("3")
LATE_PER_MINUTE = Decimal("1")
CENT = Decimal("0.01")
REQUIRED_COLUMNS = ("姓名", "工资", "绩效", "全勤")
MONEY_COLUMNS = ("工资", "绩效", "全勤", "通勤补贴")
COUNT_COLUMNS = ("普通缺勤天数", "旷工天数", "迟到", "有效加班时长")
INPUT_COLUMNS = (
    "员工编号", "姓名", "分店", "岗位", "职级", "入职时间",
    "工资", "绩效", "全勤", "通勤补贴", "普通缺勤天数",
    "旷工天数", "迟到", "有效加班时长", "工作失误项目",
)
REFERENCE_COLUMNS = ("原表加班费", "原表合计")
TEMPLATE_COLUMNS = (
    "姓名", "通勤补贴", "旷工天数", "工资", "绩效", "全勤",
    "有效加班时长", "加班费", "迟到", "工作失误项目", "合计",
    "入职时间", "普通缺勤天数", "分店", "员工编号", "岗位", "职级",
)
