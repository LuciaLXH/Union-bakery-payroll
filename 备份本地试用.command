#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo '请先由维护者安装本机运行环境。'
else
  .venv/bin/python -m scripts.local_demo backup
fi
printf '\n按回车关闭此窗口。'
read -r answer
