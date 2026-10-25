"""Consistent custom-format backup + empty-database restore verification.

BACKUP_DATABASE_URL=... python -m scripts.backup_database backup --output /private/path
RESTORE_DATABASE_URL=... python -m scripts.backup_database restore-check --archive /private/path
Only restores into an empty bakery_restore_* database. Never overwrites production.
Requires pg_dump/pg_restore on PATH or PG_BIN. Archives contain sensitive payroll data.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def fingerprints(connection):
    connection.execute("SET LOCAL timezone='UTC'")
    tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    result = {}
    for (name,) in tables:
        digest, count = hashlib.sha256(), 0
        # Canonical JSONB text preserves Decimal values; order independent of physical layout.
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier('public', name)))
            for (row,) in cursor:
                digest.update(row.encode() + b'\n')
                count += 1
        result[name] = dict(rows=count, sha256=digest.hexdigest())
    return result


def pg_env(url):
    values = conninfo_to_dict(url)
    mapping = dict(host='PGHOST', port='PGPORT', dbname='PGDATABASE', user='PGUSER', password='PGPASSWORD',
                   sslmode='PGSSLMODE', channel_binding='PGCHANNELBINDING', sslrootcert='PGSSLROOTCERT',
                   connect_timeout='PGCONNECT_TIMEOUT', options='PGOPTIONS')
    if set(values) - set(mapping):
        raise ValueError('备份连接含未支持的选项，请使用标准直连地址。')
    if '-pooler' in values.get('host', ''):
        raise ValueError('备份和恢复必须使用直连地址。')
    # Credentials are process environment, never shell commands or argv.
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'SYSTEMROOT', 'TMPDIR')}
    env.update({mapping[k]: v for k, v in values.items()})
    env.setdefault('PGCONNECT_TIMEOUT', '15')
    return env


def run_pg(name, args, url):
    binary = str(Path(os.environ['PG_BIN']) / name) if os.environ.get('PG_BIN') else shutil.which(name)
    if not binary:
        raise ValueError('请安装 PostgreSQL 客户端，并设置 PG_BIN 或 PATH。')
    completed = subprocess.run([binary, *args], env=pg_env(url), capture_output=True, timeout=300)
    if completed.returncode or completed.stderr:
        raise ValueError(f'{name} 未正常完成或返回警告，请核对工具版本、连接和权限；未输出敏感诊断。')


def file_hash(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def backup(url, destination):
    pg_env(url)  # Reject unsafe/misconfigured connection before creating output.
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    archive = destination / 'payroll.dump'
    try:
        with psycopg.connect(url, connect_timeout=15) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
            checks = fingerprints(connection)
            # Precreate privately: pg_dump opens this file without broadening permissions.
            with open(archive, 'xb'):
                pass
            archive.chmod(0o600)
            run_pg('pg_dump', ['--format=custom','--no-owner','--no-privileges','--schema=public',
                '--snapshot=' + snapshot,'--file=' + str(archive)], url)
        manifest = dict(format_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                        archive_sha256=file_hash(archive), tables=checks)
        manifest_path = destination / 'manifest.json'
        with open(manifest_path, 'x') as f:
            manifest_path.chmod(0o600)
            json.dump(manifest, f, indent=2)
        return manifest
    except Exception:
        # Keep failures distinguishable from a complete backup: no valid manifest is written.
        raise


def restore_check(url, source):
    pg_env(url)
    source = Path(source).resolve()
    manifest = json.loads((source / 'manifest.json').read_text())
    archive = source / 'payroll.dump'
    if manifest.get('format_version') != 1 or file_hash(archive) != manifest['archive_sha256']:
        raise ValueError('备份校验不通过，未恢复。')
    with psycopg.connect(url, connect_timeout=15) as connection:
        database = connection.execute('SELECT current_database()').fetchone()[0]
        if not database.startswith('bakery_restore_'):
            raise ValueError('恢复验证只允许名称以 bakery_restore_ 开头的独立空库。')
        if connection.execute("SELECT 1 FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') LIMIT 1").fetchone():
            raise ValueError('恢复目标非空，未覆盖任何数据。')
        # A newly created database already has public. Our schema-specific dump
        # recreates it. RESTRICT refuses any unexpected function/type/object.
        connection.execute('DROP SCHEMA public RESTRICT')
    run_pg('pg_restore', ['--single-transaction','--exit-on-error','--no-owner','--no-privileges',
                         '--dbname=' + database, str(archive)], url)
    with psycopg.connect(url, connect_timeout=15) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        actual = fingerprints(connection)
    if actual != manifest['tables']:
        raise ValueError('恢复后的逐表数据校验不一致。请保留验证库排查，勿切换正式连接。')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('backup').add_argument('--output', required=True)
    sub.add_parser('restore-check').add_argument('--archive', required=True)
    args = parser.parse_args()
    if args.action == 'backup':
        manifest = backup(os.environ['BACKUP_DATABASE_URL'], args.output)
        print(f"备份完成：{len(manifest['tables'])} 张表。请将整个目录存入老板控制的加密备份位置。")
    else:
        tables = restore_check(os.environ['RESTORE_DATABASE_URL'], args.archive)
        print(f'恢复验证通过：{len(tables)} 张表的行数和内容指纹一致。未修改正式数据库。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, psycopg.Error, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, ValueError) else '备份/恢复未完成，请核对配置、权限和工具版本。') from None
