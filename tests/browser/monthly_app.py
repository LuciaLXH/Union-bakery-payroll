"""Isolated browser-test entry point. Never used by deployment scripts."""
import os,sys,time,runpy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import streamlit as st
from ui import common,ai_entry
from ai_entry.service import Settings
common.DB_SCHEMA=os.environ['PAYROLL_BROWSER_SCHEMA']
original_db=getattr(common, "_ghost_original", common.db)
common._ghost_original=original_db
original_call=getattr(ai_entry, "_ghost_original", ai_entry.call)
ai_entry._ghost_original=original_call
def audit(kind, args):
    with open(os.environ['PAYROLL_BROWSER_AUDIT'], 'a') as log:
        log.write(json.dumps({'kind':kind,'method':args[0]})+'\n')

def slow_db(*args,**kwargs):
    audit('payroll', args)
    time.sleep(.2)
    return original_db(*args,**kwargs)
def slow_call(*args,**kwargs):
    audit('ai', args)
    time.sleep(.2)
    return original_call(*args,**kwargs)
common.db=slow_db
ai_entry.call=slow_call
ai_entry.Settings.from_env=lambda: Settings(key='test-key')
def fake_extract(text,*args,**kwargs):
    time.sleep(.6)
    return [dict(name='TEST-001',kind='迟到',amount=5,unit='分钟',mode='新增',source=text,question='')]
ai_entry.extract=fake_extract
original_page=getattr(st,"_ghost_page",st.Page)
st._ghost_page=original_page
st.Page=lambda path,*args,**kwargs:original_page(str(ROOT/path),*args,**kwargs)
original_link=getattr(st,'_ghost_link',st.page_link)
st._ghost_link=original_link
st.page_link=lambda path,*args,**kwargs:original_link(str(ROOT/path) if isinstance(path,str) and path.startswith('views/') else path,*args,**kwargs)
runpy.run_path(str(ROOT/'app.py'),run_name='__main__')
