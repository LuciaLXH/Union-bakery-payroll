"""Local-only trial server. Never imports cloud credentials or existing payroll data."""
import argparse
from datetime import date, datetime
import getpass
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from core.workspace import PayrollWorkspace, entry_frame
from scripts.production_database import MIGRATIONS, migration_sql
from scripts.backup_database import backup

ROOT = Path(__file__).resolve().parents[1]
DATABASE = 'bakery_payroll_local_demo'
MARKER = 'local-demo-schema-' + str(len(MIGRATIONS))
PORT = 18502
URL = f'http://127.0.0.1:{PORT}'


def connection_info(database=DATABASE):
    return make_conninfo(host='127.0.0.1', port=5432, dbname=database,
                         user=getpass.getuser(), connect_timeout=5)


def check_database(connection):
    marker = connection.execute('SELECT environment,instance_id FROM public.app_environment WHERE singleton').fetchone()
    if marker != ('test', MARKER):
        raise ValueError('本地试用库标识或结构版本不匹配，已停止。请联系维护者升级；不会重新初始化或覆盖数据。')


def initialize():
    with psycopg.connect(connection_info('postgres'), autocommit=True) as admin:
        # Serialize first launches without deleting or replacing an existing database.
        admin.execute('SELECT pg_advisory_lock(736820055)')
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DATABASE,)).fetchone():
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE)))
        with psycopg.connect(connection_info()) as connection:
            if connection.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
                check_database(connection)
                return
            for filename in MIGRATIONS:
                connection.execute(migration_sql(filename), prepare=False)
            connection.execute('SET LOCAL search_path TO public,pg_catalog')
            connection.execute('UPDATE app_environment SET instance_id=%s', (MARKER,))
            repo = PayrollWorkspace(connection)
            store = repo.add_store('本机试用店（虚构数据）')
            for code, name, wage, performance, attendance, commute in [
                ('LOCAL-001','示例小麦',2600,0,200,300),
                ('LOCAL-002','示例小豆',4500,600,300,0),
            ]:
                repo.save_employee(store['id'], dict(employee_code=code,name=name,is_active=True,
                    monthly_salary=wage,monthly_performance=performance,
                    monthly_attendance_bonus=attendance,monthly_commute_allowance=commute))
            month, _ = repo.start_month(store['id'], date.today().replace(day=1))
            entries = repo.entries(month['id'])
            frame = entry_frame(entries,store['name'])
            frame.at[0,'普通缺勤天数'] = 2
            frame.at[0,'有效加班时长'] = 3.5
            frame.at[1,'迟到'] = 15
            repo.save_inputs(month['id'],frame,{e['id']:e['input_version'] for e in entries},calculate=True)


def create_backup():
    with psycopg.connect(connection_info()) as connection:
        check_database(connection)
    if not os.environ.get('PG_BIN') and not shutil.which('pg_dump'):
        mac_tools = Path('/Applications/Postgres.app/Contents/Versions/latest/bin')
        if mac_tools.is_dir():
            os.environ['PG_BIN'] = str(mac_tools)
    destination = ROOT / '.local/local-demo/backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup(connection_info(), destination)
    print('备份完成：', destination, flush=True)


def launch():
    initialize()
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1',PORT))
        except OSError:
            raise ValueError(f'端口 {PORT} 已被占用。若试用版已启动，请直接打开 {URL}；不重复启动。') from None
    env = dict(os.environ)
    # Fixed local target; inherited production/AI settings must not leak into this trial.
    for key in list(env):
        if key.startswith(('PG','AI_ENTRY_','OWNER_','PRODUCTION_','DASHSCOPE_','OPENAI_')) or key == 'RENDER':
            env.pop(key,None)
    env.update(DATABASE_URL=connection_info(),APP_MODE='local')
    print(f'本地试用版：{URL}\n只连接本机独立虚构数据。按 Control+C 停止网页，已保存数据会保留。',flush=True)
    command = [sys.executable,'-m','streamlit','run',str(ROOT/'app.py'),
               '--server.address','127.0.0.1','--server.port',str(PORT),'--server.headless','true',
               '--server.fileWatcherType','none','--browser.gatherUsageStats','false']
    os.execve(sys.executable,command,env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','init','backup'])
    action = parser.parse_args().action
    if action == 'run':
        launch()
    elif action == 'init':
        initialize()
        print('本地试用库已就绪，已有数据保持不变。')
    else:
        create_backup()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, psycopg.Error, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc,ValueError) else
              '本地启动或备份未完成。请确认 Postgres.app 正在运行、端口为 5432，且已安装项目依赖。未输出连接或密钥。',file=sys.stderr)
        sys.exit(1)
