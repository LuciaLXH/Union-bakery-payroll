#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?请设置正式数据库受限账号连接}"
: "${OWNER_USERNAME:?请设置老板账号}"
: "${OWNER_PASSWORD_HASH:?请设置老板密码哈希}"
: "${PRODUCTION_INSTANCE_ID:?请设置正式空库初始化返回的标识}"
export APP_MODE=production
python -m scripts.check_production
exec python -m streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-10000}" --server.headless true --server.enableXsrfProtection true --server.enableCORS true --client.showErrorDetails none
