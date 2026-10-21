"""Пароль и сессии сетевой админки."""

import hashlib
import hmac
import json
import os
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


class Sessions:
    """Сессии админки. С path они переживают перезапуск сервиса (обновление
    не выбивает из админки); на диске лежат только хэши токенов."""

    def __init__(self, path=None):
        self._lock = threading.Lock()
        self._path = path
        self._sessions = self._load()

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def _load(self):
        if not self._path:
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            return {k: float(v) for k, v in data.items() if float(v) > now}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _save(self):
        if not self._path:
            return
        tmp = self._path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def create(self):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v > now}
            self._sessions[self._key(token)] = now + SESSION_TTL
            self._save()
        return token

    def valid(self, token):
        if not token:
            return False
        key = self._key(token)
        with self._lock:
            expires = self._sessions.get(key)
            if expires is None:
                return False
            if expires < time.time():
                del self._sessions[key]
                self._save()
                return False
            return True

    def drop(self, token):
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._key(token), None)
            self._save()

    def clear(self):
        with self._lock:
            self._sessions.clear()
            self._save()
