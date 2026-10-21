"""Рабочие столы и виджеты.

Экран делится на сетку layout.cols × layout.rows клеток. Стол — это список
виджетов, каждый занимает прямоугольник клеток (x, y, w, h).

Виджеты поставляют модули (см. modules.py): module.json описывает один
или несколько типов виджетов (название, размеры, схема настроек), а
module.js их рисует. Тип виджета — «модуль/виджет», например clock/clock.
У каждого экземпляра на столе свои настройки, так что один тип можно
поставить несколько раз (например, погоду для разных городов).
"""

import re

from .i18n import tr

MAX_DESKTOPS = 10
MAX_WIDGETS = 40
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
TYPE_RE = re.compile(r"^[a-z0-9_-]{1,32}/[a-z0-9_-]{1,32}$")


class LayoutError(ValueError):
    pass


def load_catalog(modules):
    """Типы виджетов загруженных модулей: {"модуль/виджет": описание}.

    modules — {id: Module} из modules.Registry.loaded().
    """
    catalog = {}
    for name, module in sorted(modules.items()):
        info = module.meta
        for widget in info.get("widgets", []):
            if not NAME_RE.match(widget.get("id", "")):
                continue
            type_id = f"{name}/{widget['id']}"
            catalog[type_id] = dict(widget, id=type_id, module=name, module_title=info.get("title", name))
    return catalog


def size_limits(meta, cols, rows):
    size = meta.get("size", {})
    min_w, min_h = size.get("min", [1, 1])
    max_w, max_h = size.get("max", [cols, rows])
    return min_w, min_h, min(max_w, cols), min(max_h, rows)


def clean_location(value):
    """Место виджета: {"mode": "panel"} (как у панели) или
    {"mode": "custom", "name", "lat", "lon"}. Неполное своё место
    превращается в «как у панели»."""
    if isinstance(value, dict) and value.get("mode") == "custom":
        try:
            lat, lon = float(value["lat"]), float(value["lon"])
        except (KeyError, TypeError, ValueError):
            return {"mode": "panel"}
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            place = {"mode": "custom", "name": str(value.get("name") or "")[:80],
                     "lat": round(lat, 4), "lon": round(lon, 4)}
            # Часовой пояс места (из поиска): нужен источникам, которые его не сообщают.
            tz = value.get("tz")
            if isinstance(tz, str) and re.fullmatch(r"[A-Za-z0-9_+/-]{1,64}", tz):
                place["tz"] = tz
            return place
    return {"mode": "panel"}


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
        elif kind == "location":
            value = clean_location(value)
        elif kind == "choice":
            # Варианты знает серверная часть модуля, ядро хранит только строку.
            value = str(value or "")[:64]
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
            w_type = w.get("type")
            if not isinstance(w_type, str) or not TYPE_RE.match(w_type):
                raise LayoutError(tr("Неизвестный виджет {type}", type=repr(w_type)))
            # Виджет удалённого или сломанного модуля остаётся на столе как есть
            # (на экране заглушка), чтобы из-за него не ломалось сохранение.
            known = w_type in catalog
            meta = catalog.get(w_type) or {"id": w_type, "title": w_type}
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
            options = w.get("options", {})
            if known:
                options = clean_options(meta, options, providers)
            elif not isinstance(options, dict):
                options = {}
            clean = {"id": w_id, "type": w_type, "x": x, "y": y, "w": width, "h": height, "options": options}
            # Плитка-подложка: своя у виджета или как задано в каталоге.
            if isinstance(w.get("card"), bool) and w["card"] != bool(meta.get("card")):
                clean["card"] = w["card"]
            clean_widgets.append(clean)
        clean_desk = {"id": desk_id, "name": name, "widgets": clean_widgets}
        # Карусель: стол можно исключить или задать ему свой интервал (секунды).
        if desk.get("carousel") is False:
            clean_desk["carousel"] = False
        if desk.get("interval"):
            clean_desk["interval"] = carousel_seconds(desk["interval"])
        result.append(clean_desk)
    return result


CAROUSEL_SECONDS = (5, 3600)
CAROUSEL_RESUME_MIN = (0, 240)


def carousel_seconds(value):
    lo, hi = CAROUSEL_SECONDS
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise LayoutError(tr("Интервал карусели: от {lo} до {hi} секунд", lo=lo, hi=hi))
    return value


def validate_carousel(value):
    """Настройки карусели столов: {enabled, interval (с), resume (мин)}."""
    if not isinstance(value, dict):
        raise LayoutError(tr("Некорректные настройки карусели"))
    resume = value.get("resume", 2)
    lo, hi = CAROUSEL_RESUME_MIN
    if not isinstance(resume, int) or isinstance(resume, bool) or not lo <= resume <= hi:
        raise LayoutError(tr("Пауза карусели после касания: от {lo} до {hi} минут", lo=lo, hi=hi))
    return {"enabled": bool(value.get("enabled")), "interval": carousel_seconds(value.get("interval", 30)), "resume": resume}


def used_providers(desktops):
    """Провайдеры статусной строки, которые показывает хоть один виджет."""
    ids = []
    for desk in desktops:
        for w in desk["widgets"]:
            for pid in w.get("options", {}).get("items", []) if w["type"] == "system/statusbar" else []:
                if pid not in ids:
                    ids.append(pid)
    return ids
