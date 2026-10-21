"""Связь админки с обновлятором (deploy/wallhub-update.py).

Сам обновлятор работает от root в отдельном systemd-юните. Сервис только
запускает его через polkit, хранит токен GitHub и читает update.json.
"""

import json
import logging
import os
import subprocess
import threading
import time

log = logging.getLogger(__name__)

MODES = ("check", "install", "rollback")
CHANNELS = ("stable", "dev")
SCHEDULES = ("off", "hourly", "6h", "daily")


def normalize_settings(settings):
    """Настройки обновлений с учётом старых значений канала (main/releases)."""
    settings = dict(settings)
    if settings.get("channel") not in CHANNELS:
        settings["channel"] = "stable"
    return settings


class Updates:
    def __init__(self, state_dir, dry_run=False):
        self.status_path = os.path.join(state_dir, "update.json")
        self.token_path = os.path.join(state_dir, "github-token")
        self.dry_run = dry_run

    def status(self):
        try:
            with open(self.status_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"state": "idle", "message": "Обновления ещё не проверялись"}

    def available(self, build_commit):
        """Есть ли версия новее установленной (по результату последней проверки)."""
        status = self.status()
        latest = (status.get("latest") or {}).get("commit")
        return bool(status.get("available") and latest and latest != build_commit)

    def token_set(self):
        try:
            return os.path.getsize(self.token_path) > 0
        except OSError:
            return False

    def set_token(self, token):
        if not token:
            try:
                os.remove(self.token_path)
            except FileNotFoundError:
                pass
            return
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)

    def start(self, mode):
        if mode not in MODES:
            raise ValueError(mode)
        if self.dry_run:
            threading.Thread(target=self._fake_run, args=(mode,), daemon=True).start()
            return
        # --no-block: юнит работает долго, а HTTP-ответ нужен сразу.
        subprocess.run(
            ["systemctl", "start", "--no-block", f"wallhub-update@{mode}.service"],
            check=True, capture_output=True, timeout=15,
        )

    def _write(self, **fields):
        status = self.status()
        status.update(fields)
        with open(self.status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)

    def _fake_run(self, mode):
        """Имитация обновлятора для --dry-run."""
        self._write(state="checking", message="Проверяю обновления…")
        time.sleep(1.5)
        latest = {"commit": "0123456789abcdef", "ref": "main", "date": "2026-10-21T10:00:00Z",
                  "title": "Тестовое обновление", "version": "0.1.2"}
        if mode == "install":
            self._write(state="installing", message="Устанавливаю…")
            time.sleep(2)
            self._write(state="idle", result="ok", message="Обновлено", available=False, latest=latest)
        else:
            self._write(state="idle", message="Есть новая версия", available=True, latest=latest,
                        checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
