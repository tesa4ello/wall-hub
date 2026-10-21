"""Автообновление модулей из магазинов — по аналогии с обновлением системы.

По расписанию (config["module_update"]: schedule hourly/6h/daily/off, time
для daily, auto) ядро перечитывает каталоги и, если включена автоустановка,
ставит новые версии установленных модулей. После установки проверяем, что
серверная часть модуля запустилась; если нет — возвращаем прошлую версию
(store.rollback). Без автоустановки только запоминаем, что есть обновления:
админка и экран это показывают.

Ход и итог — в state/module-update.json.
"""

import datetime
import json
import logging
import os
import threading
import time

from .store import StoreError

log = logging.getLogger(__name__)

SCHEDULES = ("off", "hourly", "6h", "daily")
INTERVALS = {"hourly": datetime.timedelta(hours=1), "6h": datetime.timedelta(hours=6)}
SLACK = datetime.timedelta(minutes=1)
TICK = 60


def due(settings, last, now):
    """Пора ли автопроверке (как due() в deploy/wallhub-update.py)."""
    schedule = settings.get("schedule", "daily")
    if schedule in INTERVALS:
        return last is None or now - last >= INTERVALS[schedule] - SLACK
    if schedule == "daily":
        hour, minute = map(int, settings.get("time", "04:30").split(":"))
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot:
            slot -= datetime.timedelta(days=1)
        return last is None or last < slot
    return False


def normalize(settings):
    s = dict(settings or {})
    if s.get("schedule") not in SCHEDULES:
        s["schedule"] = "daily"
    try:
        datetime.time.fromisoformat(str(s.get("time")))
    except ValueError:
        s["time"] = "04:30"
    s["auto"] = bool(s.get("auto", True))
    return s


def _now():
    return datetime.datetime.now().astimezone()


class ModuleUpdater:
    def __init__(self, store, config, state_dir, backend_error=lambda mid: None, notify=lambda: None):
        self.store = store
        self.config = config
        self.path = os.path.join(state_dir, "module-update.json")
        self.backend_error = backend_error
        self.notify = notify
        self.lock = threading.Lock()
        self.running = False
        self.stop_event = threading.Event()

    # --- статус -------------------------------------------------------------------

    def status(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data.setdefault("available", [])
        data["state"] = "running" if self.running else "idle"
        return data

    def _save(self, data):
        data = {k: v for k, v in data.items() if k != "state"}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def available_count(self):
        return len(self.status()["available"])

    def settings(self):
        return normalize(self.config.get("module_update"))

    # --- запуск -------------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="module-update").start()

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        while not self.stop_event.wait(TICK):
            try:
                settings = self.settings()
                last = self.status().get("last_auto")
                last = datetime.datetime.fromisoformat(last) if last else None
                if self.store.installed() and due(settings, last, _now()):
                    self.run(install=settings["auto"], auto=True)
            except Exception:  # noqa: BLE001 — фоновый поток не должен умирать
                log.exception("автообновление модулей")

    def run_async(self, install):
        """Запуск из админки: проверить (install=False) или обновить всё."""
        if self.running:
            return False
        threading.Thread(target=self.run, kwargs={"install": install}, daemon=True, name="module-update-run").start()
        return True

    def run(self, install, auto=False, only=None):
        with self.lock:
            self.running = True
            data = self.status()
            self.notify()
            try:
                self._run(data, install, only)
            finally:
                data["checked_at"] = _now().isoformat(timespec="seconds")
                if auto:
                    data["last_auto"] = data["checked_at"]
                self.running = False
                self._save(data)
                self.notify()
        return data

    def _run(self, data, install, only):
        self.store.refresh()
        rows = self.store.listing()["modules"]
        errors = [c for c in self.store.listing()["catalogs"] if c["enabled"] and c["error"]]
        pending = [m for m in rows if m["update"] and m["compatible"] and (only is None or m["id"] in only)]
        data["updated"], data["failed"] = [], []
        if install:
            for m in pending:
                self._update_one(m, data)
            rows = self.store.listing()["modules"]
        data["available"] = [{"id": m["id"], "title": m["title"], "installed": m["installed"], "version": m["version"]}
                             for m in rows if m["update"] and m["compatible"]]
        if errors:
            data["message"], data["message_args"] = "Не удалось прочитать каталог {repo}", {"repo": errors[0]["repo"]}
        elif data["failed"]:
            data["message"], data["message_args"] = "Не все модули обновились", {}
        elif data["updated"]:
            data["message"], data["message_args"] = "Обновлено модулей: {n}", {"n": len(data["updated"])}
        elif data["available"]:
            data["message"], data["message_args"] = "Доступны обновления модулей: {n}", {"n": len(data["available"])}
        else:
            data["message"], data["message_args"] = "Все модули в актуальном состоянии", {}

    def _update_one(self, m, data):
        mid = m["id"]
        old = self.store.installed().get(mid)
        try:
            self.store.install(mid)
        except StoreError as e:
            data["failed"].append({"id": mid, "title": m["title"], "error": e.message, "error_args": e.params})
            log.warning("модуль %s не обновился: %s", mid, e.message.format(**e.params))
            return
        # Серверная часть модуля стартует при установке (modules_changed).
        error = self.backend_error(mid)
        if error and old:
            self.store.rollback(mid, old)
            data["failed"].append({"id": mid, "title": m["title"], "rolled_back": True,
                                   "error": "серверная часть не запустилась: {error}", "error_args": {"error": error}})
            log.warning("модуль %s %s: серверная часть не запустилась (%s), вернул %s", mid, m["version"], error, old.get("version"))
            return
        data["updated"].append({"id": mid, "title": m["title"], "from": old.get("version") if old else None, "to": m["version"]})


def wait_idle(updater, timeout=10):
    """Для тестов: дождаться конца фонового запуска."""
    deadline = time.monotonic() + timeout
    while updater.running and time.monotonic() < deadline:
        time.sleep(0.05)
