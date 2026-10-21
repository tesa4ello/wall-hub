"""Рабочие столы и виджеты.

Экран делится на сетку layout.cols × layout.rows клеток. Стол — это список
виджетов, каждый занимает прямоугольник клеток (x, y, w, h).

Виджеты поставляют модули: web/modules/<модуль>/module.json описывает один
или несколько типов виджетов (название, размеры, схема настроек), а
module.js их рисует. Тип виджета — «модуль/виджет», например clock/clock.
У каждого экземпляра на столе свои настройки, так что один тип можно
поставить несколько раз (например, погоду для разных городов).
"""

import json
import os
import re

from .i18n import tr

MAX_DESKTOPS = 10
MAX_WIDGETS = 40
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class LayoutError(ValueError):
    pass


def load_catalog(web_root):
    """Типы виджетов всех модулей: {"модуль/виджет": описание}."""
    catalog = {}
    root = os.path.join(web_root, "modules")
    for module in sorted(os.listdir(root)):
        manifest = os.path.join(root, module, "module.json")
        if not NAME_RE.match(module) or not os.path.isfile(manifest):
            continue
        with open(manifest, encoding="utf-8") as f:
            info = json.load(f)
        for widget in info.get("widgets", []):
            if not NAME_RE.match(widget.get("id", "")):
                continue
            type_id = f"{module}/{widget['id']}"
            catalog[type_id] = dict(widget, id=type_id, module=module, module_title=info.get("title", module))
    return catalog


def size_limits(meta, cols, rows):
    size = meta.get("size", {})
    min_w, min_h = size.get("min", [1, 1])
    max_w, max_h = size.get("max", [cols, rows])
    return min_w, min_h, min(max_w, cols), min(max_h, rows)


def clean_options(meta, options, providers=()):
    """Оставляет только описанные в схеме настройки, приводя типы."""
    if not isinstance(options, dict):
        raise LayoutError(tr("Настройки виджета должны быть объектом"))
    result = {}
    for spec in meta.get("options", []):
        key, kind = spec["key"], spec["type"]
        value = options.get(key, spec.get("default"))
        if kind == "bool":
            value = bool(value)
        elif kind == "text":
            value = str(value or "")[: spec.get("max_length", 200)]
        elif kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise LayoutError(tr("«{title}»: нужно число", title=tr(spec["title"])))
            value = max(spec.get("min", value), min(spec.get("max", value), value))
            if spec.get("integer"):
                value = int(round(value))
        elif kind == "select":
            allowed = [choice[0] for choice in spec["choices"]]
            if value not in allowed:
                value = spec.get("default", allowed[0])
        elif kind == "providers":
            if not isinstance(value, list):
                raise LayoutError(tr("«{title}»: нужен список", title=tr(spec["title"])))
            value = [v for v in dict.fromkeys(value) if v in providers]
        else:
            continue
        result[key] = value
    return result


def default_options(meta, providers=()):
    return clean_options(meta, {}, providers)


def validate(desktops, catalog, cols, rows, providers=()):
    """Проверяет и нормализует список столов. Возвращает чистую копию."""
    if not isinstance(desktops, list) or not desktops:
        raise LayoutError(tr("Нужен хотя бы один стол"))
    if len(desktops) > MAX_DESKTOPS:
        raise LayoutError(tr("Не больше {n} столов", n=MAX_DESKTOPS))
    seen_ids = set()
    result = []
    for desk in desktops:
        if not isinstance(desk, dict):
            raise LayoutError(tr("Некорректный стол"))
        desk_id = str(desk.get("id", ""))
        if not ID_RE.match(desk_id) or desk_id in seen_ids:
            raise LayoutError(tr("Некорректный идентификатор стола"))
        seen_ids.add(desk_id)
        # Пустое имя: интерфейс покажет «Стол N» на языке панели.
        name = str(desk.get("name") or "").strip()[:40]
        widgets = desk.get("widgets", [])
        if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS:
            raise LayoutError(tr("На столе не больше {n} виджетов", n=MAX_WIDGETS))
        occupied = {}
        clean_widgets = []
        for w in widgets:
            if not isinstance(w, dict):
                raise LayoutError(tr("Некорректный виджет"))
            meta = catalog.get(w.get("type"))
            if meta is None:
                raise LayoutError(tr("Неизвестный виджет {type}", type=repr(w.get("type"))))
            w_id = str(w.get("id", ""))
            if not ID_RE.match(w_id) or w_id in seen_ids:
                raise LayoutError(tr("Некорректный идентификатор виджета"))
            seen_ids.add(w_id)
            try:
                x, y, width, height = (int(w[k]) for k in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                raise LayoutError(tr("У виджета должны быть x, y, w, h"))
            min_w, min_h, max_w, max_h = size_limits(meta, cols, rows)
            title = tr(meta["title"])
            if not (min_w <= width <= max_w and min_h <= height <= max_h):
                raise LayoutError(tr("«{title}»: размер от {min_w}×{min_h} до {max_w}×{max_h}",
                                      title=title, min_w=min_w, min_h=min_h, max_w=max_w, max_h=max_h))
            if x < 0 or y < 0 or x + width > cols or y + height > rows:
                raise LayoutError(tr("«{title}» выходит за край экрана", title=title))
            for cx in range(x, x + width):
                for cy in range(y, y + height):
                    if (cx, cy) in occupied:
                        raise LayoutError(tr("«{title}» перекрывает «{other}»", title=title, other=occupied[(cx, cy)]))
                    occupied[(cx, cy)] = title
            clean_widgets.append({
                "id": w_id, "type": meta["id"], "x": x, "y": y, "w": width, "h": height,
                "options": clean_options(meta, w.get("options", {}), providers),
            })
        result.append({"id": desk_id, "name": name, "widgets": clean_widgets})
    return result


def used_providers(desktops):
    """Провайдеры статусной строки, которые показывает хоть один виджет."""
    ids = []
    for desk in desktops:
        for w in desk["widgets"]:
            for pid in w.get("options", {}).get("items", []) if w["type"] == "system/statusbar" else []:
                if pid not in ids:
                    ids.append(pid)
    return ids
