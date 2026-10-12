"""运行方式：python -m scripts.preview_database_payroll --help。"""
import argparse
from datetime import date
from decimal import Decimal
import os
import re
import sys

import psycopg

from core.database import load_draft_inputs
from core.payroll_calc import calculate_payroll


def parse_month(value):
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise argparse.ArgumentTypeError("月份请使用 YYYY-MM，例如 2026-10。")
    try:
        return date.fromisoformat(value + '-01')
    except ValueError:
        raise argparse.ArgumentTypeError("请填写有效月份。") from None


def main():
    parser = argparse.ArgumentParser(description="读取数据库草稿并核算预览，不写回数据库。")
    parser.add_argument('--store', required=True, help='门店名称')
    parser.add_argument('--month', required=True, type=parse_month, help='工资月份，例如 2026-10')
    parser.add_argument('--employee', help='员工编号；省略时读取该店当月全部已有草稿明细')
    args = parser.parse_args()
    try:
        inputs = load_draft_inputs(
            os.environ.get('DATABASE_URL', ''), store_name=args.store,
            payroll_month=args.month, employee_code=args.employee,
        )
        result = calculate_payroll(inputs)
    except psycopg.OperationalError:
        print('数据库连接失败：请检查 Postgres.app 是否启动、连接配置及连接许可。', file=sys.stderr)
        return 1
    except psycopg.Error:
        # 不把可能包含连接配置的原始异常直接展示出来。
        print('数据库读取失败：请检查数据库权限和已执行的建表、考勤字段脚本。', file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f'门店：{args.store} | 工资月份：{args.month:%Y-%m} | 读取明细：{len(result)} 人')
    print(f'核算规则版本：{result.attrs["rule_version"]}')
    for _, row in result.iterrows():
        print(f'\n{row["员工编号"]}  {row["姓名"]}')
        for column in ('工资', '绩效', '全勤', '通勤补贴', '工资扣款', '绩效扣款',
                       '全勤扣款', '迟到扣款', '加班费', '应发合计', '扣款合计', '合计'):
            print(f'  {column}：{row[column]:.2f} 元')
        if row['工作失误项目']:
            print(f'  工作失误备注：{row["工作失误项目"]}')
        if row['核算提示']:
            print(f'  核算提示：{row["核算提示"]}')
    total = sum((Decimal(str(value)) for value in result['合计']), Decimal('0'))
    print(f'\n本次读取明细的实发合计：{total:.2f} 元')
    print('仅为当前草稿试算；结果尚未保存，不能据此认定全店员工已录齐或工资已确认。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
