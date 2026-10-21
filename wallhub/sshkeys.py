"""Хранилище SSH-ключей панели.

Панель сама ходит по SSH (например, модуль мониторинга серверов), поэтому
ключи живут в ядре, а не в модулях: /var/lib/wallhub/ssh/ (0700, только у
сервиса). Приватные ключи браузеру не отдаются никогда, наружу идут только
публичные. При первом запуске создаётся ed25519 «Ключ панели» (id panel),
его публичную часть добавляют в authorized_keys на серверах.

Модули получают список ключей, путь к приватному ключу и общий known_hosts
через ctx (см. backends.py).
"""

import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
DEFAULT_ID = "panel"
DEFAULT_TITLE = "Ключ панели"
MAX_TITLE = 60


class SshKeyError(ValueError):
    """Ошибка для пользователя (текст по-русски, переводится в server)."""

    def __init__(self, message, **params):
        super().__init__(message)
        self.message = message
        self.params = params


class SshKeys:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.index_path = os.path.join(root, "keys.json")
        self.known_hosts = os.path.join(root, "known_hosts")

    def _ensure_dir(self):
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _index(self):
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self, index):
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".keys-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.index_path)

    def private_path(self, key_id):
        return os.path.join(self.root, f"id_{key_id}")

    @staticmethod
    def _keygen(*args, input_text=None):
        return subprocess.run(["ssh-keygen", *args], input=input_text, capture_output=True, text=True, timeout=30)

    def _public(self, key_id):
        try:
            with open(self.private_path(key_id) + ".pub", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def ensure_default(self):
        """Создаёт «Ключ панели» при самом первом запуске."""
        # Метка, чтобы удалённый пользователем ключ не появлялся снова.
        marker = os.path.join(self.root, ".initialized")
        with self.lock:
            self._ensure_dir()
            if os.path.exists(marker) or self._index():
                return
            try:
                self._generate(DEFAULT_ID, DEFAULT_TITLE)
                open(marker, "w").close()
            except (OSError, SshKeyError, subprocess.SubprocessError) as e:
                log.warning("ключ панели не создан: %s", e)

    def list(self):
        with self.lock:
            index = self._index()
        result = []
        for key_id, meta in index.items():
            public = self._public(key_id)
            result.append({
                "id": key_id,
                "title": meta.get("title") or key_id,
                "type": public.split(" ", 1)[0] if public else "",
                "public_key": public,
                "created_at": meta.get("created_at"),
            })
        return result

    def path(self, key_id):
        """Путь к приватному ключу или None."""
        if not isinstance(key_id, str) or not ID_RE.match(key_id):
            return None
        with self.lock:
            if key_id not in self._index():
                return None
        full = self.private_path(key_id)
        return full if os.path.isfile(full) else None

    def _new_id(self, index):
        while True:
            key_id = secrets.token_hex(4)
            if key_id not in index:
                return key_id

    @staticmethod
    def _title(title):
        title = str(title or "").strip()[:MAX_TITLE]
        if not title:
            raise SshKeyError("Укажите название ключа")
        return title

    def _generate(self, key_id, title):
        path = self.private_path(key_id)
        for p in (path, path + ".pub"):
            if os.path.exists(p):
                os.remove(p)
        comment = f"wallhub-{key_id}"
        result = self._keygen("-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", path)
        if result.returncode != 0:
            raise SshKeyError("ssh-keygen не смог создать ключ: {error}", error=result.stderr.strip())
        os.chmod(path, 0o600)
        index = self._index()
        index[key_id] = {"title": title, "created_at": _now()}
        self._save_index(index)
        return key_id

    def generate(self, title):
        title = self._title(title)
        with self.lock:
            self._ensure_dir()
            return self._generate(self._new_id(self._index()), title)

    def import_key(self, title, private):
        """Импорт приватного ключа (OpenSSH или PEM, без пароля)."""
        title = self._title(title)
        private = str(private or "").replace("\r\n", "\n").strip() + "\n"
        if "PRIVATE KEY" not in private or len(private) > 20000:
            raise SshKeyError("Это не похоже на приватный SSH-ключ")
        with self.lock:
            self._ensure_dir()
            index = self._index()
            key_id = self._new_id(index)
            path = self.private_path(key_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(private)
            # ssh-keygen -y выводит публичный ключ и заодно проверяет приватный.
            # Пустой пароль (-P ""): ключ с паролем панель всё равно не сможет использовать.
            result = self._keygen("-y", "-P", "", "-f", path)
            if result.returncode != 0:
                os.remove(path)
                raise SshKeyError("Ключ не подошёл (возможно, он защищён паролем): {error}", error=result.stderr.strip())
            with open(path + ".pub", "w", encoding="utf-8") as f:
                f.write(result.stdout.strip() + f" wallhub-{key_id}\n")
            index[key_id] = {"title": title, "created_at": _now()}
            self._save_index(index)
            return key_id

    def rename(self, key_id, title):
        title = self._title(title)
        with self.lock:
            index = self._index()
            if key_id not in index:
                raise SshKeyError("Ключ не найден")
            index[key_id]["title"] = title
            self._save_index(index)

    def delete(self, key_id):
        with self.lock:
            index = self._index()
            if key_id not in index:
                raise SshKeyError("Ключ не найден")
            del index[key_id]
            for p in (self.private_path(key_id), self.private_path(key_id) + ".pub"):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
            self._save_index(index)


def _now():
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
