import hashlib
import os
import time
import streamlit as st
from core.access import AttemptLimiter, parse_hash, session_valid
from ui.common import header


@st.cache_resource
def limiter():
    return AttemptLimiter()


def require_test_access():
    # A Render deployment never silently falls back to the local bypass.
    mode = os.environ.get('APP_MODE','local')
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
