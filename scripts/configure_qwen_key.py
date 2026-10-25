"""Collect a local test key without echoing it or putting it in shell history."""
import getpass
import os
from pathlib import Path
import sys
import tempfile


def main():
    if not sys.stdin.isatty():
        print('请在你自己的终端运行此脚本，以隐藏方式输入密钥。')
        return 1
    target = Path(__file__).resolve().parents[1] / '.local' / 'ai-entry' / 'qwen-key'
    print('密钥将保存到本机 .local/ai-entry/qwen-key，仅当前系统用户可读写。')
    print('该目录已排除在 Git 之外。输入时屏幕不显示字符。')
    try:
        key = getpass.getpass('请粘贴北京地域百炼 API Key，然后回车：').strip()
    except (EOFError, KeyboardInterrupt):
        print('\n已取消，未保存。')
        return 1
    if not key.startswith('sk-') or any(c.isspace() for c in key):
        print('密钥格式不符合预期，未保存。请复制完整的 API Key。')
        return 1
    if key.startswith('sk-sp-'):
        print('这是套餐专用密钥，请使用百炼普通按量付费 API Key。未保存。')
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.qwen-key-')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(key)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('密钥已保存（未显示）。尚未调用 API，也尚未验证密钥有效性。')
    print('现在可以回到聊天，告诉我“已配置”。不要发送密钥内容。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
