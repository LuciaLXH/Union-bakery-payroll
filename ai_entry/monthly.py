"""Persistent fictional monthly inputs, completely separate from PostgreSQL payroll."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from ai_entry.review import PEOPLE, STORES, confirm_rows
from ai_entry.service import EntryError

FIELDS = {'迟到': '迟到分钟', '普通缺勤': '普通缺勤天数', '旷工': '旷工天数',
          '加班': '加班小时', '通勤补贴': '通勤补贴'}


def initial(code):
    return {'员工编号': code, '姓名': PEOPLE[code], '门店': STORES[code], '工资': 4500,
            '绩效': 300, '全勤': 200, **{field: 0 for field in FIELDS.values()}, '备注': ''}


def event_key(row):
    values = [row.get(k) for k in ['员工编号', '事项', '数量', '单位', '处理方式', '原文 / 备注']]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def project(current, rows):
    result = deepcopy(current)
    touched = set()
    for row in rows:
        code, kind = row['员工编号'], row['事项']
        if kind not in FIELDS and kind != '备注':
            raise EntryError('奖励、扣款规则尚未确认，此批次不能写入工资；请单独保留为记录。')
        if (code, kind) in touched and kind != '备注':
            raise EntryError('同一员工同一项目有多条记录，请先合并为一条，避免累计与新增顺序不明确。')
        touched.add((code, kind))
        item = result.setdefault(code, initial(code))
        if kind == '备注':
            item['备注'] = '\n'.join(filter(None, [item['备注'], row['原文 / 备注']]))
        else:
            field = FIELDS[kind]
            amount = Decimal(str(row['数量']))
            if row['处理方式'] == '新增':
                amount += Decimal(str(item[field]))
            item[field] = float(amount)
        if item['普通缺勤天数'] + item['旷工天数'] > 30:
            raise EntryError('写入后普通缺勤与旷工合计超过30天，整批未写入。')
    return result


class MonthlyStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS months (month TEXT PRIMARY KEY, revision INTEGER, data TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS events (month TEXT, fingerprint TEXT, PRIMARY KEY(month,fingerprint))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def read(self, month):
        with self.connect() as db:
            row = db.execute('SELECT revision,data FROM months WHERE month=?', (month,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, {})

    def write(self, month, rows, expected_revision):
        rows, _ = confirm_rows(rows, month)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT revision,data FROM months WHERE month=?', (month,)).fetchone()
            revision, current = (previous[0], json.loads(previous[1])) if previous else (0, {})
            if revision != expected_revision:
                raise EntryError('该月记录已更新，请重新核对写入预览。')
            keys = [event_key(row) for row in rows]
            if len(set(keys)) != len(keys) or any(db.execute(
                    'SELECT 1 FROM events WHERE month=? AND fingerprint=?', (month, key)).fetchone() for key in keys):
                raise EntryError('包含已经写入的相同事项，整批未重复写入。')
            updated = project(current, rows)
            db.execute('INSERT INTO months VALUES (?,?,?) ON CONFLICT(month) DO UPDATE '
                       'SET revision=excluded.revision,data=excluded.data',
                       (month, revision + 1, json.dumps(updated, ensure_ascii=False)))
            db.executemany('INSERT INTO events VALUES (?,?)', [(month, key) for key in keys])
        return updated
