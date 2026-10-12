"""English portfolio UI. Public demo state is isolated per Streamlit session."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

import config
from core.payroll_storage import RESULT_FIELDS
from core.workspace import EMPLOYEE_COLUMNS, EMPLOYEE_DEFAULTS, employee_frame, entry_frame, result_frame
from portfolio.demo import DemoWorkspace
from portfolio.domain import LABELS, english_frame, export_english, validation_message


def repo():
    if 'portfolio_repo' not in st.session_state:
        st.session_state.portfolio_repo = DemoWorkspace()
    return st.session_state.portfolio_repo


def money(value):
    return '—' if value is None else f'CNY {Decimal(str(value)):,.2f}'


def header(kicker, title, description):
    from html import escape
    st.markdown(f'<div class="page-heading"><div class="eyebrow">UNION BAKERY / {escape(kicker)}</div>'
                f'<h1>{escape(title)}</h1><p>{escape(description)}</p></div>', unsafe_allow_html=True)
    if message := st.session_state.pop('portfolio_flash', None):
        st.success(message)


def select_store(key):
    stores = repo().stores()
    selected = st.selectbox('Store', [s['id'] for s in stores],
        format_func=lambda value: next(s['name'] for s in stores if s['id'] == value), key=key)
    return next(s for s in stores if s['id'] == selected)


def month_choices(existing):
    today = date.today().replace(day=1)
    serial = today.year * 12 + today.month - 1
    months = {m['payroll_month'] for m in existing}
    for offset in range(-12, 2):
        n = serial + offset
        months.add(date(n // 12, n % 12 + 1, 1))
    return sorted(months, reverse=True), today


def flash(message):
    st.session_state.portfolio_flash = message
    st.rerun()


def metric_cards(items):
    for index, (col, (label, value, hint)) in enumerate(zip(st.columns(len(items)), items)):
        with col.container(border=True, key=f'kpi_{index}'):
            st.metric(label, value)
            st.caption(hint)


def month_edit(key, month_id, index, column):
    st.session_state.portfolio_months[month_id]['frame'].at[index, column] = st.session_state[key]


def month_table_edit(key, month_id):
    buffer = st.session_state.portfolio_months[month_id]
    for row, changes in st.session_state[key]['edited_rows'].items():
        for column, value in changes.items():
            buffer['frame'].at[int(row), column] = value
    buffer['revision'] += 1


def close_limit():
    st.session_state.pop('portfolio_limit', None)


@st.dialog('Set monthly payroll limit', on_dismiss=close_limit)
def limit_dialog(month):
    st.caption('Compare this limit with the store’s total calculated net pay. The amount stays hidden on the main page.')
    with st.form(f"en_limit_{month['id']}"):
        enabled = st.checkbox('Enable a limit for this month', value=month['payroll_cap_snapshot'] is not None)
        amount = st.number_input('Monthly limit (CNY)', min_value=0.0, max_value=999999999999.99,
                                 value=float(month['payroll_cap_snapshot'] or 0), step=1000.0)
        st.caption('Reaching the limit displays a promotion restriction notice. This demo does not enforce promotion approvals.')
        if st.form_submit_button('Save monthly limit', type='primary'):
            try:
                repo().set_cap(month['id'], amount if enabled else None, month['payroll_cap_snapshot'])
                close_limit()
                flash('Monthly limit saved in this demo session.')
            except ValueError as exc:
                st.error(validation_message(exc))


def monthly():
    header('MONTHLY PAYROLL', 'A clear month. A confident payday.',
           'Choose a store and month, record attendance, then save and calculate. Regular pay is carried forward automatically.')
    st.markdown('<div class="flow-steps"><span><b>01</b> Choose a month</span><span><b>02</b> Record attendance</span>'
                '<span><b>03</b> Calculate & review</span></div>', unsafe_allow_html=True)
    with st.expander('Try a two-minute walkthrough'):
        st.write('Alex Baker starts at CNY 5,263. Change late minutes from 7 to 10, then select Save & calculate. '
                 'The result becomes CNY 5,260. Open Calculation history to compare both versions.')
        st.caption('All people and figures are fictional. The store-specific rules and currency are unchanged from the Chinese app.')
    with st.container(border=True):
        a, b = st.columns(2)
        with a:
            store = select_store('en_month_store')
        months = repo().months(store['id'])
        choices, current = month_choices(months)
        with b:
            selected = st.selectbox('Pay month', choices, index=choices.index(current),
                                    format_func=lambda d: d.strftime('%B %Y'), key='en_month')
    month = next((m for m in months if m['payroll_month'] == selected), None)
    if month is None:
        st.info('This month has not been started. Bring in active employees and their current regular pay.')
        st.caption('Attendance starts at zero. Mid-month joiners need a manual pay review; there is no automatic proration.')
        if st.button('Start this month', type='primary'):
            try:
                _, count = repo().start_month(store['id'], selected)
                flash(f'Month created with {count} employees. Please review attendance.')
            except ValueError as exc:
                st.error(validation_message(exc))
        return
    entries = repo().entries(month['id'])
    buffers = st.session_state.setdefault('portfolio_months', {})
    if month['id'] not in buffers:
        frame = entry_frame(entries, store['name'])
        buffers[month['id']] = dict(frame=frame, base=frame.copy(deep=True),
            versions={e['id']: e['input_version'] for e in entries}, revision=0, generation=uuid4().hex)
    buffer = buffers[month['id']]
    dirty = not buffer['frame'].equals(buffer['base'])
    outdated = buffer['versions'] != {e['id']: e['input_version'] for e in entries}
    results = repo().current_results(month['id'])
    complete = bool(entries) and len(results) == len(entries) and not dirty and not outdated
    total = sum((r['net_pay'] for r in results), Decimal(0)) if complete else None
    with st.container(border=True, key='payroll_cap_settings'):
        a, b = st.columns([2, 1], vertical_alignment='center')
        with a:
            st.subheader('Payroll limit', anchor=False)
            st.caption('Set · amount visible only in settings' if month['payroll_cap_snapshot'] is not None else 'Not set for this month')
        with b:
            if st.button('Set monthly limit', type='primary', icon=':material/tune:', width='stretch'):
                st.session_state.portfolio_limit = month['id']
    if st.session_state.get('portfolio_limit') == month['id']:
        limit_dialog(month)
    metric_cards([('Employees', str(len(entries)), 'Check that the team is complete'),
                  ('Total net pay', money(total), 'CNY · all employees in this month'),
                  ('Status', 'Unsaved changes' if dirty else 'Calculated' if complete else 'Awaiting calculation', 'Save again after making changes')])
    if complete and month['payroll_cap_snapshot'] is not None:
        if total >= month['payroll_cap_snapshot']:
            st.warning('Monthly payroll limit reached. No promotion headroom under the store’s current rule.')
        else:
            st.info('Payroll is below the monthly limit. This does not automatically approve a promotion.')
    if not entries:
        st.info('There are no employees in this month. Add an employee, then bring new employees into the month below.')
    else:
        with st.container(border=True):
            st.subheader('Attendance & performance notes')
            if outdated:
                st.warning('These inputs changed. Reload saved inputs before continuing.')
            elif dirty:
                st.warning('Unsaved changes. Use Save & calculate to update this month’s results.')
            else:
                st.caption('Use zero for attendance that did not occur. Notes never create a deduction.')
            mode = st.radio('Entry method', ['By employee', 'Edit as a table'], horizontal=True, key='en_month_mode')
            prefix = f"en_input_{month['id']}_{buffer['generation']}_{buffer['revision']}"
            if mode == 'By employee':
                index = st.selectbox('Employee', list(buffer['frame'].index),
                    format_func=lambda i: f"{buffer['frame'].at[i, '姓名']} · {buffer['frame'].at[i, '员工编号']}", key='en_employee')
                row = buffer['frame'].loc[index]
                columns = st.columns(2)
                for n, (column, label, minimum, maximum, step) in enumerate([
                    ('普通缺勤天数', 'Absence (days)', 0, 30, 1),
                    ('旷工天数', 'Unauthorised absence (days)', 0, 30, 1),
                    ('迟到', 'Late minutes', 0, None, 1),
                    ('有效加班时长', 'Overtime (hours)', 0.0, None, 0.5),
                ]):
                    key = f'{prefix}_{index}_{column}'
                    value = None if pd.isna(row[column]) else (float(row[column]) if isinstance(step, float) else int(row[column]))
                    columns[n % 2].number_input(label, min_value=minimum, max_value=maximum, value=value,
                        step=step, key=key, disabled=outdated, on_change=month_edit, args=(key, month['id'], index, column))
                st.caption('Do not count a day in both absence categories. Combined absence cannot exceed 30 days.')
                key = f'{prefix}_{index}_notes'
                st.text_area('Performance notes (no pay deduction)', value=row['工作失误项目'] or '', key=key,
                    max_chars=2000, disabled=outdated, on_change=month_edit, args=(key, month['id'], index, '工作失误项目'))
                with st.expander('Review or adjust this employee’s pay for this month'):
                    st.caption('Amounts before attendance deductions, in CNY. Edit Employees for future months’ regular pay.')
                    columns = st.columns(2)
                    for n, column in enumerate(config.MONEY_COLUMNS):
                        key = f'{prefix}_{index}_{column}'
                        columns[n % 2].number_input(LABELS[column] + ' (CNY)', min_value=0.0, max_value=999999999.99,
                            value=None if pd.isna(row[column]) else float(row[column]), step=100.0, key=key, disabled=outdated,
                            on_change=month_edit, args=(key, month['id'], index, column))
            else:
                st.info('Double-click a cell, edit, then press Enter. Use the button below to save the whole month.')
                order = ['姓名', '员工编号', *config.COUNT_COLUMNS, *config.MONEY_COLUMNS, '工作失误项目']
                columns = {c: st.column_config.TextColumn(LABELS[c]) for c in buffer['frame'].columns}
                for c in config.MONEY_COLUMNS + config.COUNT_COLUMNS:
                    columns[c] = st.column_config.NumberColumn(LABELS[c], min_value=0.0,
                        max_value=30 if c in ('普通缺勤天数', '旷工天数') else 999999999.99,
                        step=1 if c in ('普通缺勤天数', '旷工天数', '迟到') else 0.01, required=True)
                st.data_editor(buffer['frame'], key=prefix, column_order=order, column_config=columns,
                    disabled=True if outdated else ['姓名', '员工编号', '分店', '岗位', '职级', '入职时间'], hide_index=True, width='stretch',
                    on_change=month_table_edit, args=(prefix, month['id']))
            st.divider()
            a, b = st.columns(2)
            calculate = a.button('Save & calculate', type='primary', width='stretch', disabled=outdated)
            save_only = b.button('Save inputs only', width='stretch', disabled=outdated)
            st.caption('Saves all edits for this store and month within this demo session. No payment or email is sent.')
            if calculate or save_only:
                try:
                    repo().save_inputs(month['id'], buffer['frame'], buffer['versions'], calculate=calculate)
                    buffers.pop(month['id'])
                    flash('Saved and calculated. Review the results below.' if calculate else 'Inputs saved. Calculate when you are ready.')
                except ValueError as exc:
                    st.error(validation_message(exc))
    if complete:
        with st.container(border=True):
            st.subheader('This month’s results')
            st.success(f'Saved in this session · {len(entries)} employees · Net pay {money(total)}')
            fields = ['姓名', '工资扣款', '绩效扣款', '全勤扣款', '迟到扣款', '加班费', '合计']
            st.dataframe(english_frame(result_frame(results)[fields]), hide_index=True, width='stretch')
            st.download_button('Download payroll detail (Excel)', export_english(results),
                f'{selected:%Y-%m}_payroll_CNY.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    elif entries:
        st.info('Downloads are unavailable until the latest inputs have been saved and calculated.')
    with st.expander('Employee list & saved inputs'):
        st.caption('Adding new employees keeps existing attendance and monthly pay unchanged.')
        if st.button('Bring new employees into this month', disabled=dirty or outdated):
            try:
                _, count = repo().start_month(store['id'], selected)
                buffers.pop(month['id'], None)
                flash(f'Added {count} employees. Existing monthly inputs were preserved.')
            except ValueError as exc:
                st.error(validation_message(exc))
        if st.button('Discard edits & reload inputs' if dirty else 'Reload saved inputs'):
            buffers.pop(month['id'], None)
            st.rerun()


def employee_table_edit(key, store_id):
    buffer = st.session_state.portfolio_employees[store_id]
    edits = st.session_state[key]
    frame = buffer['frame'].copy(deep=True)
    for row, values in edits['edited_rows'].items():
        for column, value in values.items():
            if column != '_id':
                frame.at[int(row), column] = value
    frame = frame.drop(index=edits['deleted_rows']).reset_index(drop=True)
    rows = frame.to_dict('records')
    for values in edits['added_rows']:
        row = dict(EMPLOYEE_DEFAULTS, **values)
        row['_id'] = None
        rows.append(row)
    buffer['frame'] = pd.DataFrame(rows, columns=frame.columns)
    buffer['revision'] += 1


def employees():
    header('OUR TEAM', 'People behind the pastries.',
           'Maintain regular pay, roles and grades. Changes apply to new monthly records, preserving existing pay snapshots.')
    store = select_store('en_people_store')
    people = repo().employees(store['id'])
    metric_cards([('Employees', str(len(people)), 'Unique IDs, even for shared names'),
                  ('Active', str(sum(p['is_active'] for p in people)), 'Included when starting a month'),
                  ('Inactive', str(sum(not p['is_active'] for p in people)), 'Historical records are preserved')])
    mode = st.radio('Entry method', ['By employee', 'Edit as a table'], horizontal=True, key='en_people_mode')
    if mode == 'Edit as a table':
        buffers = st.session_state.setdefault('portfolio_employees', {})
        if store['id'] not in buffers:
            frame = employee_frame(people)
            buffers[store['id']] = dict(frame=frame, expected=people, revision=0, generation=uuid4().hex)
        buffer = buffers[store['id']]
        st.subheader('Add or edit employees in one table')
        st.caption('Use the + row to add an employee. Uncheck Active for leavers. Save all rows together; amounts are in CNY.')
        key = f"en_people_{store['id']}_{buffer['generation']}_{buffer['revision']}"
        columns = {'_id': None}
        for c in EMPLOYEE_COLUMNS.values():
            if c == '在职':
                columns[c] = st.column_config.CheckboxColumn('Active', default=True)
            elif c == '入职日期':
                columns[c] = st.column_config.DateColumn('Start date', format='YYYY-MM-DD')
            elif c in ('约定工资', '月绩效', '月全勤', '通勤补贴'):
                columns[c] = st.column_config.NumberColumn(LABELS[c] + ' (CNY)', min_value=0.0,
                    max_value=999999999.99, step=100.0, default=0.0)
            else:
                columns[c] = st.column_config.TextColumn(LABELS[c])
        st.data_editor(buffer['frame'], key=key, column_config=columns, disabled=['_id'],
            num_rows='dynamic', hide_index=True, width='stretch', on_change=employee_table_edit, args=(key, store['id']))
        a, b = st.columns(2)
        if a.button('Save all employee records', type='primary', disabled=buffer['frame'].empty):
            try:
                saved = repo().save_employees(store['id'], buffer['frame'], buffer['expected'])
                buffers.pop(store['id'])
                flash(f'Saved {len(saved)} employee records. Existing months are unchanged.')
            except ValueError as exc:
                st.error(validation_message(exc))
        if b.button('Discard edits & reload employees'):
            buffers.pop(store['id'])
            st.rerun()
        return
    if people:
        st.dataframe(english_frame(employee_frame(people).drop(columns='_id')), hide_index=True, width='stretch')
    selected = st.selectbox('Add an employee or edit a record', [None] + [p['id'] for p in people],
        format_func=lambda value: '+ Add employee' if value is None else next(f"{p['name']} · {p['employee_code']}" for p in people if p['id'] == value))
    original = next((p for p in people if p['id'] == selected), {})
    with st.form(f"en_employee_form_{store['id']}_{selected}", clear_on_submit=selected is None):
        a, b = st.columns(2)
        code = a.text_input('Employee ID', value=original.get('employee_code', ''), max_chars=50, placeholder='e.g. DEMO-004')
        name = b.text_input('Name', value=original.get('name', ''), max_chars=100)
        a, b, c = st.columns(3)
        role = a.text_input('Role', value=original.get('job_title', ''), max_chars=100)
        grade = b.text_input('Grade', value=original.get('grade', ''), max_chars=100, placeholder='e.g. Beginner apprentice')
        hire = c.date_input('Start date (optional)', value=original.get('hire_date'))
        amounts = {}
        for col, field, label in zip(st.columns(2) * 2,
            ('monthly_salary', 'monthly_performance', 'monthly_attendance_bonus', 'monthly_commute_allowance'),
            ('Base pay incl. allowance', 'Performance pay', 'Attendance bonus', 'Travel allowance')):
            amounts[field] = col.number_input(label + ' (CNY)', min_value=0.0, max_value=999999999.99,
                                              value=float(original.get(field, 0)), step=100.0)
        active = st.checkbox('Active · include in new pay months', value=original.get('is_active', True))
        st.caption('Use 0 when a component does not apply. Regular pay includes the agreed social insurance allowance.')
        if st.form_submit_button('Save employee', type='primary'):
            try:
                repo().save_employee(store['id'], dict(employee_code=code, name=name, job_title=role,
                    grade=grade, hire_date=hire, is_active=active, **amounts), employee_id=selected, expected=original)
                st.session_state.get('portfolio_employees', {}).pop(store['id'], None)
                flash('Employee saved. Bring new employees into an existing month from Monthly payroll.')
            except ValueError as exc:
                st.error(validation_message(exc))


def stores():
    header('OUR STORES', 'Room for another neighbourhood.',
           'Create a store, add its team, then start a pay month. Payroll limits are set separately for each month.')
    st.dataframe(pd.DataFrame([{'Store': s['name']} for s in repo().stores()]), hide_index=True, width='stretch')
    with st.form('en_new_store', clear_on_submit=True):
        name = st.text_input('Store name', max_chars=100, placeholder='e.g. Union · Northside')
        if st.form_submit_button('Add store', type='primary'):
            try:
                repo().add_store(name)
                flash('Store added. Open Employees to create its team.')
            except ValueError as exc:
                st.error(validation_message(exc))


def history():
    header('CALCULATION HISTORY', 'Every change has a story.',
           'Review earlier attendance and pay snapshots. Only the version matching the latest inputs is current.')
    store = select_store('en_history_store')
    months = repo().months(store['id'])
    if not months:
        st.info('Start a pay month to create the first record.')
        return
    selected = st.selectbox('Pay month', [m['payroll_month'] for m in months], format_func=lambda d: d.strftime('%B %Y'))
    month = next(m for m in months if m['payroll_month'] == selected)
    entries = repo().entries(month['id'])
    versions = {e['id']: e['input_version'] for e in entries}
    records = repo().history(store_name=store['name'], payroll_month=selected)
    if not records:
        st.info('No calculations yet. Use Save & calculate on Monthly payroll.')
        return
    current = lambda r: versions.get(r['payroll_entry_id']) == r['input_version'] and r['rule_version'] == config.RULE_VERSION
    records = list(reversed(records))
    st.dataframe(pd.DataFrame([{'Name': r['input_snapshot']['employee_name_snapshot'],
        'Employee ID': r['input_snapshot']['employee_code_snapshot'], 'Version': r['input_version'],
        'Net pay (CNY)': float(r['net_pay']), 'Status': 'Current' if current(r) else 'Historical',
        'Saved (UTC)': r['calculated_at'].strftime('%Y-%m-%d %H:%M:%S')} for r in records]), hide_index=True, width='stretch')
    st.caption('Historical versions must not be added together as a monthly payroll total.')
    identity = st.selectbox('Inspect a calculation', [r['id'] for r in records], format_func=lambda value: next(
        f"{r['input_snapshot']['employee_name_snapshot']} · v{r['input_version']} · {'Current' if current(r) else 'Historical'} · {money(r['net_pay'])}"
        for r in records if r['id'] == value))
    record = next(r for r in records if r['id'] == identity)
    details = result_frame([record]).iloc[0]
    a, b = st.columns(2)
    with a:
        st.subheader('Inputs at that time')
        st.dataframe(pd.DataFrame([{'Input': LABELS[c], 'Value': str(details[c])} for c in config.MONEY_COLUMNS + config.COUNT_COLUMNS]), hide_index=True, width='stretch')
    with b:
        st.subheader('Calculated amounts · CNY')
        st.dataframe(pd.DataFrame([{'Component': LABELS[c], 'Amount': money(details[c])} for c in RESULT_FIELDS.values()]), hide_index=True, width='stretch')
    st.write('Performance notes:', details['工作失误项目'] or 'None')
    st.caption(f"Rule version: {record['rule_version']}")


def about():
    header('PROJECT NOTES', 'From spreadsheet friction to a clearer workflow.',
           'A client-informed payroll workspace for a small bakery, built through real feedback and iterative delivery.')
    a, b = st.columns(2)
    with a:
        st.subheader('The operational problem')
        st.write('A bakery owner needed a repeatable way to maintain employees, capture attendance and explain monthly pay. '
                 'The original spreadsheet template did not match the store’s actual workflow, and real payroll data could not be shared for development.')
        st.subheader('What changed')
        st.write('Owner feedback shaped the salary components, apprentice notes, absence rules and monthly store limit. '
                 'The interface moved from repeated spreadsheet uploads to reusable employee records and monthly attendance entry.')
    with b:
        st.subheader('Architecture in use')
        st.write('The Chinese owner-testing environment runs Streamlit on Render with a separate Neon PostgreSQL database. '
                 'The database stores employees, monthly snapshots, attendance inputs and versioned calculations.')
        st.subheader('How this public demo differs')
        st.write('This English portfolio uses the same Decimal calculation engine with an isolated, in-memory repository per visitor session. '
                 'It never connects to the owner’s database. Refreshing or resetting starts a fresh demo; no real salaries should be entered.')
    st.subheader('Engineering decisions')
    st.markdown('- Decimal arithmetic and explicit rounding for reproducible amounts.\n'
                '- Monthly snapshots so later changes to regular pay do not rewrite earlier months.\n'
                '- Version checks and database transactions in the PostgreSQL implementation.\n'
                '- Performance notes separated from monetary deductions.\n'
                '- Batch editing for repeated work, with clear save and calculation steps.')
    with st.expander('Calculation rules used in this case study'):
        st.code('Salary deduction = salary / 30 × (absence days + 3 × unauthorised days)\n'
                'Performance / attendance deduction = each component / 30 × total absence days\n'
                'Late deduction = late minutes × CNY 1\n'
                'Overtime = salary / 30 / 9 × 1.5 × overtime hours\n'
                'Net pay = regular components + overtime − deductions', language='text')
        st.caption('Store-specific requirements, not a general payroll or New Zealand compliance model. Currency: CNY. Translation does not change the rules.')
    st.subheader('Planned next steps')
    st.write('Individual payslips, reviewed email delivery, manager attendance access and a monthly analytics dashboard. '
             'These are roadmap items, not features available in this demo.')
    st.link_button('View source & architecture on GitHub', 'https://github.com/LuciaLXH/Union-bakery-payroll')


def run():
    st.set_page_config(page_title='Union Bakery · Payroll Studio', page_icon='🍞', layout='wide')
    st.markdown('<style>' + (Path(__file__).resolve().parents[1] / 'ui/style.css').read_text() + '</style>', unsafe_allow_html=True)
    pages = [st.Page(monthly, title='Monthly payroll', icon=':material/payments:', default=True),
             st.Page(employees, title='Employees', icon=':material/group:'),
             st.Page(stores, title='Stores', icon=':material/storefront:'),
             st.Page(history, title='Calculation history', icon=':material/history:'),
             st.Page(about, title='About this project', icon=':material/info:')]
    page = st.navigation(pages, position='hidden')
    with st.sidebar:
        st.markdown('<div class="brand"><span class="brand-name">Union</span><small>BAKERY</small>'
                    '<span class="brand-label">Payroll Studio</span></div>', unsafe_allow_html=True)
        st.caption('THE WORKSPACE')
        for p in pages:
            st.page_link(p)
        st.divider()
        st.caption('PORTFOLIO DEMO · FICTIONAL DATA')
        st.caption('Your edits stay in this session only. Refreshing starts again. Currency: CNY.')
        if st.button('Reset my demo', help='Replaces this session’s fictional records with the original sample.'):
            for key in list(st.session_state):
                if key.startswith(('portfolio_', 'en_')):
                    del st.session_state[key]
            st.rerun()
    page.run()
