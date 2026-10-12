#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export DATABASE_URL="${DATABASE_URL:-postgresql://localhost:5432/bakery_payroll_dev}"
exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 18501 "$@"
