#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec python -m streamlit run portfolio_app.py --server.address "${BIND_ADDRESS:-127.0.0.1}" --server.port "${PORT:-18502}" --server.headless true
