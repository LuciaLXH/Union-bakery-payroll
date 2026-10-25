"""Initialize a new, empty test database; never copy local payroll records.

Run with: python -m scripts.initialize_test_database
Requires CLOUD_ADMIN_URL and CLOUD_APP_PASSWORD. Refuses existing public tables.
"""
from datetime import date
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from core.workspace import PayrollWorkspace, entry_frame
from scripts.production_database import MIGRATIONS, migration_sql, grant_app


def initialize(connection, app_password):
    with connection.transaction():
        if connection.execute("SELECT current_database()").fetchone()[0] != 'bakery_payroll_test':
            raise ValueError('Only bakery_payroll_test may be initialized.')
        if connection.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' LIMIT 1").fetchone():
            raise ValueError('Database is not empty; initialization stopped without changes.')
        for filename in MIGRATIONS:
            connection.execute(migration_sql(filename), prepare=False)
        connection.execute('SET LOCAL search_path TO public, pg_catalog')
        # App role has no schema ownership, role administration, or deletion rights.
        connection.execute(sql.SQL('CREATE ROLE bakery_app LOGIN PASSWORD {}').format(sql.Literal(app_password)))
        grant_app(connection, 'bakery_app')
        repo = PayrollWorkspace(connection)
        store = repo.add_store('Union 虚构测试店')
        repo.save_employee(store['id'], dict(employee_code='DEMO-001', name='示例小面包',
            job_title='烘焙', grade='零基础学徒', is_active=True, monthly_salary=4500,
            monthly_performance=600, monthly_attendance_bonus=300, monthly_commute_allowance=0))
        month, _ = repo.start_month(store['id'], date(2026, 10, 1))
        entries = repo.entries(month['id'])
        frame = entry_frame(entries, store['name'])
        frame.at[0, '普通缺勤天数'] = 1
        frame.at[0, '迟到'] = 7
        frame.at[0, '有效加班时长'] = 2
        frame.at[0, '工作失误项目'] = '虚构测试：学徒练习失误，已指导，不扣款'
        results = repo.save_inputs(month['id'], frame,
            {e['id']: e['input_version'] for e in entries}, calculate=True)
        if results[0]['net_pay'] != 5263:
            raise ValueError('Seed calculation verification failed; rolling back.')


if __name__ == '__main__':
    url = os.environ['CLOUD_ADMIN_URL']
    if '-pooler.' in (urlsplit(url).hostname or ''):
        raise ValueError('Use a direct connection for initialization.')
    with psycopg.connect(url, connect_timeout=15) as connection:
        initialize(connection, os.environ['CLOUD_APP_PASSWORD'])
    print('Initialized current schema and synthetic demo. Verified net pay: 5263.00')
