"""读取 PostgreSQL 月度草稿；本阶段不写入或确认工资。"""
from datetime import date

import pandas as pd
import psycopg
from psycopg.rows import dict_row


def load_draft_inputs(database_url: str, *, store_name: str,
                      payroll_month: date, employee_code: str | None = None):
    """读当月快照并转换为计算引擎的列名，不读取员工当前约定金额。"""
    if not database_url.strip():
        raise ValueError("请先设置 DATABASE_URL 数据库连接地址。")
    if payroll_month.day != 1:
        raise ValueError("工资月份必须使用当月 1 日。")

    query = """
        SELECT
            d.employee_code_snapshot AS "员工编号",
            d.employee_name_snapshot AS "姓名",
            s.name AS "分店",
            d.job_title_snapshot AS "岗位",
            d.grade_snapshot AS "职级",
            d.hire_date_snapshot AS "入职时间",
            d.salary_snapshot AS "工资",
            d.performance_snapshot AS "绩效",
            d.attendance_bonus_snapshot AS "全勤",
            d.commute_allowance_snapshot AS "通勤补贴",
            d.ordinary_absence_days AS "普通缺勤天数",
            d.unauthorized_absence_days AS "旷工天数",
            d.late_minutes AS "迟到",
            d.overtime_hours AS "有效加班时长",
            d.work_error_notes AS "工作失误项目"
        FROM public.payroll_entries AS d
        JOIN public.payroll_months AS p ON d.payroll_month_id = p.id
        JOIN public.stores AS s ON p.store_id = s.id
        WHERE s.name = %s AND p.payroll_month = %s AND p.status = 'draft'
    """
    parameters = [store_name, payroll_month]
    if employee_code is not None:
        query += ' AND d.employee_code_snapshot = %s'
        parameters.append(employee_code)
    query += ' ORDER BY d.id'

    # 参数交给驱动绑定，避免把姓名等用户输入拼成 SQL 代码。
    # 只读事务保护本次预览；它不替代将来的数据库账号权限。
    with psycopg.connect(database_url, connect_timeout=5,
                         options='-c default_transaction_read_only=on',
                         row_factory=dict_row) as connection:
        records = connection.execute(query, parameters).fetchall()
    if not records:
        raise ValueError("未找到符合门店、月份和员工条件的草稿明细；已确认月份不在此预览。")
    return pd.DataFrame.from_records(records)
