"""Хранилище настроек панели.

Настройки лежат в одном JSON-файле. Значения по умолчанию задаются здесь,
а в файле хранится только то, что пользователь поменял (плюс служебные поля).
Каждое сохранение увеличивает revision: по нему киоск понимает, что
настройки изменились из админки, и перечитывает их.
"""

import copy
import json
import re
import os
import threading

DEFAULTS = {
    "ui": {
        # Тема при ручном режиме: light | dark.
        "theme": "dark",
        # Как выбирается тема: manual (вручную), schedule (по времени),
        # sun (по восходу и закату). В режимах schedule и sun днём светлая,
        # ночью тёмная. Тем же правилом определяется «ночь» для приглушения.
        "theme_mode": "manual",
        "day_start": "07:00",
        "night_start": "21:00",
        # Координаты для расчёта восхода и заката (по умолчанию Москва).
        "latitude": 55.75,
        "longitude": 37.62,
        # Постоянное приглушение ночью, 0..90 %. 0 = выключено.
        "night_dim": 40,
        # Затемнение при неактивности. 0 минут = выключено.
        "dim_after_min": 5,
        # Насколько затемнять, 0..95 %.
        "dim_level": 80,
        # Полное гашение экрана (DPMS) при неактивности. 0 минут = не гасить.
        "blank_after_min": 30,
        # Фон: id картинки из wallhub.background (встроенной default-… или
        # загруженной) или "" (без фона). У тёмной
        # темы может быть свой фон; "same" — тот же, что у светлой.
        "background": "default-aurora-light",
        "background_dark": "default-aurora-dark",
        # Затемнение фона, 0..80 %.
        "bg_dim": 0,
        # Плитки поверх фона: baked | blur | frosted | solid (см. web/lib/wallpaper.js).
        "tiles": "baked",
        "language": "ru",
    },
    # Сетка экрана и поведение столов. home_after_min: через сколько минут
    # без касаний вернуться на первый стол (0 — не возвращаться).
    "layout": {"cols": 16, "rows": 10, "home_after_min": 5},
    # Рабочие столы: виджеты в клетках сетки (см. wallhub.desktops).
    "desktops": [
        {
            "id": "d1",
            "name": "",
            "widgets": [
                {"id": "w1", "type": "system/statusbar", "x": 0, "y": 0, "w": 16, "h": 1,
                 "options": {"items": ["wifi", "load", "memory", "temp", "traffic"], "align": "left"}},
                {"id": "w2", "type": "clock/clock", "x": 0, "y": 1, "w": 16, "h": 9,
                 "options": {"seconds": True, "date": True}},
            ],
        },
    ],
    # Первый запуск: пока Wi‑Fi не выбран (или шаг не пропущен), киоск
    # открывает выбор сети.
    "setup": {"wifi_done": False},
    # Обновления. Канал — ключ из branches (ветка или "tags" — последний релиз).
    # schedule: off | hourly | 6h | daily (в time). Должно совпадать с
    # DEFAULT_SETTINGS в deploy/wallhub-update.py.
    "update": {
        "repo": "tesa4ello/wall-hub",
        "channel": "stable",
        "branches": {"stable": "main", "dev": "dev"},
        "auto": True,
        "schedule": "daily",
        "time": "04:00",
    },
    "admin": {
        # Хеш пароля админки (см. wallhub.auth). Пустой = пароль ещё не задан,
        # его придумывают при первом входе в админку.
        "password_hash": "",
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


def _migrate(data):
    """Приводит настройки старых версий к текущему виду."""
    ui = data.get("ui", {})
    # Раньше пароль генерировался и показывался на экране.
    data.get("admin", {}).pop("initial_password", None)
    if "auto_theme" in ui:
        ui.setdefault("theme_mode", "schedule" if ui.pop("auto_theme") else "manual")
    # До столов были список модулей и отдельная статусная строка.
    modules = data.pop("modules", None)
    statusbar = data.pop("statusbar", None)
    if (modules is not None or statusbar is not None) and "desktops" not in data:
        desk = copy.deepcopy(DEFAULTS["desktops"][0])
        bar, clock = desk["widgets"]
        if statusbar is not None:
            bar["options"]["items"] = list(statusbar)
        clock_cfg = next((m for m in modules or [] if m.get("id") == "clock"), None)
        if clock_cfg is not None:
            clock["options"].update(clock_cfg.get("options", {}))
            if not clock_cfg.get("enabled", True):
                desk["widgets"].remove(clock)
        data["desktops"] = [desk]
    # Раньше имя «Стол N» записывалось в настройки. Пустое имя интерфейс
    # показывает как «Стол N» на языке панели.
    for desk in data.get("desktops", []):
        if re.fullmatch(r"Стол \d+", desk.get("name", "")):
            desk["name"] = ""


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
            _migrate(data)
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
        data["admin"]["password_set"] = bool(self._data["admin"]["password_hash"])
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
