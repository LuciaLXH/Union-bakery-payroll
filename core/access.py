"""Password hashing/session primitives; in-memory limiter is for the test entrance only."""
import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 600_000
SESSION_SECONDS = 4 * 60 * 60


def password_hash(password):
    if not 16 <= len(password) <= 1024:
        raise ValueError('密码需为 16–1024 个字符。')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def parse_hash(encoded):
    try:
        algorithm, iterations, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or iterations != str(ITERATIONS) or len(salt)!=32 or len(digest)!=64:
            raise ValueError
        return bytes.fromhex(salt), bytes.fromhex(digest)
    except (ValueError, AttributeError):
        raise ValueError('访问密码配置无效。') from None


def verify_password(password, encoded):
    salt, expected = parse_hash(encoded)
    if not 16 <= len(password) <= 1024:
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS)
    return hmac.compare_digest(actual, expected)


def session_valid(session, encoded, now=None):
    now = time.time() if now is None else now
    return bool(session and session.get('hash') == hashlib.sha256(encoded.encode()).hexdigest()
                and 0 <= now-session.get('time',0) < SESSION_SECONDS)


class AttemptLimiter:
    """单个测试服务内共享限速；重启会重置，正式环境应使用独立身份服务。"""
    def __init__(self):
        self.lock = threading.Lock()
        self.failures = 0
        self.until = 0

    def check(self, password, encoded, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if now < self.until:
                return 'wait'
            if self.until:
                self.failures, self.until = 0, 0
            if verify_password(password, encoded):
                self.failures = 0
                return 'ok'
            self.failures += 1
            if self.failures >= 5:
                self.until = now + 60
            return 'invalid'
