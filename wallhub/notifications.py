"""Уведомления панели: от ядра (обновления) и от модулей.

Модуль шлёт уведомление из серверной части (ctx.notify) или из виджета
(notify() в mount, это POST /api/notifications). Экран показывает новое
всплывашкой и звуком источника (ui.sound_sources), а список — в шторке, где
уведомление смахивают. Текст хранится исходным (русским) с подстановками и
переводится при выдаче, чтобы смена языка меняла и старые уведомления.

Список хранится в state/notifications.json, не больше LIMIT штук. key
позволяет заменить прежнее уведомление того же источника (например,
«сервер недоступен» не плодит копии, а обновляется).
"""

import datetime
import itertools
import json
import os
import threading

LEVELS = ("info", "ok", "warn", "crit")
ACTIONS = ("update", "open", "module")
LIMIT = 50
MAX_TEXT = 300


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _clean_params(params):
    return {str(k)[:40]: v if isinstance(v, (int, float)) else str(v)[:200] for k, v in (params or {}).items()}


class Notifications:
    def __init__(self, path, notify=lambda: None):
        self.path = path
        self.notify = notify
        self.lock = threading.Lock()
        self.items = self._load()
        start = max([i["id"] for i in self.items] + [0])
        self.ids = itertools.count(start + 1)
        # Номер последнего добавленного: киоск по нему узнаёт о новом.
        self.seq = start
        # Растёт при любом изменении списка: шторка перечитывает его.
        self.rev = 0

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                items = json.load(f)
            return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), int)][-LIMIT:]
        except (OSError, ValueError):
            return []

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, source, title, text="", level="info", icon=None, action=None, key=None, params=None, sound=True):
        """Добавляет уведомление и возвращает его. title и text — исходные строки
        (переводятся при выдаче), params — подстановки для них."""
        title = str(title or "").strip()[:MAX_TEXT]
        if not title:
            raise ValueError("title is required")
        if level not in LEVELS:
            level = "info"
        if action is not None:
            if not isinstance(action, dict) or action.get("kind") not in ACTIONS or not action.get("label"):
                raise ValueError("bad action")
            action = {k: str(v)[:200] for k, v in action.items() if k in ("kind", "label", "target")}
        with self.lock:
            item = {
                "id": next(self.ids), "source": str(source), "key": str(key)[:80] if key else None,
                "level": level, "icon": str(icon)[:40] if icon else None,
                "title": title, "text": str(text or "").strip()[:MAX_TEXT], "params": _clean_params(params),
                "action": action, "time": _now(), "sound": bool(sound),
            }
            if item["key"]:
                self.items = [i for i in self.items if not (i["source"] == item["source"] and i.get("key") == item["key"])]
            self.items = (self.items + [item])[-LIMIT:]
            self.seq = item["id"]
            self._save()
        self.rev += 1
        self.notify()
        return item

    def remove_key(self, source, key):
        """Снимает уведомление по ключу (например, сервер снова доступен)."""
        with self.lock:
            before = len(self.items)
            self.items = [i for i in self.items if not (i["source"] == source and i.get("key") == key)]
            if len(self.items) != before:
                self._save()
        self.rev += 1
        self.notify()

    def dismiss(self, item_id=None):
        """Смахнуть одно (item_id) или все (None)."""
        with self.lock:
            self.items = [i for i in self.items if item_id is not None and i["id"] != item_id]
            self._save()
        self.rev += 1
        self.notify()

    def get(self, item_id):
        with self.lock:
            return next((dict(i) for i in self.items if i["id"] == item_id), None)

    def listing(self, translate):
        """Новые сверху, с переведённым текстом. translate(text, params) -> str."""
        with self.lock:
            items = list(self.items)
        result = []
        for i in reversed(items):
            out = {k: i.get(k) for k in ("id", "source", "level", "icon", "time", "sound")}
            out["title"] = translate(i["title"], i.get("params") or {})
            out["text"] = translate(i["text"], i.get("params") or {}) if i["text"] else ""
            action = i.get("action")
            out["action"] = dict(action, label=translate(action["label"], {})) if action else None
            result.append(out)
        return result


class Tiles:
    """Плитки модулей в шторке: короткое состояние («1 недоступен»)."""

    def __init__(self, notify=lambda: None):
        self.notify = notify
        self.lock = threading.Lock()
        self.items = {}  # (модуль, id) -> плитка
        self.rev = 0

    def set(self, module, tile_id, title, text="", level="info", icon=None, params=None):
        if level not in LEVELS:
            level = "info"
        with self.lock:
            self.items[(module, str(tile_id))] = {
                "module": module, "id": str(tile_id)[:40], "title": str(title)[:80], "text": str(text or "")[:160],
                "level": level, "icon": str(icon)[:40] if icon else None, "params": _clean_params(params),
            }
        self.rev += 1
        self.notify()

    def remove(self, module, tile_id=None):
        with self.lock:
            for key in [k for k in self.items if k[0] == module and (tile_id is None or k[1] == str(tile_id))]:
                del self.items[key]
        self.rev += 1
        self.notify()

    def listing(self, translate):
        with self.lock:
            items = list(self.items.values())
        return [dict({k: v for k, v in t.items() if k != "params"},
                     title=translate(t["title"], t["params"]), text=translate(t["text"], t["params"]) if t["text"] else "")
                for t in items]
