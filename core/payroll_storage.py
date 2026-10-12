"""草稿核算结果的事务保存和版本查询；不负责确认发薪。"""
from datetime import date, datetime
from decimal import Decimal
import json

import pandas as pd
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import config
from core.payroll_calc import calculate_payroll


INPUT_FIELDS = {
    'employee_code_snapshot': '员工编号', 'employee_name_snapshot': '姓名',
    'job_title_snapshot': '岗位', 'grade_snapshot': '职级',
    'hire_date_snapshot': '入职时间', 'salary_snapshot': '工资',
    'performance_snapshot': '绩效', 'attendance_bonus_snapshot': '全勤',
    'commute_allowance_snapshot': '通勤补贴', 'ordinary_absence_days': '普通缺勤天数',
    'unauthorized_absence_days': '旷工天数', 'late_minutes': '迟到',
    'overtime_hours': '有效加班时长', 'work_error_notes': '工作失误项目',
}
RESULT_FIELDS = {
    'salary_deduction': '工资扣款', 'performance_deduction': '绩效扣款',
    'attendance_deduction': '全勤扣款', 'late_deduction': '迟到扣款',
    'overtime_pay': '加班费', 'gross_pay': '应发合计',
    'total_deductions': '扣款合计', 'net_pay': '合计',
}


def _json_default(value):
    # JSON 金额用十进制字符串保存，避免转换为浮点数丢失分位。
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f'不支持的输入快照类型：{type(value).__name__}')


def _snapshot_json(value):
    return json.dumps(value, default=_json_default, ensure_ascii=False, allow_nan=False)


class PayrollStorage:
    """schema 参数供隔离集成测试使用；正式调用默认 public。

    保存入口先锁月份再锁明细。连接由调用方管理；如果调用方已有事务，
    save() 使用保存点，最终持久化仍需调用方提交外层事务。
    """

    def __init__(self, connection, *, schema='public'):
        self.connection = connection
        self.tables = {name: sql.Identifier(schema, name) for name in (
            'stores', 'payroll_months', 'payroll_entries', 'payroll_calculations')}

    def _rows(self, statement, parameters=()):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql.SQL(statement).format(**self.tables), parameters)
            return cursor.fetchall()

    def _month(self, store_name, payroll_month, *, lock=False):
        if payroll_month.day != 1:
            raise ValueError('工资月份必须使用当月 1 日。')
        query = '''SELECT p.*, s.name AS store_name
                   FROM {payroll_months} p JOIN {stores} s ON s.id=p.store_id
                   WHERE s.name=%s AND p.payroll_month=%s'''
        if lock:
            query += ' FOR UPDATE OF p'
        rows = self._rows(query, (store_name, payroll_month))
        if not rows:
            raise ValueError('未找到指定门店的月度工资记录。')
        return rows[0]

    def save(self, *, store_name, payroll_month, employee_code=None):
        """按当前规则核算并保存；相同输入版本与规则复用原结果，不覆盖历史。"""
        rule_version = config.RULE_VERSION
        with self.connection.transaction():
            month = self._month(store_name, payroll_month, lock=True)
            if month['status'] != 'draft':
                raise ValueError('已确认月份不能通过草稿保存入口重新核算。')
            query = 'SELECT d.* FROM {payroll_entries} d WHERE d.payroll_month_id=%s'
            parameters = [month['id']]
            if employee_code is not None:
                query += ' AND d.employee_code_snapshot=%s'
                parameters.append(employee_code)
            entries = self._rows(query + ' ORDER BY d.id FOR UPDATE OF d', parameters)
            if not entries:
                raise ValueError('未找到符合条件的员工月度明细。')

            saved = []
            for entry in entries:
                existing = self._rows('''SELECT * FROM {payroll_calculations}
                    WHERE payroll_entry_id=%s AND input_version=%s AND rule_version=%s''',
                    (entry['id'], entry['input_version'], rule_version))
                if existing:
                    if existing[0]['net_pay'] < 0:
                        raise ValueError('已存结果的实发金额为负，请先核对输入。')
                    record = existing[0]
                    record['reused'] = True
                else:
                    inputs = {column: entry[field] for field, column in INPUT_FIELDS.items()}
                    inputs['分店'] = month['store_name']
                    calculated = calculate_payroll(
                        pd.DataFrame([inputs]), decimal_output=True).iloc[0]
                    if calculated['核算提示']:
                        raise ValueError(f"员工 {entry['employee_code_snapshot']}："
                                         f"{calculated['核算提示']}；本次保存全部回滚。")
                    snapshot = dict(entry)
                    snapshot.update(payroll_month=month['payroll_month'],
                                    store_id=month['store_id'], store_name=month['store_name'])
                    values = [entry['id'], entry['input_version'], rule_version,
                              Jsonb(snapshot, dumps=_snapshot_json)]
                    values.extend(calculated[column] for column in RESULT_FIELDS.values())
                    record = self._rows('''INSERT INTO {payroll_calculations} (
                        payroll_entry_id, input_version, rule_version, input_snapshot,
                        salary_deduction, performance_deduction, attendance_deduction,
                        late_deduction, overtime_pay, gross_pay, total_deductions, net_pay)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                        values)[0]
                    record['reused'] = False
                record['employee_code'] = entry['employee_code_snapshot']
                saved.append(record)
        return saved

    def status(self, *, store_name, payroll_month, employee_code=None):
        """用一条查询比较当前输入与已存结果，避免把旧结果显示为当前工资。"""
        if payroll_month.day != 1:
            raise ValueError('工资月份必须使用当月 1 日。')
        query = '''SELECT d.id AS payroll_entry_id,
            d.employee_code_snapshot AS employee_code, d.input_version,
            p.status AS month_status, c.id AS calculation_id, c.rule_version,
            c.net_pay, c.calculated_at,
            CASE WHEN c.id IS NOT NULL AND c.net_pay < 0 THEN '核算异常'
                 WHEN c.id IS NOT NULL THEN '已核算'
                 WHEN EXISTS (SELECT 1 FROM {payroll_calculations} old
                              WHERE old.payroll_entry_id=d.id) THEN '待重新核算'
                 ELSE '待核算' END AS calculation_status
            FROM {payroll_entries} d
            JOIN {payroll_months} p ON p.id=d.payroll_month_id
            JOIN {stores} s ON s.id=p.store_id
            LEFT JOIN {payroll_calculations} c ON c.payroll_entry_id=d.id
                AND c.input_version=d.input_version AND c.rule_version=%s
            WHERE s.name=%s AND p.payroll_month=%s'''
        parameters = [config.RULE_VERSION, store_name, payroll_month]
        if employee_code is not None:
            query += ' AND d.employee_code_snapshot=%s'
            parameters.append(employee_code)
        rows = self._rows(query + ' ORDER BY d.id', parameters)
        if not rows:
            raise ValueError('未找到符合条件的员工月度明细。')
        return rows

    def history(self, *, store_name, payroll_month, employee_code=None):
        month = self._month(store_name, payroll_month)
        query = '''SELECT c.* FROM {payroll_calculations} c
                   JOIN {payroll_entries} d ON d.id=c.payroll_entry_id
                   WHERE d.payroll_month_id=%s'''
        parameters = [month['id']]
        if employee_code is not None:
            query += ' AND d.employee_code_snapshot=%s'
            parameters.append(employee_code)
        return self._rows(query + ' ORDER BY c.payroll_entry_id, c.id', parameters)
