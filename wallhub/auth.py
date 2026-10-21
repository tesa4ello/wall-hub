"""Пароль и сессии сетевой админки."""

import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 200_000
SESSION_TTL = 12 * 3600
# Пауза после неудачной попытки входа, защищает от перебора.
FAIL_DELAY = 1.0


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def check_password(password, stored):
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def generate_password():
    # Без похожих символов, чтобы пароль было легко перепечатать с экрана.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


class Sessions:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions = {}

    def create(self):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.time() + SESSION_TTL
        return token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if expires < time.time():
                del self._sessions[token]
                return False
            return True

    def drop(self, token):
        with self._lock:
            self._sessions.pop(token, None)

    def clear(self):
        with self._lock:
            self._sessions.clear()
