"""Переводы интерфейса.

Тексты в коде пишутся по-русски (это исходный язык), а переводы лежат в
web/i18n/<язык>.json: {"русский текст": "перевод"}. Тот же каталог читают
фронтенд (web/lib/i18n.js), установщик и обновлятор. Модули добавляют свои
переводы в module.json, в поле "i18n": {"en": {...}}.

Чтобы добавить язык: вписать его в web/i18n/languages.json и положить рядом
<код>.json. Непереведённые строки показываются по-русски.
"""

import json
import os

_languages = []
_source = "ru"
_catalogs = {}
_current = "ru"


def load(web_root):
    """Читает список языков и каталоги, включая переводы модулей."""
    global _languages, _source
    root = os.path.join(web_root, "i18n")
    with open(os.path.join(root, "languages.json"), encoding="utf-8") as f:
        meta = json.load(f)
    _source = meta["source"]
    _languages = meta["languages"]
    _catalogs.clear()
    for lang in _languages:
        code = lang["code"]
        path = os.path.join(root, f"{code}.json")
        catalog = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                catalog.update(json.load(f))
        catalog.update(module_catalog(web_root, code))
        _catalogs[code] = catalog


def module_catalog(web_root, code):
    catalog = {}
    modules = os.path.join(web_root, "modules")
    if not os.path.isdir(modules):
        return catalog
    for name in sorted(os.listdir(modules)):
        manifest = os.path.join(modules, name, "module.json")
        if os.path.isfile(manifest):
            with open(manifest, encoding="utf-8") as f:
                catalog.update(json.load(f).get("i18n", {}).get(code, {}))
    return catalog


def languages():
    return [lang["code"] for lang in _languages] or [_source]


def set_language(code):
    global _current
    _current = code if code in languages() else _source


def current():
    return _current


def tr(text, **params):
    """Перевод на текущий язык; {имя} в тексте заменяются на params."""
    if _current != _source:
        text = _catalogs.get(_current, {}).get(text, text)
    return text.format(**params) if params else text
