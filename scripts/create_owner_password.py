"""Generate only a hash locally; the plaintext password never leaves the prompt."""
from getpass import getpass
from core.access import password_hash

if __name__ == '__main__':
    password = getpass('设置老板专用密码（至少16个字符，不回显）：')
    if password != getpass('再次输入：'):
        raise SystemExit('两次输入不一致，未生成。')
    try:
        print('OWNER_PASSWORD_HASH=' + password_hash(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
