"""核算保存、当前状态和历史查询。例：python -m scripts.database_payroll --help。"""
import argparse
import os
import sys

import psycopg

from core.payroll_storage import PayrollStorage
from scripts.preview_database_payroll import parse_month


def main():
    parser = argparse.ArgumentParser(description='月度工资草稿：保存、状态和历史。')
    parser.add_argument('action', choices=('save', 'status', 'history'))
    parser.add_argument('--store', required=True)
    parser.add_argument('--month', required=True, type=parse_month)
    parser.add_argument('--employee')
    args = parser.parse_args()
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url.strip():
        print('请先设置 DATABASE_URL 数据库连接地址。', file=sys.stderr)
        return 1
    try:
        options = '-c lock_timeout=5000 -c statement_timeout=30000'
        if args.action != 'save':
            options += ' -c default_transaction_read_only=on'
        with psycopg.connect(database_url, connect_timeout=5, options=options) as connection:
            storage = PayrollStorage(connection)
            rows = getattr(storage, args.action)(store_name=args.store,
                payroll_month=args.month, employee_code=args.employee)
        # 必须等连接上下文成功提交后，才报告保存成功。
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except psycopg.OperationalError:
        print('连接失败或等待数据库锁超时；请检查服务与连接配置，稍后重试。', file=sys.stderr)
        return 1
    except psycopg.Error:
        print('数据库操作失败；保存事务已回滚。请检查表结构和权限。', file=sys.stderr)
        return 1

    if args.action == 'save':
        for row in rows:
            action = '复用已有结果' if row['reused'] else '已保存新结果'
            print(f"{row['employee_code']} | {action} | 结果编号 {row['id']} | "
                  f"输入版本 {row['input_version']} | 实发 {row['net_pay']:.2f} 元")
        print('本次保存事务已提交；工资仍为草稿，未确认发薪。')
    elif args.action == 'status':
        for row in rows:
            amount = '—' if row['net_pay'] is None else f"{row['net_pay']:.2f} 元"
            print(f"{row['employee_code']} | 输入版本 {row['input_version']} | "
                  f"{row['calculation_status']} | 当前实发 {amount} | 月份 {row['month_status']}")
    else:
        if not rows:
            print('尚无保存的核算历史。')
        for row in rows:
            snapshot = row['input_snapshot']
            print(f"结果 {row['id']} | 明细 {row['payroll_entry_id']} | "
                  f"输入版本 {row['input_version']} | 规则 {row['rule_version']} | "
                  f"迟到 {snapshot.get('late_minutes', '—')} 分钟 | 实发 {row['net_pay']:.2f} 元")
        print('以上为历史记录，是否当前有效请使用 status 查询。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
