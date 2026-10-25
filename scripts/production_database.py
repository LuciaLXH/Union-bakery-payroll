"""Initialize an EMPTY owner-controlled database, or migrate an existing test DB.

No seed employees. Credentials are read from env, never command arguments.
python -m scripts.production_database init --database bakery_payroll_production
python -m scripts.production_database migrate-test --database bakery_payroll_test
"""
import argparse
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ('001_schema_baseline.sql', '002_add_attendance.sql',
              '003_calculation_history.sql', '004_ai_entry.sql', '005_production.sql')


def migration_sql(filename):
    return '\n'.join(line for line in (ROOT / 'database' / filename).read_text().splitlines()
                     if not line.startswith('\\') and line.strip() not in ('BEGIN;', 'COMMIT;'))


def grant_app(connection, role):
    """Run as DB owner. No DDL, DELETE, audit edits, or calculation-history edits."""
    r = sql.Identifier(role)
    database = connection.execute('SELECT current_database()').fetchone()[0]
    connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database), r))
    connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(r))
    connection.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}').format(r))
    connection.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(r))
    connection.execute(sql.SQL('''GRANT INSERT,UPDATE ON public.stores,public.employees,
        public.payroll_months,public.payroll_entries,public.ai_entry_usage TO {}''').format(r))
    connection.execute(sql.SQL('GRANT INSERT ON public.payroll_calculations,public.ai_entry_events TO {}').format(r))
    connection.execute(sql.SQL('GRANT UPDATE ON public.owner_login_attempts TO {}').format(r))
    connection.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(r))


def initialize(connection, app_password, *, role='bakery_owner_app'):
    if len(app_password) < 24:
        raise ValueError('数据库应用密码至少需要24个字符。')
    with connection.transaction():
        if connection.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
            raise ValueError('只允许初始化空数据库；已有数据库未修改。')
        for filename in MIGRATIONS:
            connection.execute(migration_sql(filename), prepare=False)
        instance = str(uuid4())
        connection.execute("UPDATE public.app_environment SET environment='production',instance_id=%s", (instance,))
        connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}')
                           .format(sql.Identifier(role), sql.Literal(app_password)))
        grant_app(connection, role)
    return instance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'migrate-test'])
    parser.add_argument('--database', required=True, help='Exact target database name; checked before writing')
    parser.add_argument('--role', help='New restricted role for init, existing app role for migrate-test')
    args = parser.parse_args()
    url = os.environ['PRODUCTION_ADMIN_URL' if args.action == 'init' else 'CLOUD_ADMIN_URL']
    if '-pooler' in conninfo_to_dict(url).get('host', ''):
        raise ValueError('迁移请使用直连地址。')
    with psycopg.connect(url, connect_timeout=15) as connection:
        actual = connection.execute('SELECT current_database()').fetchone()[0]
        if actual != args.database:
            raise ValueError('目标数据库名称不一致，未执行。')
        if args.action == 'init':
            if actual != 'bakery_payroll_production':
                raise ValueError('正式空库名称必须为 bakery_payroll_production。')
            instance = initialize(connection, os.environ['PRODUCTION_APP_PASSWORD'], role=args.role or 'bakery_owner_app')
            print('空库和受限应用账号已建立。PRODUCTION_INSTANCE_ID=' + instance)
        else:
            if actual != 'bakery_payroll_test':
                raise ValueError('此升级入口仅用于 bakery_payroll_test。')
            if connection.execute("SELECT to_regclass('public.app_environment')").fetchone()[0]:
                raise ValueError('005已执行或环境不为空；请核对迁移记录，未重复执行。')
            connection.execute(migration_sql('005_production.sql'), prepare=False)
            grant_app(connection, args.role or 'bakery_app')
            print('测试库005迁移已完成。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, psycopg.Error) as exc:
        # Driver errors can contain user data or connection details. Never echo them.
        raise SystemExit(str(exc) if isinstance(exc, ValueError) else '配置或数据库操作失败，未提交本次事务。') from None
