"""交互式生成口令哈希。不要把原始口令写进代码、Git 或聊天记录。"""
from getpass import getpass
from core.access import password_hash

if __name__ == '__main__':
    password = getpass('设置测试口令（至少 16 个字符，不回显）：')
    if password != getpass('再次输入：'):
        raise SystemExit('两次输入不一致，未生成。')
    try:
        print(password_hash(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
