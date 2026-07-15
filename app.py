"""
Streamlit 薪酬核算系统主入口文件
初始化全局状态并配置多页面导航路由。
"""

import streamlit as st

# ==================== 1. 初始化全局 Session State ====================
if 'employees_df' not in st.session_state:
    st.session_state.employees_df = None

if 'loss_df' not in st.session_state:
    st.session_state.loss_df = None

if 'store_sales' not in st.session_state:
    # 默认三家分店的月营业额（元），供老板手动微调修改
    st.session_state.store_sales = {
        "分店A": 80000.0,
        "分店B": 95000.0,
        "分店C": 110000.0
    }

if 'social_security_std' not in st.session_state:
    # 默认社保扣减标准金额
    st.session_state.social_security_std = 1000.0

if 'payroll_df' not in st.session_state:
    st.session_state.payroll_df = None

# ==================== 2. 配置多页面路由 ====================
# 使用 Streamlit 1.35+ 的 st.navigation 和 st.Page 规范
import_page = st.Page("views/import_page.py", title="1. 数据导入与配置", icon="📥")
editor_page = st.Page("views/editor_page.py", title="2. 薪酬核算与微调", icon="✏️")
export_page = st.Page("views/export_page.py", title="3. 经营分析与导出", icon="📊")

# 分组导航
pg = st.navigation({
    "数据初始化": [import_page],
    "核心算薪中心": [editor_page],
    "报表与分析": [export_page]
})

# ==================== 3. 页面全局配置与侧边栏渲染 ====================
st.set_page_config(
    page_title="面包店多门店月度薪酬核算系统",
    page_icon="🍞",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 侧边栏标题与简短介绍
st.sidebar.markdown("# 🍞 烘焙工坊薪酬系统")
st.sidebar.caption("轻量化多分店月度算薪 MVP 工具")
st.sidebar.divider()

# 运行当前路由页面
pg.run()
