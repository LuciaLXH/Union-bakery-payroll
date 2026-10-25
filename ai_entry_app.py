"""Run separately: streamlit run ai_entry_app.py --server.address 127.0.0.1."""
from datetime import date
from pathlib import Path
import json
import os

import pandas as pd
import streamlit as st

from ai_entry.service import Budget, EntryError, Settings, extract
from ai_entry.review import COLUMNS, EXAMPLE_ITEMS, KINDS, MODES, PEOPLE, SAMPLE, confirm_rows, review_rows, STORES
from ai_entry.monthly import MonthlyStore, initial, project

st.set_page_config(page_title='Union Bakery · AI 录入实验室', page_icon='🍞', layout='wide')
st.markdown('<style>' + (Path(__file__).parent / 'ui/style.css').read_text() + '</style>', unsafe_allow_html=True)
st.title('AI 录入实验室')
st.caption('独立原型 · 全部为虚构员工 · 本地测试月度记录 · 不连接老板数据库')
st.write('输入员工姓名或编号，自动匹配门店。核对后可写入本地当月工资输入记录。')
try:
    settings = Settings.from_env()
    ledger_path = os.getenv('AI_ENTRY_LEDGER', str(Path(__file__).parent / '.local/ai-entry/usage.sqlite3'))
    budget = Budget(ledger_path)
    monthly = MonthlyStore(Path(ledger_path).parent / 'test-monthly.sqlite3')
except (EntryError, OSError) as exc:
    st.error(str(exc))
    st.stop()

month = st.date_input('工资月份（按所选日期所在月份）', date(2026, 10, 1)).strftime('%Y-%m')
context = month
with st.expander('查看虚构员工名单'):
    st.table(pd.DataFrame([{'员工编号': k, '姓名': v, '门店': STORES[k]} for k, v in PEOPLE.items()]))
    st.caption('特意保留两个“张三”和相近姓名，测试时必须核对编号。')

st.session_state.setdefault('review_generation', 0)
st.session_state.setdefault('confirmed_batches', {})
mode = st.radio('录入方式', ['AI 识别', '手动录入'], horizontal=True)
text = st.text_area('记录文字', value=SAMPLE, max_chars=3000, height=120)
if mode == 'AI 识别':
    st.caption(f'当前模型：{settings.model}。只发送这段记录和月份；员工门店由本地档案匹配。')
    if not settings.key:
        st.warning(f'尚未配置 {settings.key_variable}。先用固定示例或手动录入；配置方法见 docs/ai-entry-prototype.md。')

label = '识别文字' if mode == 'AI 识别' else '建立空白确认表'
submit = st.button(label, type='primary', disabled=mode == 'AI 识别' and not settings.key)
with st.expander('无 API 密钥时体验固定示例'):
    st.caption('载入预先编写的结果，不调用模型，也不识别上方文字。')
    sample = st.button('载入固定示例')
if submit or sample:
    # Clear stale previews before attempting a new request, including failed requests.
    st.session_state.pop('review_draft', None)
    st.session_state.review_generation += 1
    try:
        if sample:
            rows = review_rows(EXAMPLE_ITEMS)
        elif mode == '手动录入':
            rows = []
        else:
            with st.spinner('正在整理，最长等待约45秒…'):
                rows = review_rows(extract(text, '按员工档案自动匹配', month, settings, budget))
            if not rows:
                st.info('没有识别到相关事项，请补充文字或手动录入。')
        st.session_state.review_draft = {'rows': rows, 'context': context, 'text': text, 'mode': mode, 'sample': sample}
    except EntryError as exc:
        st.error(str(exc))

