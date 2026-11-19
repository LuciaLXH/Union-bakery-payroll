"""Packaged Windows trial: private PostgreSQL, owner login, backups and clean shutdown.

The command-line entry point is Windows-only. Manager also supports isolated native
PostgreSQL integration tests; it never accepts a cloud database URL.
"""
import argparse
from contextlib import contextmanager
from datetime import date, datetime
import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
import webbrowser

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from core.access import password_hash
from core.workspace import PayrollWorkspace, entry_frame
from scripts.production_database import initialize, MIGRATIONS
from scripts.backup_database import backup

DB_PORT = 18532
WEB_PORT = 18512
DATABASE = 'bakery_windows_trial'
ADMIN = 'bakery_local_admin'
APP_ROLE = 'bakery_local_app'
SCHEMA_VERSION = len(MIGRATIONS)
URL = f'http://127.0.0.1:{WEB_PORT}'


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp-' + uuid.uuid4().hex)
    with tmp.open('x', encoding='utf-8') as out:
        if os.name != 'nt':
            tmp.chmod(0o600)
        json.dump(data,out,ensure_ascii=False,indent=2)
    tmp.replace(path)


def clean_environment():
    env = {k:v for k,v in os.environ.items() if not k.startswith(
        ('PG','OWNER_','PRODUCTION_','AI_ENTRY_','OPENAI_','DASHSCOPE_','PYTHON'))
        and k not in ('DATABASE_URL','APP_MODE','RENDER','LOCAL_INSTALL_MODE')}
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    return env


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    file = path.open('a+b')
    file.seek(0)
    if not file.read(1):
        file.write(b'0'); file.flush()
    file.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(file.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        raise ValueError('本地程序正在运行或维护中。请先关闭启动窗口或使用“停止”，不要同时启动两个版本。') from None
    try:
        yield
    finally:
        file.close()


def protect_home(home):
    home.mkdir(parents=True,exist_ok=True)
    if os.name == 'nt':
        # Apply only to our dedicated directory, never to the whole user profile.
        import csv
        result = subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True)
        sid = next(csv.reader(result.stdout.strip().splitlines()))[1]
        if not sid.startswith('S-1-'):
            raise ValueError('无法确定本机账号，未初始化。')
        result = subprocess.run(['icacls',str(home),'/inheritance:r','/grant:r',
            f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True)
        if result.returncode:
            raise ValueError('无法保护本地数据目录，未初始化。')
    else:
        home.chmod(0o700)


class Manager:
    def __init__(self, root, home, *, pg_bin=None, port=DB_PORT):
        self.root,self.home = Path(root),Path(home)
        self.pg_bin = Path(pg_bin) if pg_bin else self.root/'runtime/postgres/bin'
        self.port = port
        self.data = self.home/'database'
        self.logs = self.home/'logs'
        self.settings_path = self.home/'settings.json'
        self.settings = None
        self.home.mkdir(parents=True,exist_ok=True)
        self.logs.mkdir(exist_ok=True)

    def tool(self,name):
        return str(self.pg_bin/(name + ('.exe' if os.name == 'nt' else '')))

    def pg(self,name,args,*,check=True):
        result = subprocess.run([self.tool(name),*map(str,args)],env=clean_environment(),
                                capture_output=True,timeout=90)
        if check and result.returncode:
            # Tool output can include local paths/configuration; keep it in protected storage.
            with (self.logs/'database-tools.log').open('ab') as log:
                log.write(result.stdout + result.stderr)
            raise ValueError(f'{name} 未完成。请联系维护者查看本机日志；不会重建或清空已有数据库。')
        return result

    def load(self):
        self.settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        if self.settings.get('format') != 1 or self.settings.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('此版本需要配套数据库升级，请联系维护者。已有数据未重建。')
        return self.settings

    def configure(self,username,encoded):
        if self.settings_path.exists():
            return self.load()
        self.settings = dict(format=1,schema_version=SCHEMA_VERSION,owner_username=username,
            owner_password_hash=encoded,admin_password=secrets.token_urlsafe(40),
            app_password=secrets.token_urlsafe(40),instance_id=None,ai_key='',trial=True)
        atomic_json(self.settings_path,self.settings)
        return self.settings

    def connection(self, database=DATABASE, *, app=False):
        return make_conninfo(host='127.0.0.1',port=self.port,dbname=database,
            user=APP_ROLE if app else ADMIN,
            password=self.settings['app_password' if app else 'admin_password'],connect_timeout=5)

    def init_cluster(self):
        if (self.data/'PG_VERSION').exists():
            if (self.data/'PG_VERSION').read_text().strip() != '18':
                raise ValueError('数据库主版本不兼容，未启动；请联系维护者迁移。')
            return
        if self.data.exists() and any(self.data.iterdir()):
            raise ValueError('发现未完成的数据库目录，已停止；不会覆盖已有文件。')
        password_file = self.home/('init-password-' + uuid.uuid4().hex)
        try:
            password_file.write_text(self.settings['admin_password'],encoding='utf-8')
            if os.name != 'nt': password_file.chmod(0o600)
            self.pg('initdb',['-D',self.data,'--username='+ADMIN,'--pwfile='+str(password_file),
                '--encoding=UTF8','--locale=C','--auth-host=scram-sha-256','--auth-local=scram-sha-256'])
        finally:
            password_file.unlink(missing_ok=True)

    def start_db(self):
        self.init_cluster()
        if self.pg('pg_ctl',['-D',self.data,'status'],check=False).returncode == 0:
            # Verify the server actually reached is the instance we own.
            self.verify_server()
            return False
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1',self.port))
            except OSError: raise ValueError(f'数据库端口 {self.port} 已被其他程序占用。未连接该程序。') from None
        self.pg('pg_ctl',['-D',self.data,'-l',self.logs/'postgres.log','-w','-t','60',
            '-o',f'-h 127.0.0.1 -p {self.port}','start'])
        self.verify_server()
        return True

    def verify_server(self):
        with psycopg.connect(self.connection('postgres')) as db:
            actual = Path(db.execute('SHOW data_directory').fetchone()[0]).resolve()
            if actual != self.data.resolve():
                raise ValueError('数据库目录不匹配，已停止操作。')

    def stop_db(self):
        if (self.data/'PG_VERSION').exists():
            self.pg('pg_ctl',['-D',self.data,'-w','-t','60','-m','fast','stop'])

    def initialize_database(self):
        with psycopg.connect(self.connection('postgres'),autocommit=True) as admin:
            if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(DATABASE,)).fetchone():
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE)))
        with psycopg.connect(self.connection()) as db:
            if not db.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
                instance = initialize(db,self.settings['app_password'],role=APP_ROLE)
                # First release is a trial only; never copy live payroll into an unverified machine.
                repo = PayrollWorkspace(db)
                store = repo.add_store('Windows 试用店（虚构数据）')
                for code,name,w,p,a,c in [('WIN-001','示例小麦',2600,0,200,300),('WIN-002','示例小豆',4500,600,300,0)]:
                    repo.save_employee(store['id'],dict(employee_code=code,name=name,is_active=True,
                        monthly_salary=w,monthly_performance=p,monthly_attendance_bonus=a,monthly_commute_allowance=c))
                month,_ = repo.start_month(store['id'],date.today().replace(day=1))
                entries = repo.entries(month['id']); frame = entry_frame(entries,store['name'])
                frame.at[0,'普通缺勤天数']=2; frame.at[0,'有效加班时长']=3.5
                repo.save_inputs(month['id'],frame,{e['id']:e['input_version'] for e in entries},calculate=True)
            else:
                environment,instance = db.execute('SELECT environment,instance_id FROM public.app_environment WHERE singleton').fetchone()
                if environment != 'production' or (self.settings['instance_id'] and self.settings['instance_id'] != instance):
                    raise ValueError('数据库身份不匹配，未覆盖已有数据。')
        self.settings['instance_id'] = instance
        atomic_json(self.settings_path,self.settings)
        # Restricted role must be usable before any app is launched.
        with psycopg.connect(self.connection(app=True)) as db:
            db.execute('SELECT count(*) FROM public.stores')

    def app_environment(self):
        env = clean_environment()
        env.update(DATABASE_URL=self.connection(app=True),APP_MODE='production',
            OWNER_USERNAME=self.settings['owner_username'],OWNER_PASSWORD_HASH=self.settings['owner_password_hash'],
            PRODUCTION_INSTANCE_ID=self.settings['instance_id'],LOCAL_INSTALL_MODE='trial',
            AI_ENTRY_PROVIDER='qwen',DASHSCOPE_API_KEY=self.settings.get('ai_key',''))
        return env

    def create_backup(self):
        self.verify_server()
        destination = self.home/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        previous = os.environ.get('PG_BIN')
        try:
            os.environ['PG_BIN'] = str(self.pg_bin)
            backup(self.connection(),destination)
        finally:
            if previous is None: os.environ.pop('PG_BIN',None)
            else: os.environ['PG_BIN']=previous
        # Recovery needs both the database archive and this machine's local credentials.
        target = destination/'settings.json'
        target.write_bytes(self.settings_path.read_bytes())
        if os.name != 'nt': target.chmod(0o600)
        print('备份完成：',destination,flush=True)
        return destination

    def run(self, *, open_browser=True):
        with exclusive(self.home/'application.lock'):
            process = None
            started = False
            run_id = uuid.uuid4().hex
            try:
                self.start_db(); started=True
                self.initialize_database()
                self.create_backup()  # Fail closed if the pre-update/start backup cannot complete.
                with socket.socket() as probe:
                    try: probe.bind(('127.0.0.1',WEB_PORT))
                    except OSError: raise ValueError(f'网页端口 {WEB_PORT} 已占用，未打开其他程序。') from None
                command = [sys.executable,'-X','utf8','-m','streamlit','run','app.py','--server.address','127.0.0.1',
                    '--server.port',str(WEB_PORT),'--server.headless','true','--server.fileWatcherType','none',
                    '--browser.gatherUsageStats','false','--server.enableXsrfProtection','true',
                    '--server.enableCORS','true','--client.showErrorDetails','none']
                with (self.logs/'website.log').open('ab') as log:
                    process = subprocess.Popen(command,cwd=self.root/'app',env=self.app_environment(),
                        stdout=log,stderr=log,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0)
                atomic_json(self.home/'running.json',dict(run_id=run_id,url=URL))
                import urllib.request
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    if process.poll() is not None: raise ValueError('网页启动失败，请联系维护者检查本机日志。')
                    try:
                        with opener.open(URL+'/_stcore/health',timeout=1) as response:
                            if response.status==200: break
                    except OSError: time.sleep(.5)
                else: raise ValueError('网页未按时启动，请联系维护者。')
                print('已启动：'+URL+'\n请保留本窗口。按 Ctrl+C 或双击“停止”正常关闭。',flush=True)
                if open_browser:
                    webbrowser.open(URL)
                while process.poll() is None:
                    stop = self.home/'stop.json'
                    if stop.exists() and json.loads(stop.read_text(encoding='utf-8')).get('run_id')==run_id: break
                    time.sleep(.5)
            except KeyboardInterrupt:
                pass
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=20)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
                (self.home/'running.json').unlink(missing_ok=True)
                (self.home/'stop.json').unlink(missing_ok=True)
                if started:
                    try: self.create_backup()
                    finally: self.stop_db()


