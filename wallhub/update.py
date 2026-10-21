"""Связь админки с обновлятором (deploy/wallhub-update.py).

Сам обновлятор работает от root в отдельном systemd-юните. Сервис только
запускает его через polkit, хранит токен GitHub и читает update.json.
"""

import datetime
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

    def progress(self):
        """Ход обновления для экрана панели: идущее или только что закончившееся.

        Готовое обновление показывается минуту, ошибка — десять минут. Итог
        простой проверки (job="check") на экран не выводится.
        """
        status = self.status()
        keys = ("state", "phase", "message", "message_args", "target", "started_at", "result", "finished_at")
        if status.get("state") == "installing":
            return {k: status.get(k) for k in keys}
        try:
            finished = datetime.datetime.fromisoformat(status["finished_at"])
        except (KeyError, TypeError, ValueError):
            return None
        age = (datetime.datetime.now(finished.tzinfo) - finished).total_seconds()
        if status.get("job") in ("install", "rollback") and status.get("result") in ("ok", "failed") and 0 <= age < (60 if status["result"] == "ok" else 600):
            return {k: status.get(k) for k in keys}
        return None

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
        if "message" in fields:
            fields.setdefault("message_args", {})
        status.update(fields)
        with open(self.status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False)

    def _fake_run(self, mode):
        """Имитация обновлятора для --dry-run."""
        self._write(state="checking", job="check", message="Проверяю обновления…")
        time.sleep(1.5)
        now = lambda: datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        latest = {"commit": "0123456789abcdef", "ref": "main", "date": "2026-10-21T10:00:00Z",
                  "title": "Тестовое обновление", "version": "0.1.2"}
        if mode == "install":
            self._write(state="installing", job="install", phase="download", message="Скачиваю {ref} {commit}…",
                        message_args={"ref": "main", "commit": "0123456"}, target=latest["version"],
                        result=None, started_at=now())
            for phase, message in (("install", "Устанавливаю…"), ("verify", "Проверяю, что сервис запустился…")):
                time.sleep(3)
                self._write(phase=phase, message=message)
            time.sleep(3)
            self._write(state="idle", phase=None, result="ok", message="Обновлено", available=False,
                        latest=latest, finished_at=now())
        else:
            self._write(state="idle", message="Есть новая версия", available=True, latest=latest,
                        checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
