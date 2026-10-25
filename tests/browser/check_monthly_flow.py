"""Browser regression: real isolated PostgreSQL, mocked AI, delayed reads.

Run from the project root with TEST_DATABASE_URL and PLAYWRIGHT_CHROMIUM set.
No production data or API key is used. Requires the optional playwright package.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import psycopg
from psycopg import sql
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
from test_database_schema import DatabaseFixture
from core.workspace import PayrollWorkspace

expect.set_options(timeout=15000)
fixture = DatabaseFixture()
fixture.setUp()
fixture.connection.commit()
process = None
footer = '当前为核算草稿。已保存的数据下次打开仍可使用；尚未保存的填写内容仅留在当前网页会话。'
try:
    with tempfile.TemporaryDirectory(prefix='payroll-browser-') as tmp:
        audit = Path(tmp) / 'reads.jsonl'
        audit.touch()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, DATABASE_URL=os.environ['TEST_DATABASE_URL'], APP_MODE='local', RENDER='',
                   PAYROLL_BROWSER_SCHEMA=fixture.schema, PAYROLL_BROWSER_AUDIT=str(audit))
        log = open(Path(tmp) / 'server.log', 'w')
        process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', 'tests/browser/monthly_app.py',
            '--server.port', str(port), '--server.address', '127.0.0.1', '--server.headless', 'true',
            '--server.fileWatcherType', 'none'], cwd=ROOT, env=env, stdout=log, stderr=log)
        url = f'http://127.0.0.1:{port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                opener.open(url + '/_stcore/health', timeout=.5).close()
                break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Test server did not start')
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM'))
            page = browser.new_page(viewport=dict(width=1440, height=900))
            page.goto(url)
            expect(page.get_by_text(footer, exact=True)).to_be_visible()
            expect(page.get_by_test_id('stMetric')).to_have_count(3)

            def settled():
                # Wait for the frontend to retire old blocks, then check the real DOM.
                page.wait_for_function('''() => !document.querySelector('[data-testid="stMain"] [data-stale="true"]')''')
                expect(page.get_by_test_id('stException')).to_have_count(0)
                expect(page.get_by_role('heading', name='核对已保存的考勤与备注', exact=True)).to_have_count(1)

            def reads():
                return [json.loads(line) for line in audit.read_text().splitlines()]

            def button(name):
                return page.get_by_role('button', name=name, exact=False)

            def position_action(locator):
                locator.scroll_into_view_if_needed()
                locator.evaluate('''e => {let main=document.querySelector('[data-testid="stMain"]');
                    main.scrollTop += e.getBoundingClientRect().top - 430;
                    window.actionScroll=[];window.actionWatch=setInterval(()=>window.actionScroll.push(main.scrollTop),20)}''')
                return page.get_by_test_id('stMain').evaluate('e=>e.scrollTop')

            def check_scroll(before):
                settled()
                values = page.evaluate('() => {clearInterval(window.actionWatch);return window.actionScroll}')
                after = page.get_by_test_id('stMain').evaluate('e=>e.scrollTop')
                assert before > 200 and after > 200, (before, after)
                assert not values or min(values) > 100, (before, after, min(values))
                return dict(before=round(before), after=round(after), minimum=round(min(values or [after])))

            page.evaluate('''() => {window.cards=[];window.cardWatch=setInterval(()=>window.cards.push(
                [...document.querySelectorAll('h3')].filter(e=>e.textContent.includes('核对已保存的考勤与备注')).length),20)}''')
            results = []
            for cycle in range(3):
                settled()
                page.evaluate("window.payrollNode=document.querySelector('.st-key-month_entry')")
                start = len(reads())
                page.get_by_label('粘贴本月记录', exact=True).fill(f'虚构员工10月{8+cycle}日迟到5分钟')
                button('识别文字').click()
                expect(button('预览写入变更')).to_be_enabled()
                button('预览写入变更').click()
                checkbox = page.get_by_text('我已核对所属门店、员工、月份、数量及新增/累计方式', exact=True)
                before = position_action(checkbox)
                checkbox.click()
                expect(button('写入当月工资记录')).to_be_enabled()
                checkbox_scroll = check_scroll(before)
                assert page.evaluate("window.payrollNode===document.querySelector('.st-key-month_entry')")
                assert not any(r['kind']=='payroll' for r in reads()[start:]), reads()[start:]
                before = position_action(button('写入当月工资记录'))
                button('写入当月工资记录').click()
                expect(page.get_by_text('已写入 1 条事项及原文。', exact=False)).to_be_visible()
                expect(button('写入当月工资记录')).to_be_disabled()
                write_scroll = check_scroll(before)
                page.evaluate("window.aiNode=document.querySelector('.st-key-monthly_ai_entry')")
                start = len(reads())
                before = position_action(button('保存并计算本月工资'))
                started = time.monotonic()
                button('保存并计算本月工资').click()
                expect(page.get_by_role('heading', name='本月计算结果', exact=True)).to_be_visible()
                calc_scroll = check_scroll(before)
                assert page.evaluate("window.aiNode===document.querySelector('.st-key-monthly_ai_entry')")
                actions = reads()[start:]
                assert actions == [{'kind':'payroll','method':'save_inputs'}, {'kind':'payroll','method':'month_view'}], actions
                results.append(dict(cycle=cycle+1, checkbox=checkbox_scroll, write=write_scroll,
                                    calculate=calc_scroll, calculate_seconds=round(time.monotonic()-started,2)))
            cards = page.evaluate('() => {clearInterval(window.cardWatch);return window.cards}')
            assert max(cards) == 1, max(cards)
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
                repo = PayrollWorkspace(db, schema=fixture.schema)
                batch = repo.months()[0]
                assert repo.entries(batch['id'])[0]['late_minutes'] == 22
                assert repo.current_results(batch['id'])[0]['late_deduction'] == 22
                assert len(repo.current_results(batch['id'])) == 1
            page.screenshot(path=str(ROOT / '.local/ai-entry/fragment-verified.png'), full_page=True)
            print(json.dumps(dict(cycles=results, maximum_attendance_cards=max(cards), persisted_late_minutes=22), ensure_ascii=False))
            browser.close()
finally:
    if process:
        process.terminate()
        process.wait(timeout=10)
    fixture.connection.close()
    with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as db:
        db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(fixture.schema)))
