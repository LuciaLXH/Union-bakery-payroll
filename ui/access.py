import hashlib
import os
import time
import streamlit as st
import psycopg
from core.access import AttemptLimiter, parse_hash, session_valid
from core.production import owner_config, owner_session_valid, check_owner_login, new_owner_session


@st.cache_resource
def limiter():
    return AttemptLimiter()


def require_test_access():
    from ui.common import header
    # A Render deployment never silently falls back to the local bypass.
    mode = os.environ.get('APP_MODE','local')
    if mode == 'production':
        return require_owner_access()
    if mode=='local' and not os.environ.get('RENDER'):
        return
    if mode not in ('local','online-test'):
        st.error('工作台运行模式配置无效，请联系维护者。')
        st.stop()
    encoded = os.environ.get('TEST_PASSWORD_HASH','')
    try:
        parse_hash(encoded)
    except ValueError:
        st.error('在线测试入口尚未配置完成，请联系维护者。')
        st.stop()
    if session_valid(st.session_state.get('test_access'),encoded):
        with st.sidebar:
            st.caption('在线体验 · 仅使用虚构数据')
            if st.button('退出测试'):
                st.session_state.clear()
                st.rerun()
        return
    if 'test_access' in st.session_state or not st.session_state.get('access_gate_ready'):
        st.session_state.clear()
        st.session_state.access_gate_ready = True
    header('UNION BAKERY / WELCOME', '欢迎体验工资小管家', '请输入维护者提供的测试口令。此环境仅用于虚构数据体验，请勿填写真实员工工资。')
    with st.form('test_login', clear_on_submit=True):
        password = st.text_input('测试口令', type='password', max_chars=1024)
        submitted = st.form_submit_button('进入测试工作台', type='primary')
    if submitted:
        outcome = limiter().check(password,encoded)
        if outcome=='ok':
            st.session_state.test_access = dict(hash=hashlib.sha256(encoded.encode()).hexdigest(),time=time.time())
            st.rerun()
        elif outcome=='wait':
            st.error('尝试次数较多，请一分钟后再试。')
        else:
            st.error('口令不正确，请检查后重试。')
    st.stop()


def check_access():
    """Also called by callbacks/fragments, before the main app gate can rerun."""
    mode = os.environ.get('APP_MODE', 'local')
    if mode == 'local' and not os.environ.get('RENDER'):
        return
    if mode == 'production':
        valid = owner_session_valid(st.session_state.get('owner_access'))
    elif mode in ('local', 'online-test'):
        valid = session_valid(st.session_state.get('test_access'), os.environ.get('TEST_PASSWORD_HASH', ''))
    else:
        valid = False
    if not valid:
        raise ValueError('登录已失效，请刷新页面重新登录；本次操作未提交。')


def require_owner_access():
    from ui.common import header
    try:
        owner_config()
        if owner_session_valid(st.session_state.get('owner_access')):
            with st.sidebar:
                st.caption('老板工作台 · 单人正式版')
                if st.button('退出登录'):
                    st.session_state.clear()
                    st.rerun()
            return
    except ValueError:
        st.error('正式入口尚未配置完成，请联系维护者。')
        st.stop()
    if 'owner_access' in st.session_state or not st.session_state.get('owner_gate_ready'):
        st.session_state.clear()
        st.session_state.owner_gate_ready = True
    header('UNION BAKERY', '登录工资工作台', '仅供老板本人使用。登录后可管理员工、核算和确认工资。')
    with st.form('owner_login', clear_on_submit=True):
        username = st.text_input('账号', max_chars=100)
        password = st.text_input('密码', type='password', max_chars=1024)
        submitted = st.form_submit_button('登录', type='primary')
    if submitted:
        try:
            with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=15) as connection:
                connection.execute('SET LOCAL lock_timeout=5000; SET LOCAL statement_timeout=30000')
                outcome = check_owner_login(connection, username.strip(), password)
            if outcome == 'ok':
                st.session_state.owner_access = new_owner_session(time.time())
                st.rerun()
            st.error('尝试次数较多，请一分钟后再试。' if outcome == 'wait' else '账号或密码不正确。')
        except (ValueError, psycopg.Error):
            st.error('暂时无法登录，请稍后重试或联系维护者。')
    st.stop()
