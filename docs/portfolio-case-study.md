# Case study: Union Bakery payroll workspace

## Context

A small bakery owner needed a practical monthly payroll workflow. The initial spreadsheet import template did not match the actual payroll sheet. Access to real employee data was limited, so development and acceptance testing used fictional cases and concrete examples supplied by the owner.

## Discovery that changed the implementation

The owner clarified that regular salary included a social insurance allowance, most employees were not paid as a percentage of turnover, and apprentice mistakes should appear as notes without automatically reducing pay. Follow-up examples resolved the difference between ordinary absence and unauthorised absence, and established the exact overtime divisor and monthly store limit.

The resulting workflow places reusable employee records before attendance entry. A month captures its own pay snapshot; changes to an employee’s regular salary affect new records instead of rewriting earlier months. The monthly limit compares against the sum of current net pay, with the amount hidden outside its settings dialog.

## Technical implementation

Python and Streamlit provide the interface. The operational test deployment uses PostgreSQL on Neon and an application service on Render. Five tables separate stores, employees, payroll months, monthly inputs and calculation versions. Monetary fields use PostgreSQL NUMERIC and Python Decimal; components are rounded before totals are formed.

The storage layer locks the relevant month and inputs while saving and calculating. Input versions distinguish a current result from older calculations. Employee batch edits are atomic, and later profile changes do not retroactively change monthly snapshots.

The English portfolio preserves these concepts and reuses the calculation engine. Its session repository simulates the workflow with bounded fictional data. It intentionally has no database connection: visitors cannot inspect or edit the Chinese test environment, and one visitor’s edits do not change another visitor’s demo. Database transaction and concurrency guarantees belong to the PostgreSQL implementation and are not claimed for the session adapter.

## What was delivered

- Chinese owner-testing workspace and an English portfolio interface.
- Individual and batch employee entry, store setup and monthly attendance editing.
- Clear save-versus-calculate actions, stale-result handling and version inspection.
- Hidden payroll-limit amount with an explicit settings entry point.
- English Excel exports containing currency and calculation notes.
- Versioned schema, deployment scripts and automated regression tests.

Owner feedback informed the design and the previous calculation checks. No time-saving percentage, adoption metric or production payroll volume has been measured, so none is claimed here.

## Boundaries and next steps

The rules reflect this bakery’s stated requirements, not a universal payroll compliance model. The operational environment remains a fictional-data acceptance test. The public demo has no employee login, actual payment, payslip distribution, or email automation. The [roadmap](product-roadmap.md) describes the dependencies and acceptance checks for those features.