spent = budget.spent() / 1_000_000
cost_note = '按人民币单价记账。' if settings.provider == 'qwen' else f'按 1 美元 = {settings.usd_cny} 元记账（预算假设，非实时汇率）。'
st.caption(f'本月预算 ¥{settings.budget_cny} · 已用及预留 ¥{spent:.4f} · {cost_note}刷新不会清零。')
draft = st.session_state.get('review_draft')
if draft:
    stale = draft['context'] != context or draft['text'] != text or draft['mode'] != mode
    st.subheader('核对整理结果')
    if draft.get('sample'):
        st.info('当前为固定示例结果，原文：' + SAMPLE)
    st.caption('可直接修改、增加或删除行。确认表示你已核对每条内容及员工编号；奖罚只作测试记录。')
    if stale:
        st.warning('月份、文字或录入方式已改变，请重新生成确认表。')
    frame = pd.DataFrame(draft['rows'], columns=COLUMNS)
    frame['数量'] = pd.to_numeric(frame['数量'], errors='coerce').astype(float)
    edited = st.data_editor(frame, key=f'review_{st.session_state.review_generation}', num_rows='dynamic',
        hide_index=True, width='stretch', disabled=True if stale else ['所属门店'], column_config={
            '员工编号': st.column_config.SelectboxColumn(options=list(PEOPLE), format_func=lambda code: f'{PEOPLE[code]} · {code} · {STORES[code]}' if code in PEOPLE else code, required=True),
            '事项': st.column_config.SelectboxColumn(options=KINDS, required=True),
            '数量': st.column_config.NumberColumn(min_value=0, max_value=1000000),
            '单位': st.column_config.SelectboxColumn(options=['分钟', '天', '小时', '元', '']),
            '处理方式': st.column_config.SelectboxColumn(options=MODES, required=True, default='新增'),
            '原文 / 备注': st.column_config.TextColumn(width='large'),
            '待核对问题': st.column_config.TextColumn(width='large'),
        })
    matched = [{'员工编号': row.get('员工编号'), '姓名': PEOPLE.get(row.get('员工编号'), '待匹配'),
                '所属门店': STORES.get(row.get('员工编号'), '待匹配')} for row in edited.to_dict('records')]
    if matched:
        st.caption('按当前员工编号匹配的写入归属（更换编号后以此为准）：')
        st.dataframe(pd.DataFrame(matched).drop_duplicates(), hide_index=True)
    checked = st.checkbox('我已核对员工编号、事项、数量以及新增/累计方式', key=f'checked_{st.session_state.review_generation}')
    if st.button('确认并保留本次测试记录', disabled=stale or not checked):
        try:
            records, digest = confirm_rows(edited.to_dict('records'), context)
            if digest in st.session_state.confirmed_batches:
                raise EntryError('这批内容已经确认过，不重复添加。')
            st.session_state.confirmed_batches[digest] = {'月份': month, '记录': records}
            st.success('已确认。下方可预览变更并选择写入当月测试工资记录。')
        except EntryError as exc:
            st.error(str(exc))

if st.session_state.confirmed_batches:
    st.subheader('本次会话已确认的记录')
    batches = list(st.session_state.confirmed_batches.values())
    st.json(batches, expanded=False)
    st.download_button('下载测试记录（JSON）', json.dumps(batches, ensure_ascii=False, indent=2),
                       'ai-entry-test-records.json', mime='application/json')
    for digest, batch in st.session_state.confirmed_batches.items():
        with st.expander(f"写入 {batch['月份']} 工资记录 · {digest[:6]}"):
            st.caption('写入独立的本地虚构数据。累计值替换该项目，新增值加到原值；不自动计算或发薪。')
            try:
                revision, current = monthly.read(batch['月份'])
                updated = project(current, batch['记录'])
                changes = []
                for code in {row['员工编号'] for row in batch['记录']}:
                    before = current.get(code, initial(code))
                    for field, after in updated[code].items():
                        if before.get(field) != after:
                            changes.append({'员工': PEOPLE[code], '编号': code, '门店': STORES[code],
                                            '项目': field, '原值': str(before.get(field)), '写入后': str(after)})
                st.dataframe(pd.DataFrame(changes), hide_index=True)
                preview_key = f'preview_{digest}'
                shown_revision = st.session_state.setdefault(preview_key, revision)
                if shown_revision != revision:
                    st.warning('月度记录已有变化，请先刷新预览再写入。')
                    if st.button('刷新写入预览', key=f'refresh_{digest}'):
                        st.session_state[preview_key] = revision
                        st.rerun()
                if st.button('写入当月工资记录（测试）', key=f'write_{digest}', disabled=shown_revision != revision):
                    monthly.write(batch['月份'], batch['记录'], shown_revision)
                    st.success('已写入本地当月工资输入记录，刷新页面仍保留。')
            except EntryError as exc:
                st.warning(str(exc))

st.subheader(f'{month} 当月工资记录（测试）')
_, month_data = monthly.read(month)
if month_data:
    st.dataframe(pd.DataFrame(list(month_data.values())), hide_index=True, width='stretch')
else:
    st.caption('尚无写入记录。确认上方整理结果后，可选择写入。')
st.caption('本页仅测试工资输入；固定工资为虚构默认值。奖励和额外扣款尚不支持写入。')
