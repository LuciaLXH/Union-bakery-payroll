"""Fail closed before serving the production UI; never dump settings."""
import os
import psycopg
from core.production import owner_config, bind_connection


def main():
    if os.environ.get('APP_MODE') != 'production':
        raise ValueError('必须明确设置 APP_MODE=production。')
    owner_config()
    with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=15) as connection:
        bind_connection(connection)
        connection.execute('SELECT lifecycle_version,confirmed_calculation_ids FROM public.payroll_months LIMIT 0')
    print('正式入口配置与受限数据库权限检查通过。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, psycopg.Error):
        raise SystemExit('正式启动检查未通过，请核对环境标识、迁移、连接和受限权限。未启动网页。') from None
