"""Shared PostgreSQL budget and transactional, reviewed payroll input writes."""
from datetime import date
from decimal import Decimal
import hashlib
import json
import os

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ai_entry.monthly import event_key
from ai_entry.review import confirm_rows
from ai_entry.service import Budget, EntryError
from core.workspace import PayrollWorkspace, entry_frame

FIELDS = {'迟到': '迟到', '普通缺勤': '普通缺勤天数', '旷工': '旷工天数',
          '加班': '有效加班时长', '通勤补贴': '通勤补贴'}


class CloudBudget:
    month = staticmethod(Budget.month)

    def __init__(self, url=None, schema='public'):
        self.url = url or os.environ['DATABASE_URL']
        self.table = sql.Identifier(schema, 'ai_entry_usage')

    def connect(self):
        return psycopg.connect(self.url, connect_timeout=15)

    def spent(self):
        with self.connect() as db:
            return db.execute(sql.SQL('SELECT COALESCE(SUM(units),0) FROM {} WHERE month=%s').format(self.table),
                              (self.month(),)).fetchone()[0]

    def reserve(self, units, settings):
        with self.connect() as db:
            db.execute('SET LOCAL lock_timeout = 5000')
            # All requests in the app share this transaction-scoped lock, including multiple instances.
            db.execute('SELECT pg_advisory_xact_lock(736820041)')
            spent = db.execute(sql.SQL('SELECT COALESCE(SUM(units),0) FROM {} WHERE month=%s').format(self.table),
                               (self.month(),)).fetchone()[0]
            if spent + units > int(settings.budget_cny * 1000000):
                raise EntryError('本月 AI 预算不足，请使用手动录入。')
            return db.execute(sql.SQL('INSERT INTO {}(month,units,state,model) VALUES(%s,%s,%s,%s) RETURNING id').format(self.table),
                              (self.month(), units, 'reserved', settings.model)).fetchone()[0]

    def settle(self, request_id, units, input_tokens, output_tokens):
        with self.connect() as db:
            db.execute(sql.SQL('UPDATE {} SET units=%s,state=%s,input_tokens=%s,output_tokens=%s WHERE id=%s').format(self.table),
                       (units, 'reported', input_tokens, output_tokens, request_id))


class AIWorkspace(PayrollWorkspace):
    def __init__(self, connection, *, schema='public'):
        super().__init__(connection, schema=schema)
        self.tables['ai_events'] = sql.Identifier(schema, 'ai_entry_events')

    def catalog(self):
        return self._rows('''SELECT e.id,e.employee_code,e.name,e.store_id,s.name AS store_name
            FROM {employees} e JOIN {stores} s ON s.id=e.store_id ORDER BY e.id''')

    def prepare(self, month, records):
        if isinstance(month, str):
            month = date.fromisoformat(month + '-01')
        catalog = self.catalog()
        people = {e['employee_code']: e['name'] for e in catalog}
        stores = {e['employee_code']: e['store_name'] for e in catalog}
        records, _ = confirm_rows(records, str(month), people, stores)
        employees = {e['employee_code']: e for e in catalog}
        store_ids = sorted({employees[r['员工编号']]['store_id'] for r in records})
        plans, changes, evidence = [], [], []
        for store_id in store_ids:
            batches = [m for m in self.months(store_id) if m['payroll_month'] == month]
            if not batches:
                raise EntryError(f"{next(e['store_name'] for e in catalog if e['store_id']==store_id)} 尚未建立该月工资。请先在“月度工资”建立，再回来预览。")
            batch = batches[0]
            if batch['status'] != 'draft':
                raise EntryError('该月份已确认，不能写入。')
            entries = self.entries(batch['id'])
            frame = entry_frame(entries, next(e['store_name'] for e in catalog if e['store_id']==store_id))
            relevant = [r for r in records if employees[r['员工编号']]['store_id']==store_id]
            touched = {}
            for row in relevant:
                code, kind = row['员工编号'], row['事项']
                employee = employees[code]
                matches = [i for i,e in enumerate(entries) if e['employee_id']==employee['id']]
                if len(matches)!=1:
                    raise EntryError(f'{code} 不在所属门店的当月工资中，请先核对员工及月份。')
                i = matches[0]
                if entries[i]['employee_code_snapshot'] != code:
                    raise EntryError(f'{code} 与当月员工编号快照不一致，请先核对档案。')
                if kind not in FIELDS and kind != '备注':
                    raise EntryError('奖励及报废扣款尚未接入核算。请改成“备注”保留原文；不会自动扣款。')
                key = (code, kind)
                if kind != '备注':
                    modes = touched.setdefault(key, [])
                    if modes and (row['处理方式'] != '新增' or any(m != '新增' for m in modes)):
                        raise EntryError('同一员工同一项目多条记录只支持全部“新增”；累计请合并为一条。')
                    modes.append(row['处理方式'])
                    field = FIELDS[kind]
                    before = frame.at[i, field]
                    amount = Decimal(str(row['数量']))
                    if row['处理方式']=='新增':
                        amount += Decimal(str(before))
                    frame.at[i, field] = float(amount)
                    changes.append({'员工': people[code], '编号': code, '门店': stores[code], '项目': field,
                                    '原值': str(before), '写入后': str(amount)})
                note = f"[录入明细 · {kind} · {row['处理方式']}] {row['原文 / 备注']}"
                before_note = frame.at[i,'工作失误项目'] or ''
                frame.at[i,'工作失误项目'] = '\n'.join(filter(None,[before_note,note]))
                if kind == '备注':
                    changes.append({'员工': people[code], '编号': code, '门店': stores[code], '项目': '备注',
                                    '原值': str(before_note), '写入后': frame.at[i,'工作失误项目']})
                if Decimal(str(frame.at[i,'普通缺勤天数'])) + Decimal(str(frame.at[i,'旷工天数'])) > 30:
                    raise EntryError('普通缺勤与旷工合计不能超过30天。')
                if self._rows('SELECT fingerprint FROM {ai_events} WHERE payroll_month=%s AND fingerprint=%s',
                              (month, event_key(row))):
                    raise EntryError('存在已经写入的相同事项，整批未写入。请删除重复行再预览。')
            plans.append((batch['id'], frame, {e['id']:e['input_version'] for e in entries}))
            evidence.append([batch, entries])
        signature = hashlib.sha256(json.dumps([month, records, catalog, evidence], default=str,
                                              ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return dict(month=month, records=records, token=signature, plans=plans, changes=changes)

    def apply(self, month, records, expected):
        with self.connection.transaction():
            # Serialize AI imports; regular editors are protected by save_inputs' row/version checks.
            self.connection.execute('SELECT pg_advisory_xact_lock(736820042)')
            month_date = date.fromisoformat(month + '-01') if isinstance(month, str) else month
            # Match start_month's month -> employee lock order.
            self._rows('SELECT id FROM {payroll_months} WHERE payroll_month=%s ORDER BY id FOR UPDATE', (month_date,))
            self._rows('SELECT id FROM {employees} ORDER BY id FOR UPDATE')
            preview = self.prepare(month, records)
            if preview['token'] != expected:
                raise EntryError('档案或月度数据已变化，请重新预览后再写入。')
            for month_id, frame, versions in preview['plans']:
                self.save_inputs(month_id, frame, versions)
            for row in preview['records']:
                self._rows('INSERT INTO {ai_events}(payroll_month,fingerprint,record) VALUES(%s,%s,%s) RETURNING fingerprint',
                           (preview['month'], event_key(row), Jsonb(row)))
            return len(preview['records'])
