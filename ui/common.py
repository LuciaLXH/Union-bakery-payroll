from datetime import date, datetime
from decimal import Decimal
from html import escape
import os
from zoneinfo import ZoneInfo

import psycopg
import streamlit as st

from core.workspace import PayrollWorkspace

DB_SCHEMA = 'public'


def db(method, *args, **kwargs):
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise ValueError('工作空间尚未连接，请使用项目启动脚本打开网页。')
    with psycopg.connect(url, connect_timeout=15) as connection:
        # Transaction-local settings also work through Neon's PgBouncer pool.
        connection.execute('SET LOCAL lock_timeout = 5000; SET LOCAL statement_timeout = 30000')
        return getattr(PayrollWorkspace(connection, schema=DB_SCHEMA), method)(*args, **kwargs)


def show_error(exc):
    if isinstance(exc, psycopg.errors.UniqueViolation):
        st.error('名称或员工编号已存在，请检查后重试。')
    elif isinstance(exc, psycopg.OperationalError):
        st.error('暂时无法连接工作空间，或数据正在被其他操作使用。请确认数据库已启动后重试。')
    elif isinstance(exc, psycopg.Error):
        st.error('数据保存或读取失败，本次更改未提交。请检查数据库结构和访问权限。')
    else:
        st.error(str(exc))


def fetch(method, *args, **kwargs):
    try:
        return db(method, *args, **kwargs)
    except (ValueError, psycopg.Error) as exc:
        show_error(exc)
        st.stop()


def header(kicker, title, description):
    st.markdown(f'<div class="page-heading"><div class="eyebrow">{escape(kicker)}</div>'
                f'<h1>{escape(title)}</h1><p>{escape(description)}</p></div>', unsafe_allow_html=True)


def flash():
    # Keep the same element position whether or not a save message is present.
    slot = st.empty()
    message = st.session_state.pop('workspace_flash', None)
    if message:
        slot.success(message)


def select_store(key):
    stores = fetch('stores')
    if not stores:
        st.info('先建立第一家门店，再添加员工，就可以开始每月核算。')
        st.page_link('views/stores_page.py', label='建立第一家门店', icon=':material/group_add:')
        st.stop()
    selected = st.selectbox('门店', [row['id'] for row in stores],
                            format_func=lambda value: next(row['name'] for row in stores if row['id'] == value),
                            key=key)
    return next(row for row in stores if row['id'] == selected)


def month_options(existing):
    today = datetime.now(ZoneInfo('Pacific/Auckland')).date()
    options = {row['payroll_month'] for row in existing}
    serial = today.year * 12 + today.month - 1
    for offset in range(-12, 2):
        value = serial + offset
        options.add(date(value // 12, value % 12 + 1, 1))
    return sorted(options, reverse=True), today.replace(day=1)


def money(value):
    return '—' if value is None else f'¥{Decimal(str(value)):,.2f}'


def metrics(items):
    for index, (column, (label, value, detail)) in enumerate(zip(st.columns(len(items)), items)):
        with column.container(border=True, key=f'kpi_{index}'):
            st.metric(label, value)
            st.caption(detail)
