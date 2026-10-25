"""Employee matching and review validation for the isolated prototype."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from ai_entry.service import EntryError, KINDS, MODES

PEOPLE = {'TEST-001': '张三', 'TEST-002': '李四', 'TEST-003': '王五',
          'TEST-004': '杨西宁', 'TEST-005': '杨新宁', 'TEST-006': '张三'}
STORES = {code: ('虚构门店 A' if code in ('TEST-001', 'TEST-002', 'TEST-003') else '虚构门店 B') for code in PEOPLE}
COLUMNS = ['员工编号', '所属门店', '原文姓名', '事项', '数量', '单位', '处理方式', '原文 / 备注', '待核对问题']
SAMPLE = '李四本月累计迟到10分钟，杨新宁本月普通缺勤累计一天，王五操作失误报废一公斤乳酪。'
EXAMPLE_ITEMS = [
    dict(name='李四', kind='迟到', amount=10, unit='分钟', mode='累计', source='李四本月累计迟到10分钟', question=''),
    dict(name='杨新宁', kind='普通缺勤', amount=1, unit='天', mode='累计', source='杨新宁本月普通缺勤累计一天', question=''),
    dict(name='王五', kind='备注', amount=None, unit='', mode='不适用', source='王五操作失误报废一公斤乳酪', question=''),
]


def review_rows(items, people=None, stores=None):
    people = PEOPLE if people is None else people
    stores = STORES if stores is None else stores
    rows = []
    for item in items:
        identity = item['name'].strip()
        matches = [code for code, name in people.items() if name == identity or code == identity]
        if not matches:
            # Some providers include the explicit field label in the name field.
            # Strip only that label, require an exact known ID also present in the source.
            candidate = re.sub(r'^(?:员工编号|编号)\s*[:：]?\s*', '', identity).strip()
            if candidate != identity and candidate in people and re.search(
                    r'(?<![A-Za-z0-9_-])' + re.escape(candidate) + r'(?![A-Za-z0-9_-])', item['source']):
                matches = [candidate]
        issues = [item['question']] if item['question'] else []
        if len(matches) != 1:
            issues.append('姓名同名或未匹配，请手动选择员工编号；不自动纠正姓名。')
        if item['mode'] == '待确认':
            issues.append('请确认是新增还是本月累计。')
        if item['kind'] in ('奖励', '扣款'):
            issues.append('奖罚金额尚未接入核算，请改为备注保留原文。')
        rows.append(dict(zip(COLUMNS, [matches[0] if len(matches) == 1 else '', stores[matches[0]] if len(matches) == 1 else '', item['name'],
                    item['kind'], item['amount'], item['unit'], item['mode'], item['source'], '；'.join(issues)])))
    return rows


def confirm_rows(rows, context, people=None, stores=None):
    people = PEOPLE if people is None else people
    stores = STORES if stores is None else stores
    if not rows:
        raise EntryError('请先录入至少一条事项。')
    clean = []
    units = {'迟到': '分钟', '普通缺勤': '天', '旷工': '天', '加班': '小时',
             '通勤补贴': '元', '奖励': '元', '扣款': '元'}
    for index, row in enumerate(rows, 1):
        row = dict(row)
        for label in ('原文姓名', '待核对问题'):
            if not isinstance(row.get(label), str):
                row[label] = ''
        if row.get('员工编号') not in people:
            raise EntryError(f'第 {index} 行：请选择有效员工编号。')
        kind, mode = row.get('事项'), row.get('处理方式')
        if kind not in KINDS or kind == '待确认' or mode not in MODES or mode == '待确认':
            raise EntryError(f'第 {index} 行：请明确事项和处理方式。')
        if not isinstance(row.get('原文 / 备注'), str) or not row['原文 / 备注'].strip():
            raise EntryError(f'第 {index} 行：请保留原文或填写备注。')
        if kind == '备注':
            row.update(数量=None, 单位='', 处理方式='不适用')
        else:
            try:
                amount = Decimal(str(row.get('数量')))
                if not amount.is_finite() or not 0 <= amount <= 1000000:
                    raise ValueError
                if kind in ('普通缺勤', '旷工') and (amount > 30 or amount != amount.to_integral_value()):
                    raise ValueError
                if kind == '迟到' and amount != amount.to_integral_value():
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise EntryError(f'第 {index} 行：数量无效；缺勤按整天、迟到按整数分钟测试。') from None
            if row.get('单位') != units[kind] or mode == '不适用':
                raise EntryError(f'第 {index} 行：请核对单位及新增/累计方式。')
            row['数量'] = float(amount)
        row['确认姓名'] = people[row['员工编号']]
        row['所属门店'] = stores[row['员工编号']]
        clean.append(row)
    # Review notes/labels do not allow identical events to bypass duplicate checks.
    identities = [{k: row.get(k) for k in ['员工编号', '事项', '数量', '单位', '处理方式', '原文 / 备注']} for row in clean]
    serials = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in identities]
    if len(set(serials)) != len(serials):
        raise EntryError('本批次存在完全重复事项，请删除重复行。')
    digest = hashlib.sha256(json.dumps([context, sorted(serials)], ensure_ascii=False).encode()).hexdigest()
    return clean, digest
