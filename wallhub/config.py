"""Хранилище настроек панели.

Настройки лежат в одном JSON-файле. Значения по умолчанию задаются здесь,
а в файле хранится только то, что пользователь поменял (плюс служебные поля).
Каждое сохранение увеличивает revision: по нему киоск понимает, что
настройки изменились из админки, и перечитывает их.
"""

import copy
import json
import os
import threading

DEFAULTS = {
    "ui": {
        # light | dark
        "theme": "dark",
        # Автопереключение темы по расписанию: днём светлая, ночью тёмная.
        "auto_theme": False,
        "day_start": "07:00",
        "night_start": "21:00",
        # Затемнение при неактивности. 0 минут = выключено.
        "dim_after_min": 5,
        # Насколько затемнять, 0..95 %.
        "dim_level": 80,
        "language": "ru",
    },
    # Порядок и включённость модулей главного экрана.
    "modules": [
        {"id": "clock", "enabled": True, "options": {"seconds": True, "date": True}},
    ],
    # Пункты статусной строки (идентификаторы провайдеров из wallhub.status).
    "statusbar": ["address", "wifi", "load", "memory", "temp", "traffic"],
    "admin": {
        # Хеш пароля админки (см. wallhub.auth). Пустой = админка закрыта.
        "password_hash": "",
        # Начальный пароль показывается на экране, пока его не сменят.
        "initial_password": "",
    },
}

# Поля, которые нельзя отдавать наружу через API.
SECRET_PATHS = (("admin", "password_hash"),)


def _merge(base, override):
    """Глубокое слияние: словари сливаются, остальное заменяется."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = {}
        self.revision = 0
        self.load()

    def load(self):
        data = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        with self._lock:
            self.revision = data.pop("_revision", 0)
            self._data = _merge(DEFAULTS, data)

    def _save_locked(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        payload = dict(self._data, _revision=self.revision)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, *path):
        with self._lock:
            node = self._data
            for key in path:
                node = node[key]
            return copy.deepcopy(node)

    def public(self):
        """Настройки без секретов, для отдачи в браузер."""
        with self._lock:
            data = copy.deepcopy(self._data)
        for path in SECRET_PATHS:
            node = data
            for key in path[:-1]:
                node = node.get(key, {})
            node.pop(path[-1], None)
        data["revision"] = self.revision
        return data

    def update(self, patch):
        """Применяет частичное обновление и сохраняет файл."""
        with self._lock:
            self._data = _merge(self._data, patch)
            self.revision += 1
            self._save_locked()
            return self.revision

    def set(self, path, value):
        with self._lock:
            node = self._data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = copy.deepcopy(value)
            self.revision += 1
            self._save_locked()
