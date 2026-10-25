"""网页工作区：员工档案、按月建立草稿、带版本检查的输入保存。"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

import pandas as pd
from psycopg import sql

import config
from core.payroll_calc import normalize_inputs
from core.payroll_storage import PayrollStorage, INPUT_FIELDS, RESULT_FIELDS


EDIT_FIELDS = {key: value for key, value in INPUT_FIELDS.items() if key in (
    'salary_snapshot', 'performance_snapshot', 'attendance_bonus_snapshot',
    'commute_allowance_snapshot', 'ordinary_absence_days', 'unauthorized_absence_days',
    'late_minutes', 'overtime_hours', 'work_error_notes')}

EMPLOYEE_COLUMNS = {
    'employee_code': '员工编号', 'name': '姓名', 'job_title': '岗位', 'grade': '职级',
    'hire_date': '入职日期', 'is_active': '在职', 'monthly_salary': '约定工资',
    'monthly_performance': '月绩效', 'monthly_attendance_bonus': '月全勤',
    'monthly_commute_allowance': '通勤补贴',
}
EMPLOYEE_DEFAULTS = dict.fromkeys(EMPLOYEE_COLUMNS.values(), '')
EMPLOYEE_DEFAULTS.update({'_id': None, '入职日期': None, '在职': True,
                          '约定工资': 0.0, '月绩效': 0.0, '月全勤': 0.0, '通勤补贴': 0.0})


def employee_frame(employees):
    frame = pd.DataFrame([dict(_id=e['id'], **{col: e[field] for field, col in EMPLOYEE_COLUMNS.items()})
                          for e in employees], columns=['_id', *EMPLOYEE_COLUMNS.values()])
    frame['_id'] = frame['_id'].astype('Int64')
    for column in ('约定工资', '月绩效', '月全勤', '通勤补贴'):
        frame[column] = frame[column].astype(float)
    frame['在职'] = frame['在职'].astype(bool)
    return frame


def entry_frame(entries, store_name):
    rows = []
    for entry in entries:
        row = {column: entry[field] for field, column in INPUT_FIELDS.items()}
        row['分店'] = store_name
        for column in config.MONEY_COLUMNS + ('有效加班时长',):
            row[column] = float(row[column])
        rows.append(row)
    return pd.DataFrame(rows)


class PayrollWorkspace(PayrollStorage):
    def __init__(self, connection, *, schema='public'):
        super().__init__(connection, schema=schema)
        self.tables['employees'] = sql.Identifier(schema, 'employees')

    def stores(self):
        return self._rows('SELECT * FROM {stores} ORDER BY id')

    def add_store(self, name):
        name = name.strip()
        if not name:
            raise ValueError('请填写门店名称。')
        return self._rows('INSERT INTO {stores}(name) VALUES (%s) RETURNING *', (name,))[0]

    def employees(self, store_id):
        return self._rows('SELECT * FROM {employees} WHERE store_id=%s ORDER BY id', (store_id,))

    def save_employee(self, store_id, payload, *, employee_id=None, expected=None):
        if not payload['employee_code'].strip():
            raise ValueError('员工编号不能为空。')
        source = {'员工编号': payload['employee_code'], '姓名': payload['name'],
                  '工资': payload['monthly_salary'], '绩效': payload['monthly_performance'],
                  '全勤': payload['monthly_attendance_bonus'],
                  '通勤补贴': payload['monthly_commute_allowance'],
                  '入职时间': payload.get('hire_date')}
        normalized = normalize_inputs(pd.DataFrame([source])).iloc[0]
        fields = ['employee_code', 'name', 'store_id', 'job_title', 'grade', 'hire_date',
                  'is_active', 'monthly_salary', 'monthly_performance',
                  'monthly_attendance_bonus', 'monthly_commute_allowance']
        values = [normalized['员工编号'], normalized['姓名'], store_id,
                  (payload.get('job_title') or '').strip(), (payload.get('grade') or '').strip(),
                  date.fromisoformat(normalized['入职时间']) if normalized['入职时间'] else None,
                  bool(payload.get('is_active', True))]
        values += [Decimal(str(normalized[col])) for col in config.MONEY_COLUMNS]
        with self.connection.transaction():
            if employee_id is None:
                return self._rows('''INSERT INTO {employees} (employee_code,name,store_id,
                    job_title,grade,hire_date,is_active,monthly_salary,monthly_performance,
                    monthly_attendance_bonus,monthly_commute_allowance)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''', values)[0]
            old = self._rows('SELECT * FROM {employees} WHERE id=%s AND store_id=%s FOR UPDATE',
                             (employee_id, store_id))
            if not old or expected is None or any(old[0][f] != expected[f] for f in fields):
                raise ValueError('员工档案已被其他操作更新，请刷新后重新编辑。')
            return self._rows('''UPDATE {employees} SET employee_code=%s,name=%s,store_id=%s,
                job_title=%s,grade=%s,hire_date=%s,is_active=%s,monthly_salary=%s,
                monthly_performance=%s,monthly_attendance_bonus=%s,monthly_commute_allowance=%s
                WHERE id=%s RETURNING *''', [*values, employee_id])[0]

    def save_employees(self, store_id, frame, expected):
        """整批新增和修改；任何校验或并发冲突都撤销整批写入。"""
        originals = {e['id']: e for e in expected}
        ids = frame['_id'].dropna().tolist()
        if len(ids) != len(set(ids)) or set(ids) != set(originals):
            raise ValueError('已有员工不能通过删行删除；离职请取消勾选“在职”。误删后请重新读取。')
        codes = set()
        payloads = []
        for index, row in frame.iterrows():
            payload = {field: None if pd.isna(row[col]) else row[col]
                       for field, col in EMPLOYEE_COLUMNS.items()}
            code = str(payload['employee_code'] or '').strip()
            if not code or code in codes:
                raise ValueError(f'第 {index + 1} 行：员工编号不能为空，也不能重复。')
            codes.add(code)
            payload['employee_code'] = code
            if payload['is_active'] is None:
                raise ValueError(f'第 {index + 1} 行：请选择是否在职。')
            for field in ('monthly_salary', 'monthly_performance', 'monthly_attendance_bonus', 'monthly_commute_allowance'):
                if payload[field] is None:
                    raise ValueError(f'第 {index + 1} 行：金额不能留空，没有该项请填 0。')
            identity = None if pd.isna(row['_id']) else int(row['_id'])
            payloads.append((index + 1, identity, payload))
        with self.connection.transaction():
            # 与建月流程一致，按 ID 锁定，避免不同排序的批量保存互相等待。
            self._rows('SELECT id FROM {employees} WHERE store_id=%s ORDER BY id FOR UPDATE', (store_id,))
            saved = []
            for line, identity, payload in payloads:
                try:
                    saved.append(self.save_employee(store_id, payload, employee_id=identity,
                                                    expected=originals.get(identity)))
                except ValueError as exc:
                    raise ValueError(f'第 {line} 行：{exc}') from exc
            return saved

    def months(self, store_id=None):
        if store_id is None:
            return self._rows('SELECT * FROM {payroll_months} ORDER BY payroll_month DESC')
        return self._rows('SELECT * FROM {payroll_months} WHERE store_id=%s ORDER BY payroll_month DESC',
                          (store_id,))

    def month_view(self, store_id, month):
        """Read the selected month's live data using one connection, without caching inputs."""
        batches = self._rows('SELECT * FROM {payroll_months} WHERE store_id=%s AND payroll_month=%s',
                             (store_id, month))
        if not batches:
            return None
        batch = batches[0]
        return dict(batch=batch, entries=self.entries(batch['id']), results=self.current_results(batch['id']))

    def start_month(self, store_id, month):
        if month.day != 1:
            raise ValueError('请选择有效工资月份。')
        with self.connection.transaction():
            self._rows('''INSERT INTO {payroll_months}(store_id,payroll_month,payroll_cap_snapshot)
                SELECT id,%s,monthly_payroll_cap FROM {stores} WHERE id=%s
                ON CONFLICT(store_id,payroll_month) DO NOTHING RETURNING id''', (month, store_id))
            batch = self._rows('SELECT * FROM {payroll_months} WHERE store_id=%s AND payroll_month=%s FOR UPDATE',
                               (store_id, month))
            if not batch or batch[0]['status'] != 'draft':
                raise ValueError('该月份不存在或已经确认，不能添加员工。')
            batch = batch[0]
            last_day = month.replace(day=monthrange(month.year, month.month)[1])
            employees = self._rows('''SELECT * FROM {employees} WHERE store_id=%s AND is_active
                AND (hire_date IS NULL OR hire_date<=%s) ORDER BY id FOR UPDATE''', (store_id, last_day))
            added = 0
            for employee in employees:
                duplicate = self._rows('''SELECT d.id FROM {payroll_entries} d
                    JOIN {payroll_months} p ON p.id=d.payroll_month_id
                    WHERE d.employee_id=%s AND p.payroll_month=%s AND p.id<>%s''',
                    (employee['id'], month, batch['id']))
                if duplicate:
                    raise ValueError(f"员工 {employee['employee_code']} 已在其他门店的该月工资中，请先核对归属。")
                inserted = self._rows('''INSERT INTO {payroll_entries}(payroll_month_id,employee_id,
                    employee_code_snapshot,employee_name_snapshot,job_title_snapshot,grade_snapshot,
                    hire_date_snapshot,salary_snapshot,performance_snapshot,attendance_bonus_snapshot,
                    commute_allowance_snapshot)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(payroll_month_id,employee_id) DO NOTHING RETURNING id''',
                    [batch['id'], employee['id'], employee['employee_code'], employee['name'],
                     employee['job_title'], employee['grade'], employee['hire_date'],
                     employee['monthly_salary'], employee['monthly_performance'],
                     employee['monthly_attendance_bonus'], employee['monthly_commute_allowance']])
                added += len(inserted)
            return batch, added

    def entries(self, month_id):
        return self._rows('SELECT * FROM {payroll_entries} WHERE payroll_month_id=%s ORDER BY id', (month_id,))

    def save_inputs(self, month_id, frame, versions, *, calculate=False, expected_lifecycle=None):
        with self.connection.transaction():
            batches = self._rows('''SELECT p.*,s.name AS store_name FROM {payroll_months} p
                JOIN {stores} s ON s.id=p.store_id WHERE p.id=%s FOR UPDATE OF p''', (month_id,))
            if not batches or batches[0]['status'] != 'draft':
                raise ValueError('该月份已经确认或不存在，不能修改。')
            month = batches[0]
            if expected_lifecycle is not None and month['lifecycle_version'] != expected_lifecycle:
                raise ValueError('本月确认状态已变化，请重新读取后核对。')
            entries = self._rows('SELECT * FROM {payroll_entries} WHERE payroll_month_id=%s ORDER BY id FOR UPDATE',
                                 (month_id,))
            if {row['id']: row['input_version'] for row in entries} != versions:
                raise ValueError('数据已被其他页面或操作更新。本次未覆盖，请刷新后核对。')
            if len(frame) != len(entries) or not entries:
                raise ValueError('员工列表已变化或为空，请刷新。')
            numeric = [column for field, column in EDIT_FIELDS.items() if field != 'work_error_notes']
            if frame[numeric].isna().any().any():
                raise ValueError('金额和考勤不能留空，没有发生的项目请明确填写 0。')
            normalized = normalize_inputs(frame)
            for entry, (_, row) in zip(entries, normalized.iterrows()):
                if row['员工编号'] != entry['employee_code_snapshot']:
                    raise ValueError('员工顺序或身份发生变化，请刷新。')
                values = [row[col] if field == 'work_error_notes' else Decimal(str(row[col]))
                          for field, col in EDIT_FIELDS.items()]
                assignment = ','.join(field + '=%s' for field in EDIT_FIELDS)
                self._rows('UPDATE {payroll_entries} SET ' + assignment + ' WHERE id=%s RETURNING id',
                           [*values, entry['id']])
            if calculate:
                return self.save(store_name=month['store_name'], payroll_month=month['payroll_month'])
            return []

    def set_cap(self, month_id, cap, expected):
        if cap is not None:
            cap = Decimal(str(cap))
            if not cap.is_finite() or cap < 0 or cap != cap.quantize(Decimal('.01')):
                raise ValueError('工资上限应为非负金额，最多两位小数。')
        with self.connection.transaction():
            month = self._rows('SELECT * FROM {payroll_months} WHERE id=%s FOR UPDATE', (month_id,))[0]
            if month['status'] != 'draft' or month['payroll_cap_snapshot'] != expected:
                raise ValueError('月份已确认或上限已更新，请刷新后重试。')
            self._rows('UPDATE {payroll_months} SET payroll_cap_snapshot=%s WHERE id=%s RETURNING id', (cap, month_id))

    def current_results(self, month_id):
        return self._rows('''SELECT c.* FROM {payroll_calculations} c
            JOIN {payroll_entries} d ON d.id=c.payroll_entry_id
            JOIN {payroll_months} p ON p.id=d.payroll_month_id
            WHERE d.payroll_month_id=%s AND c.input_version=d.input_version
            AND ((p.status='draft' AND c.rule_version=%s) OR
                 (p.status<>'draft' AND c.id=ANY(p.confirmed_calculation_ids)))
            ORDER BY d.id''', (month_id, config.RULE_VERSION))

    def transition_month(self, month_id, action, expected_version, *, calculation_ids=None,
                         expected_cap=None, reason=''):
        """Lock and compare the exact reviewed result set before finalization."""
        targets = {'confirm': ('draft', 'confirmed'), 'paid': ('confirmed', 'paid')}
        with self.connection.transaction():
            rows = self._rows('SELECT * FROM {payroll_months} WHERE id=%s FOR UPDATE', (month_id,))
            if not rows or rows[0]['lifecycle_version'] != expected_version:
                raise ValueError('月份状态已变化，请重新读取后操作。')
            month = rows[0]
            if action == 'reopen':
                if month['status'] not in ('confirmed', 'paid') or not reason.strip():
                    raise ValueError('请填写更正原因；只有已确认或已标记发放的月份可更正。')
                target = 'draft'
            elif action in targets and month['status'] == targets[action][0]:
                target = targets[action][1]
                reason = '老板核对后确认工资' if action == 'confirm' else '老板确认已在系统外完成发放（仅标记）'
            else:
                raise ValueError('当前月份状态不支持此操作，请重新读取。')
            self.connection.execute("SELECT set_config('app.reason', %s, true)", (reason.strip(),))
            if action == 'confirm':
                entries = self._rows('SELECT * FROM {payroll_entries} WHERE payroll_month_id=%s ORDER BY id FOR UPDATE', (month_id,))
                results = self.current_results(month_id)
                actual = sorted(r['id'] for r in results)
                if (not entries or len(results) != len(entries) or any(r['net_pay'] < 0 for r in results)
                        or actual != sorted(calculation_ids or []) or month['payroll_cap_snapshot'] != expected_cap):
                    raise ValueError('工资结果或上限已变化，或还有未核算员工，请重新读取、核算并核对。')
                self._rows('''UPDATE {payroll_months} SET status=%s,confirmed_rule_version=%s,
                    confirmed_calculation_ids=%s WHERE id=%s RETURNING id''',
                    (target, config.RULE_VERSION, actual, month_id))
            else:
                self._rows('UPDATE {payroll_months} SET status=%s WHERE id=%s RETURNING id', (target, month_id))
            return self._rows('SELECT * FROM {payroll_months} WHERE id=%s', (month_id,))[0]

    def month_actions(self, month_id):
        table = sql.Identifier(self.schema, 'audit_log')
        with self.connection.cursor() as cursor:
            cursor.execute(sql.SQL('''SELECT happened_at,actor,reason,before_record->>'status',
                after_record->>'status' FROM {} WHERE table_name='payroll_months' AND record_id=%s
                AND before_record->>'status' IS DISTINCT FROM after_record->>'status' ORDER BY id DESC''').format(table), (month_id,))
            return cursor.fetchall()


def result_frame(results):
    rows = []
    for result in results:
        snapshot = result['input_snapshot']
        row = {column: snapshot.get(field) for field, column in INPUT_FIELDS.items()}
        for column in config.MONEY_COLUMNS + config.COUNT_COLUMNS:
            row[column] = float(row[column]) if row[column] is not None else 0
        row['分店'] = snapshot.get('store_name', '')
        row['工资月份'] = snapshot.get('payroll_month', '')
        row.update({column: float(result[field]) for field, column in RESULT_FIELDS.items()})
        row['核算提示'] = '实发金额为负' if result['net_pay'] < 0 else ''
        row['输入版本'] = result['input_version']
        row['规则版本'] = result['rule_version']
        rows.append(row)
    return pd.DataFrame(rows)
