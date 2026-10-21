"""Серверные части модулей.

Модуль с полем "backend": "backend/main.py" в module.json получает код,
который ядро импортирует в своём процессе и запускает функцией setup(ctx).
Через ctx модуль регистрирует маршруты /api/m/<id>/<путь>, хранит свои
файлы в data_dir и общие настройки в конфиге. Если setup вернул функцию или
объект с stop(), ядро вызывает её, когда модуль удаляют, обновляют или
останавливают сервис. Свои фоновые потоки модуль заводит сам (daemon).

Код модулей доверенный (подключая каталог, пользователь доверяет ему), так
что изоляции нет: ошибки только перехватываются, чтобы не уронить ядро.
"""

import importlib
import logging
import os
import re
import sys
import threading
import types

from .i18n import tr

log = logging.getLogger(__name__)

PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
BACKEND_RE = re.compile(r"^backend/[A-Za-z0-9_]+\.py$")


class ModuleError(Exception):
    """Ошибка для пользователя: текст переводится переводами модуля."""

    def __init__(self, message, **params):
        super().__init__(message)
        self.message = message
        self.params = params

    def text(self):
        return tr(self.message, **self.params)


class Context:
    Error = ModuleError

    def __init__(self, host, module_id, data_dir):
        self.id = module_id
        self.data_dir = data_dir
        self.log = logging.getLogger(f"wallhub.module.{module_id}")
        self._host = host

    def route(self, method, path, handler, admin=False):
        """handler(body, query) -> JSON. admin=True: только вошедшим в админку,
        иначе ещё и экрану панели."""
        method = method.upper()
        if method not in ("GET", "POST") or not PATH_RE.match(path):
            raise ValueError(f"bad route {method} {path}")
        self._host.routes[(self.id, method, path)] = (handler, bool(admin))

    def settings(self):
        return dict(self._host.config.get("module_settings").get(self.id) or {})  # копия

    def save_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError("settings must be a dict")
        self._host.config.set(("module_settings", self.id), settings)

    def notify(self, title, text="", level="info", key=None, action=None, icon=None, sound=True, **params):
        """Уведомление на экране и в шторке. title, text — русские строки
        (ключи i18n модуля) с {подстановками} из params. level: info, ok, warn,
        crit. key: новое уведомление с тем же ключом заменяет прежнее.
        action: {"kind": "module", "label": "Открыть"} и т. п."""
        if self._host.notifications is None:
            return None
        return self._host.notifications.add(self.id, title, text, level=level, icon=icon, action=action,
                                            key=key, params=params, sound=sound)

    def clear_notification(self, key):
        """Снять своё уведомление с ключом key (например, проблема ушла)."""
        if self._host.notifications is not None:
            self._host.notifications.remove_key(self.id, key)

    def tile(self, tile_id, title, text="", level="info", icon=None, **params):
        """Плитка модуля в шторке: короткое состояние. Повторный вызов с тем
        же tile_id обновляет её."""
        if self._host.tiles is not None:
            self._host.tiles.set(self.id, tile_id, title, text, level=level, icon=icon, params=params)

    def remove_tile(self, tile_id=None):
        if self._host.tiles is not None:
            self._host.tiles.remove(self.id, tile_id)


def backend_path(module):
    """Файл серверной части модуля или None (поля нет или оно неверное)."""
    rel = module.meta.get("backend")
    if not isinstance(rel, str) or not BACKEND_RE.match(rel):
        return None
    full = os.path.join(module.path, rel)
    return full if os.path.isfile(full) else None


class Backends:
    def __init__(self, config, data_root, notifications=None, tiles=None):
        self.config = config
        self.data_root = data_root
        self.notifications = notifications
        self.tiles = tiles
        self.routes = {}  # (id, метод, путь) -> (handler, admin)
        self.running = {}  # id -> {"path", "stop"}
        self.errors = {}  # id -> текст ошибки запуска
        self.lock = threading.Lock()
        self.counter = 0

    def sync(self, modules):
        """Запускает серверные части модулей {id: Module}, перезапускает
        обновлённые и останавливает те, которых больше нет."""
        with self.lock:
            wanted = {mid: path for mid, m in modules.items() if (path := backend_path(m))}
            for mid in list(self.running):
                if wanted.get(mid) != self.running[mid]["path"]:
                    self._stop(mid)
            for mid in list(self.errors):
                if mid not in wanted:
                    del self.errors[mid]
            for mid, path in wanted.items():
                if mid not in self.running:
                    self._start(mid, path)

    def stop_all(self):
        with self.lock:
            for mid in list(self.running):
                self._stop(mid)

    def _start(self, mid, path):
        data_dir = os.path.join(self.data_root, mid)
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        # Пакет из папки backend: модуль может делить код на файлы и
        # импортировать их относительно (from . import ssh).
        self.counter += 1
        pkg_name = f"wallhub_backend_{mid.replace('-', '_')}_{self.counter}"
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.dirname(path)]
        sys.modules[pkg_name] = pkg
        ctx = Context(self, mid, data_dir)
        try:
            code = importlib.import_module(f"{pkg_name}.{os.path.splitext(os.path.basename(path))[0]}")
            stop = code.setup(ctx)
        except Exception as e:
            log.exception("серверная часть модуля %s не запустилась", mid)
            self._drop_routes(mid)
            self._unload(pkg_name)
            self.errors[mid] = f"{type(e).__name__}: {e}"
            return
        self.errors.pop(mid, None)
        self.running[mid] = {"path": path, "stop": stop, "package": pkg_name}
        log.info("серверная часть модуля %s запущена", mid)

    def _stop(self, mid):
        entry = self.running.pop(mid)
        stop = entry["stop"]
        stop = getattr(stop, "stop", stop)
        if callable(stop):
            try:
                stop()
            except Exception:
                log.exception("модуль %s не остановился чисто", mid)
        self._drop_routes(mid)
        self._unload(entry["package"])
        # Плитки показывают живое состояние: без серверной части их нет.
        if self.tiles is not None:
            self.tiles.remove(mid)
        log.info("серверная часть модуля %s остановлена", mid)

    def _drop_routes(self, mid):
        for key in [k for k in self.routes if k[0] == mid]:
            del self.routes[key]

    @staticmethod
    def _unload(pkg_name):
        for name in [n for n in sys.modules if n == pkg_name or n.startswith(pkg_name + ".")]:
            del sys.modules[name]

    def find(self, mid, method, path):
        """(handler, admin) или None."""
        return self.routes.get((mid, method, path))
