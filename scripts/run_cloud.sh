#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?请在云端设置 DATABASE_URL}"
: "${TEST_PASSWORD_HASH:?请在云端设置 TEST_PASSWORD_HASH}"
export APP_MODE=online-test
python -c 'import os; from core.access import parse_hash; parse_hash(os.environ["TEST_PASSWORD_HASH"])'
exec python -m streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-10000}" --server.headless true
