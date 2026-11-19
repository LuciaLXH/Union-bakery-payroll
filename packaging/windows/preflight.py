"""Windows runtime check; contains no account or connection settings."""
import ctypes
import os
from pathlib import Path
import platform
import sys

if os.name != 'nt' or platform.machine().lower() not in ('amd64','x86_64'):
    sys.exit('此包面向 Windows 10/11 Intel/AMD 64位电脑。')
if sys.getwindowsversion().major < 10:
    sys.exit('此包需要 Windows 10/11。')
try:
    ctypes.WinDLL('msvcp140.dll')
except OSError:
    sys.exit('缺少微软运行库。请先双击“00-补充运行库.cmd”，完成后重新启动。')
try:
    import streamlit, pandas, pyarrow, psycopg, openpyxl, xlsxwriter
except Exception:
    sys.exit('运行环境检查未通过。请确认已完整解压到较短的目录，并联系维护者。')
print('Windows 运行环境检查通过。')
