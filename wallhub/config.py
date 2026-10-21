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
        # Местоположение панели: для темы «по солнцу» и виджетов, у которых
        # выбрано «как у панели». location_mode: manual | ip | yandex
        # (см. wallhub.location). По умолчанию Москва, пока не определено.
        "latitude": 55.75,
        "longitude": 37.62,
        "location_mode": "ip",
        "location_name": "",
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
        # Прозрачность стеклянных плиток, 0..90 % (0 — почти сплошные).
        "tile_transparency": 58,
        # Плитки поверх фона: baked | blur | frosted | solid (см. web/lib/wallpaper.js).
        "tiles": "baked",
        "language": "ru",
        # Звуковые уведомления (играет киоск, см. web/lib/sound.js).
        # sound_volume 0..100; в тихие часы (quiet_from..quiet_to, могут
        # переходить через полночь) панель молчит.
        "sound_enabled": True,
        "sound_volume": 60,
        "sound_name": "chime",
        # Масштаб интерфейса: "auto" (по разрешению экрана) или число из display.SCALES.
        "scale": "auto",
        "sound_quiet": True,
        "sound_quiet_from": "22:00",
        "sound_quiet_to": "08:00",
        # Какие события звучат: появилась новая версия, обновление закончилось.
        "sound_update": True,
    },
    # Общие настройки серверных частей модулей: {id модуля: {...}}
    # (см. wallhub.backends). Секреты модули хранят в своём data_dir.
    "module_settings": {},
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
    # Магазин модулей: каталоги — git-репозитории на GitHub (см. store.py).
    # Основной каталог всегда первый, его можно только выключить.
    "store": {
        "main": {"repo": "tesa4ello/wallhub-modules", "branch": "main", "enabled": True},
        "catalogs": [],
    },
    "update": {
        "repo": "tesa4ello/wall-hub",
        "channel": "stable",
        "branches": {"stable": "stable", "dev": "dev"},
        "auto": True,
        "schedule": "daily",
        "time": "04:00",
    },
    # Автообновление модулей из магазинов (wallhub/modupdate.py).
    "module_update": {"auto": True, "schedule": "daily", "time": "04:30"},
    # Раздел «Погода» в админке: провайдеры, ключи (секрет), интервал,
    # единицы по умолчанию. Пусто = значения из wallhub.weather.DEFAULT_SETTINGS.
    "weather": {},
    # Ключ Яндекс Локатора (секрет, наружу отдаётся только признак).
    "location": {"yandex_key": ""},
    "admin": {
        # Хеш пароля админки (см. wallhub.auth). Пустой = пароль ещё не задан,
        # его придумывают при первом входе в админку.
        "password_hash": "",
    },
}

# Поля, которые нельзя отдавать наружу через API.
SECRET_PATHS = (("admin", "password_hash"), ("location", "yandex_key"), ("weather", "keys"))


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
    # До 1.0.0 стабильной веткой была main, теперь stable.
    branches = data.get("update", {}).get("branches", {})
    if branches.get("stable") == "main":
        branches["stable"] = "stable"
    # Раньше пароль генерировался и показывался на экране.
    data.get("admin", {}).pop("initial_password", None)
    # Кто уже вписал координаты вручную, у того они и остаются.
    if "location_mode" not in ui and ("latitude" in ui or "longitude" in ui):
        ui["location_mode"] = "manual"
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
    # Встроенные фоны первых версий убраны, вместо них — сияние той же темы.
    for key in ("background", "background_dark"):
        m = re.fullmatch(r"default-(?:waves|orbs)-(light|dark)", str(ui.get(key, "")))
        if m:
            ui[key] = f"default-aurora-{m.group(1)}"
    # Раньше имя «Стол N» записывалось в настройки. Пустое имя интерфейс
    # показывает как «Стол N» на языке панели.
    for desk in data.get("desktops", []):
        if re.fullmatch(r"Стол \d+", desk.get("name", "")):
            desk["name"] = ""


class Config:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        # Будит киоск, который ждёт изменений (см. wait).
        self._changed = threading.Condition(self._lock)
        self._data = {}
        self.revision = 0
        self.load()

    def wait(self, predicate, timeout):
        """Ждёт до timeout секунд, пока predicate() не станет истинным.
        Проверяется после каждого сохранения и после notify()."""
        with self._changed:
            return self._changed.wait_for(predicate, timeout)

    def notify(self):
        with self._changed:
            self._changed.notify_all()

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
        data["location"]["yandex_key_set"] = bool(self._data["location"]["yandex_key"])
        data["revision"] = self.revision
        return data

    def update(self, patch):
        """Применяет частичное обновление и сохраняет файл."""
        with self._lock:
            self._data = _merge(self._data, patch)
            self.revision += 1
            self._save_locked()
            self._changed.notify_all()
            return self.revision

    def set(self, path, value):
        with self._lock:
            node = self._data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = copy.deepcopy(value)
            self.revision += 1
            self._save_locked()
            self._changed.notify_all()
