"""Session-local synthetic repository implementing the web workspace operations.

The public demo never reads a DATABASE_URL. Production PostgreSQL storage lives
in core.workspace; both interfaces use the same Decimal payroll engine.
"""
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd

import config
from core.payroll_calc import calculate_payroll, normalize_inputs
from core.payroll_storage import INPUT_FIELDS, RESULT_FIELDS
from core.workspace import EMPLOYEE_COLUMNS, entry_frame


class DemoWorkspace:
    MAX_EMPLOYEES = 50
    MAX_STORES = 5
    MAX_MONTHS = 24

    def __init__(self, today=None):
        self.data = {k: [] for k in ('stores', 'employees', 'months', 'entries', 'history')}
        self.sequence = {k: 0 for k in self.data}
        today = today or date.today()
        store = self.add_store('Union · Riverside')
        self.add_store('Union · Garden Lane')
        for code, name, role, grade, salary, performance, bonus, travel in [
            ('DEMO-001', 'Alex Baker', 'Baker', 'Beginner apprentice', 4500, 600, 300, 0),
            ('DEMO-002', 'Jamie Chen', 'Baker', 'Experienced apprentice', 5200, 500, 300, 120),
            ('DEMO-003', 'Taylor Lee', 'Front of house', 'Team member', 4200, 0, 300, 100),
        ]:
            self.save_employee(store['id'], dict(employee_code=code, name=name, job_title=role,
                grade=grade, hire_date=None, is_active=True, monthly_salary=salary,
                monthly_performance=performance, monthly_attendance_bonus=bonus,
                monthly_commute_allowance=travel))
        month, _ = self.start_month(store['id'], today.replace(day=1))
        entries = self.entries(month['id'])
        frame = entry_frame(entries, store['name'])
        frame.at[0, '普通缺勤天数'] = 1
        frame.at[0, '迟到'] = 7
        frame.at[0, '有效加班时长'] = 2
        frame.at[0, '工作失误项目'] = 'Apprentice practice: coaching provided; no deduction.'
        self.save_inputs(month['id'], frame, {e['id']: e['input_version'] for e in entries}, calculate=True)

    def _insert(self, table, **values):
        self.sequence[table] += 1
        row = dict(values, id=self.sequence[table])
        self.data[table].append(row)
        return deepcopy(row)

    def _month(self, identity):
        return next(m for m in self.data['months'] if m['id'] == identity)

    def stores(self):
        return deepcopy(self.data['stores'])

    def add_store(self, name):
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError('Enter a store name of 1–100 characters.')
        if any(s['name'].casefold() == name.casefold() for s in self.data['stores']):
            raise ValueError('A store with this name already exists.')
        if len(self.data['stores']) >= self.MAX_STORES:
            raise ValueError('This demo supports five stores. Reset the demo to start again.')
        return self._insert('stores', name=name, monthly_payroll_cap=None)

    def employees(self, store_id):
        return deepcopy([e for e in self.data['employees'] if e['store_id'] == store_id])

    def save_employee(self, store_id, payload, *, employee_id=None, expected=None):
        code = str(payload.get('employee_code') or '').strip()
        if not code:
            raise ValueError('Employee ID is required.')
        if not any(s['id'] == store_id for s in self.data['stores']):
            raise ValueError('Select an existing store.')
        if any(e['employee_code'] == code and e['id'] != employee_id for e in self.data['employees']):
            raise ValueError('Employee ID must be unique across all stores.')
        source = {'员工编号': code, '姓名': payload.get('name'), '工资': payload.get('monthly_salary'),
                  '绩效': payload.get('monthly_performance'), '全勤': payload.get('monthly_attendance_bonus'),
                  '通勤补贴': payload.get('monthly_commute_allowance'), '入职时间': payload.get('hire_date')}
        row = normalize_inputs(pd.DataFrame([source])).iloc[0]
        if len(code) > 50 or any(len(str(payload.get(k) or '')) > 100 for k in ('name', 'job_title', 'grade')):
            raise ValueError('Use an employee ID up to 50 characters and names, roles and grades up to 100 characters.')
        values = dict(employee_code=code, name=row['姓名'], store_id=store_id,
                      job_title=str(payload.get('job_title') or '').strip(), grade=str(payload.get('grade') or '').strip(),
                      hire_date=date.fromisoformat(row['入职时间']) if row['入职时间'] else None,
                      is_active=bool(payload.get('is_active', True)))
        for field, col in zip(('monthly_salary', 'monthly_performance', 'monthly_attendance_bonus',
                               'monthly_commute_allowance'), config.MONEY_COLUMNS):
            values[field] = Decimal(str(row[col]))
        if employee_id is None:
            if len(self.data['employees']) >= self.MAX_EMPLOYEES:
                raise ValueError('This demo supports 50 employees. Reset to start again.')
            return self._insert('employees', **values)
        old = next((e for e in self.data['employees'] if e['id'] == employee_id and e['store_id'] == store_id), None)
        if old is None or old != expected:
            raise ValueError('Employee details changed. Reload before saving.')
        old.update(values)
        return deepcopy(old)

    def save_employees(self, store_id, frame, expected):
        originals = {e['id']: e for e in expected}
        identities = frame['_id'].dropna().tolist()
        if len(identities) != len(set(identities)) or set(identities) != set(originals):
            raise ValueError('Keep existing employee rows. Uncheck Active for leavers instead of deleting them.')
        before, sequence = deepcopy(self.data), deepcopy(self.sequence)
        try:
            saved = []
            for _, row in frame.iterrows():
                payload = {field: None if pd.isna(row[col]) else row[col] for field, col in EMPLOYEE_COLUMNS.items()}
                identity = None if pd.isna(row['_id']) else int(row['_id'])
                saved.append(self.save_employee(store_id, payload, employee_id=identity, expected=originals.get(identity)))
            return saved
        except Exception:
            self.data, self.sequence = before, sequence
            raise

    def months(self, store_id):
        return deepcopy(sorted([m for m in self.data['months'] if m['store_id'] == store_id],
                               key=lambda m: m['payroll_month'], reverse=True))

    def start_month(self, store_id, month):
        if month.day != 1:
            raise ValueError('A pay month must start on the first day.')
        store = next(s for s in self.data['stores'] if s['id'] == store_id)
        existing = next((m for m in self.data['months'] if m['store_id'] == store_id and m['payroll_month'] == month), None)
        if not existing:
            if len(self.data['months']) >= self.MAX_MONTHS:
                raise ValueError('This demo supports 24 store-months. Reset to start again.')
            existing = self._insert('months', store_id=store_id, payroll_month=month,
                                    payroll_cap_snapshot=None, status='draft')
        if existing['status'] != 'draft':
            raise ValueError('This pay month is read-only.')
        last_day = month.replace(day=monthrange(month.year, month.month)[1])
        count = 0
        for e in self.employees(store_id):
            if not e['is_active'] or (e['hire_date'] and e['hire_date'] > last_day):
                continue
            if any(d['employee_id'] == e['id'] and d['payroll_month_id'] == existing['id'] for d in self.data['entries']):
                continue
            self._insert('entries', payroll_month_id=existing['id'], employee_id=e['id'], input_version=1,
                employee_code_snapshot=e['employee_code'], employee_name_snapshot=e['name'],
                job_title_snapshot=e['job_title'], grade_snapshot=e['grade'], hire_date_snapshot=e['hire_date'],
                salary_snapshot=e['monthly_salary'], performance_snapshot=e['monthly_performance'],
                attendance_bonus_snapshot=e['monthly_attendance_bonus'], commute_allowance_snapshot=e['monthly_commute_allowance'],
                ordinary_absence_days=0, unauthorized_absence_days=0, late_minutes=0, overtime_hours=0, work_error_notes='')
            count += 1
        return deepcopy(existing), count

    def entries(self, month_id):
        return deepcopy([e for e in self.data['entries'] if e['payroll_month_id'] == month_id])

    def save_inputs(self, month_id, frame, versions, *, calculate=False):
        entries = self.entries(month_id)
        month = self._month(month_id)
        if month['status'] != 'draft':
            raise ValueError('This pay month is read-only.')
        if versions != {e['id']: e['input_version'] for e in entries}:
            raise ValueError('Inputs changed. Reload before saving.')
        if len(frame) != len(entries) or not entries:
            raise ValueError('The employee list changed or is empty. Reload before saving.')
        numeric = list(config.MONEY_COLUMNS + config.COUNT_COLUMNS)
        if frame[numeric].isna().any().any():
            raise ValueError('Amounts and attendance cannot be blank. Enter 0 when not applicable.')
        normalized = normalize_inputs(frame)
        if any(normalized['工作失误项目'].str.len() > 2000):
            raise ValueError('Keep each performance note within 2,000 characters.')
        computed = calculate_payroll(normalized, decimal_output=True) if calculate else None
        if calculate and any(computed['合计'] < 0):
            raise ValueError('Net pay cannot be negative. Check attendance and deductions; nothing was saved.')
        updated = []
        for original, (_, row) in zip(entries, normalized.iterrows()):
            if row['员工编号'] != original['employee_code_snapshot']:
                raise ValueError('Employee order changed. Reload before saving.')
            new = deepcopy(original)
            for field, column in INPUT_FIELDS.items():
                if column in numeric or field == 'work_error_notes':
                    new[field] = str(row[column]) if field == 'work_error_notes' else Decimal(str(row[column]))
            if any(new[k] != original[k] for k in new):
                new['input_version'] += 1
            updated.append(new)
        new_results = sum(not any(h['payroll_entry_id'] == e['id'] and h['input_version'] == e['input_version']
                          and h['rule_version'] == config.RULE_VERSION for h in self.data['history']) for e in updated)
        if calculate and len(self.data['history']) + new_results > 2000:
            raise ValueError('Demo history limit reached. Reset your demo to start again.')
        replacements = {e['id']: e for e in updated}
        self.data['entries'] = [replacements.get(e['id'], e) for e in self.data['entries']]
        if not calculate:
            return []
        store = next(s for s in self.data['stores'] if s['id'] == month['store_id'])
        for entry, (_, result) in zip(updated, computed.iterrows()):
            if any(h['payroll_entry_id'] == entry['id'] and h['input_version'] == entry['input_version']
                   and h['rule_version'] == config.RULE_VERSION for h in self.data['history']):
                continue
            snapshot = dict(deepcopy(entry), store_name=store['name'], payroll_month=month['payroll_month'].isoformat())
            self._insert('history', payroll_entry_id=entry['id'], input_version=entry['input_version'],
                rule_version=config.RULE_VERSION, input_snapshot=snapshot, calculated_at=datetime.now(timezone.utc),
                **{field: result[column] for field, column in RESULT_FIELDS.items()})
        return self.current_results(month_id)

    def current_results(self, month_id):
        versions = {e['id']: e['input_version'] for e in self.entries(month_id)}
        return deepcopy(sorted([h for h in self.data['history'] if versions.get(h['payroll_entry_id']) == h['input_version']
                         and h['rule_version'] == config.RULE_VERSION], key=lambda h: h['payroll_entry_id']))

    def history(self, *, store_name, payroll_month):
        return deepcopy([h for h in self.data['history'] if h['input_snapshot']['store_name'] == store_name
                         and h['input_snapshot']['payroll_month'] == payroll_month.isoformat()])

    def set_cap(self, month_id, cap, expected):
        month = self._month(month_id)
        if month['status'] != 'draft' or month['payroll_cap_snapshot'] != expected:
            raise ValueError('The limit changed. Reload before saving.')
        if cap is not None:
            cap = Decimal(str(cap))
            if not cap.is_finite() or cap < 0 or cap != cap.quantize(Decimal('.01')):
                raise ValueError('Enter a non-negative limit with at most two decimal places.')
        month['payroll_cap_snapshot'] = cap