def stop(home):
    marker = home/'running.json'
    if not marker.exists():
        print('没有正在运行的网页。若窗口刚打开，请稍后再试。'); return
    data=json.loads(marker.read_text(encoding='utf-8'))
    atomic_json(home/'stop.json',dict(run_id=data['run_id']))
    for _ in range(120):
        if not marker.exists():
            # Shutdown removes marker before backup, so wait on the application lock too.
            try:
                with exclusive(home/'application.lock'): pass
                print('已停止，数据保留在本机。'); return
            except ValueError: pass
        time.sleep(.5)
    raise ValueError('停止仍在进行，请查看启动窗口；没有强制结束其他进程。')


def main():
    if os.name != 'nt': raise ValueError('此入口只供 Windows 安装包使用。')
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['start','stop','backup','ai'])
    action=parser.parse_args().action
    root=Path(__file__).resolve().parents[2]
    home=Path(os.environ['LOCALAPPDATA'])/'UnionBakeryPayrollTrial'
    protect_home(home)
    manager=Manager(root,home)
    if action=='stop': return stop(home)
    if not manager.settings_path.exists():
        if action!='start': raise ValueError('请先双击“启动”完成首次设置。')
        with exclusive(home/'application.lock'):
            print('首次设置：本机试用版，仅含虚构数据。登录账号为 boss。')
            first=getpass.getpass('请设置登录密码（至少16个字符，可用一段容易记住的话）：')
            if first!=getpass.getpass('再输入一次：'): raise ValueError('两次密码不一致，未初始化。')
            manager.configure('boss',password_hash(first))
    else: manager.load()
    if action=='start': return manager.run()
    if action=='ai':
        with exclusive(home/'application.lock'):
            print('使用老板自己的千问 API Key；仅在识别时联网并按服务商计费。直接回车可停用。')
            manager.settings['ai_key']=getpass.getpass('API Key（输入不显示）：').strip()
            atomic_json(manager.settings_path,manager.settings)
            print('已保存本机设置，下次启动生效。')
        return
    with exclusive(home/'application.lock'):
        started=False
        try:
            started=manager.start_db()
            manager.initialize_database()
            manager.create_backup()
        finally:
            if started: manager.stop_db()


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError,psycopg.Error,subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc,ValueError) else '操作未完成。请联系维护者检查本机日志；未输出密码或数据库连接。',file=sys.stderr)
        sys.exit(1)
