"""Run against packaged Windows binaries in CI, or an isolated native PG 18 locally."""
import json
from decimal import Decimal
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
import urllib.request
from uuid import uuid4

import psycopg
from psycopg import sql

from core.access import password_hash
from core.production import bind_connection
from core.workspace import PayrollWorkspace
from scripts.backup_database import fingerprints, restore_check
from scripts.windows_local import Manager, clean_environment, exclusive, stop, protect_home, URL

ROOT=Path(__file__).resolve().parents[1]


class WindowsLauncherUnitTests(unittest.TestCase):
    def test_cloud_configuration_never_inherited(self):
        poisoned=dict(DATABASE_URL='remote-secret',OWNER_PASSWORD_HASH='private',PGHOST='remote',
            DASHSCOPE_API_KEY='private',OPENAI_API_KEY='private',RENDER='true',PYTHONPATH='untrusted')
        with patch.dict(os.environ,poisoned):
            clean=clean_environment()
            self.assertTrue(all(k not in clean for k in poisoned))

    def test_second_process_cannot_open_installation_for_update(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'lock'
            with exclusive(path):
                with self.assertRaises(ValueError):
                    with exclusive(path): self.fail('Lock must be exclusive')
            with exclusive(path): pass

    def test_configuration_preserves_credentials_and_refuses_unknown_schema(self):
        with TemporaryDirectory() as temp:
            manager=Manager(ROOT,Path(temp))
            original=manager.configure('test-user',password_hash('fake-test-password-123'))
            manager.configure('other-user',password_hash('different-password-123'))
            self.assertEqual(manager.settings,original)
            changed=dict(original,schema_version=-1)
            manager.settings_path.write_text(json.dumps(changed),encoding='utf-8')
            with self.assertRaises(ValueError): manager.load()
            self.assertEqual(json.loads(manager.settings_path.read_text(encoding='utf-8')),changed)


@unittest.skipUnless(os.getenv('PG_BIN'),'Set PG_BIN for an isolated PostgreSQL 18 test')
class WindowsLauncherIntegrationTests(unittest.TestCase):
    def test_install_login_restart_backup_restore_and_packaged_server(self):
        with TemporaryDirectory(prefix='bakery-local-ci-') as temp:
            home=Path(temp)/'state';protect_home(home)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]
            root=Path(os.getenv('WINDOWS_BUNDLE_TEST_ROOT',str(ROOT)))
            manager=Manager(root,home,pg_bin=os.environ['PG_BIN'],port=port)
            password='fictional-windows-test-password-123'
            manager.configure('boss',password_hash(password))
            process=None
            try:
                manager.start_db();manager.initialize_database()
                with psycopg.connect(manager.connection()) as db:
                    before=fingerprints(db)
                    repo=PayrollWorkspace(db)
                    self.assertEqual(len(repo.employees(repo.stores()[0]['id'])),2)
                    self.assertEqual([r['net_pay'] for r in repo.current_results(repo.months()[0]['id'])],[Decimal('2943.90'),Decimal('5400')])
                with patch.dict(os.environ,manager.app_environment()):
                    with psycopg.connect(manager.connection(app=True)) as db: bind_connection(db)
                    from streamlit.testing.v1 import AppTest
                    app=root/'app/app.py' if os.getenv('WINDOWS_BUNDLE_TEST_ROOT') else ROOT/'app.py'
                    page=AppTest.from_file(str(app),default_timeout=30).run()
                    self.assertFalse(page.exception)
                    next(w for w in page.text_input if w.label=='账号').set_value('boss')
                    next(w for w in page.text_input if w.label=='密码').set_value(password)
                    next(w for w in page.button if w.label=='登录').click().run()
                    self.assertFalse(page.exception)
                    self.assertTrue(any('试用版' in w.value for w in page.warning))
                    self.assertTrue(any('2,943.90' in w.value for w in page.markdown))
                manager.stop_db();manager.start_db();manager.initialize_database()
                with psycopg.connect(manager.connection()) as db: after=fingerprints(db)
                # Owner login updates only its dedicated throttle record.
                self.assertEqual({k:v for k,v in before.items() if k!='owner_login_attempts'},
                                 {k:v for k,v in after.items() if k!='owner_login_attempts'})
                archive=manager.create_backup()
                restore_name='bakery_restore_windows_'+uuid4().hex
                with psycopg.connect(manager.connection('postgres'),autocommit=True) as admin:
                    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_name)))
                    try: self.assertEqual(len(restore_check(manager.connection(restore_name),archive)),10)
                    finally: admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(restore_name)))
                self.assertEqual(json.loads((archive/'settings.json').read_text(encoding='utf-8')),manager.settings)
                manager.stop_db()
                if os.getenv('WINDOWS_BUNDLE_TEST_ROOT'):
                    # Boot the actual packaged interpreter/server and stop it through the shipped protocol.
                    code=('from scripts.windows_local import Manager; '
                          f'm=Manager({str(root)!r},{str(home)!r},port={port}); m.load(); m.run(open_browser=False)')
                    log=(Path(temp)/'server-output.log').open('wb')
                    process=subprocess.Popen([sys.executable,'-X','utf8','-c',code],stdout=log,stderr=log)
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    for _ in range(120):
                        if process.poll() is not None:
                            log.close();self.fail('Packaged server exited before becoming healthy')
                        try:
                            with opener.open(URL+'/_stcore/health',timeout=1) as response:
                                if response.status==200: break
                        except OSError: time.sleep(.5)
                    else: self.fail('Packaged server health check timed out')
                    stop(home)
                    self.assertEqual(process.wait(timeout=90),0)
                    log.close()
                    self.assertFalse((manager.data/'postmaster.pid').exists())
            finally:
                if process and process.poll() is None: process.terminate();process.wait(timeout=20)
                if (manager.data/'postmaster.pid').exists(): manager.stop_db()


if __name__=='__main__': unittest.main(verbosity=2)
