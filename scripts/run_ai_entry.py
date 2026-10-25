"""Launch the local Qwen prototype with the locally configured test key."""
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    key_file = root / '.local' / 'ai-entry' / 'qwen-key'
    if not key_file.is_file() or not key_file.read_text().strip():
        print('请先运行 .venv/bin/python scripts/configure_qwen_key.py 配置测试密钥。')
        return 1
    env = os.environ.copy()
    env.update(AI_ENTRY_PROVIDER='qwen', DASHSCOPE_API_KEY=key_file.read_text().strip(),
               AI_ENTRY_MODEL='qwen3.7-flash',
               AI_ENTRY_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1',
               AI_ENTRY_INPUT_PRICE='0.20', AI_ENTRY_OUTPUT_PRICE='0.80', AI_ENTRY_USD_CNY='1')
    os.chdir(root)
    os.execve(sys.executable, [sys.executable, '-m', 'streamlit', 'run', 'ai_entry_app.py',
                              '--server.address', '127.0.0.1', '--server.port', '8503',
                              '--server.headless', 'true'], env)


if __name__ == '__main__':
    raise SystemExit(main())
