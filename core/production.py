"""Single-owner deployment boundary; no passwords or connection strings in logs."""
import hashlib
import hmac
import os
from uuid import UUID

from psycopg import sql

from core.access import parse_hash, session_valid, verify_password


def owner_config():
    username = os.environ.get('OWNER_USERNAME', '').strip()
    encoded = os.environ.get('OWNER_PASSWORD_HASH', '')
    instance = os.environ.get('PRODUCTION_INSTANCE_ID', '')
    if not username or len(username) > 100:
        raise ValueError('正式入口尚未配置老板账号。')
    parse_hash(encoded)
    try:
        UUID(instance)
    except ValueError:
        raise ValueError('正式数据库标识配置无效。') from None
    if not os.environ.get('DATABASE_URL'):
        raise ValueError('正式数据库尚未配置。')
    return username, encoded, instance


def owner_session_valid(session):
    username, encoded, instance = owner_config()
    return bool(session_valid(session, encoded) and session.get('username') == username
                and session.get('instance') == instance)


def bind_connection(connection, *, schema='public', actor=None):
    """Refuse test/production mixups, including misconfigured local startup."""
    mode = os.environ.get('APP_MODE', 'local')
    table = sql.Identifier(schema, 'app_environment')
    exists = connection.execute('SELECT to_regclass(%s)', (f'{schema}.app_environment',)).fetchone()[0]
    if not exists:
        if mode == 'production':
            raise ValueError('数据库尚未完成正式版迁移。')
        return
    row = connection.execute(sql.SQL('SELECT environment,instance_id FROM {} WHERE singleton').format(table)).fetchone()
    if not row:
        raise ValueError('数据库环境标识缺失。')
    if mode == 'production':
        username, _, instance = owner_config()
        if row != ('production', instance):
            raise ValueError('正式入口与数据库标识不一致，已停止访问。')
        unsafe = connection.execute('''SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
            OR has_schema_privilege(current_user,%s,'CREATE')
            OR has_table_privilege(current_user,%s,'DELETE')
            OR has_table_privilege(current_user,%s,'UPDATE')
            FROM pg_roles r WHERE r.rolname=current_user''',
            (schema, f'{schema}.payroll_entries', f'{schema}.audit_log')).fetchone()[0]
        if unsafe:
            raise ValueError('正式网页必须使用受限数据库账号，不能使用管理员连接。')
        actor = actor or username
    elif row[0] == 'production':
        raise ValueError('测试或本地入口不能连接正式数据库。')
    connection.execute("SELECT set_config('app.actor', %s, true)", (actor or 'test-owner',))


def check_owner_login(connection, username, password, *, schema='public'):
    """DB-backed global throttling survives web process restarts and parallel sessions."""
    expected_user, encoded, _ = owner_config()
    bind_connection(connection, schema=schema, actor='login')
    table = sql.Identifier(schema, 'owner_login_attempts')
    row = connection.execute(sql.SQL('''SELECT failures,blocked_until>clock_timestamp()
        FROM {} WHERE singleton FOR UPDATE''').format(table)).fetchone()
    if row is None:
        raise ValueError('登录保护尚未初始化。')
    if row[1]:
        return 'wait'
    # Always verify the hash, even for a wrong username.
    password_ok = verify_password(password, encoded)
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    if password_ok and user_ok:
        connection.execute(sql.SQL('UPDATE {} SET failures=0,blocked_until=NULL WHERE singleton').format(table))
        return 'ok'
    connection.execute(sql.SQL('''UPDATE {} SET
        failures=CASE WHEN blocked_until IS NOT NULL THEN 1 ELSE failures+1 END,
        blocked_until=CASE WHEN blocked_until IS NULL AND failures>=4
            THEN clock_timestamp()+interval '60 seconds' ELSE NULL END WHERE singleton''').format(table))
    return 'invalid'


def new_owner_session(now):
    username, encoded, instance = owner_config()
    return dict(username=username, instance=instance,
                hash=hashlib.sha256(encoded.encode()).hexdigest(), time=now)
