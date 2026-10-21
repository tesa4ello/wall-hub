"""Провайдеры статусной строки.

Каждый провайдер — функция без аргументов, зарегистрированная декоратором
@provider("id"). Она возвращает словарь:

    {"icon": "cpu", "text": "0.42", "title": "подсказка", "level": "ok|warn|crit"}

или None, если показывать нечего. Чтобы добавить пункт в строку, достаточно
положить новый файл в этот пакет и вписать его id в настройку "statusbar".
"""

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

PROVIDERS = {}


def provider(provider_id, title=""):
    def register(func):
        func.provider_id = provider_id
        func.title = title or provider_id
        PROVIDERS[provider_id] = func
        return func
    return register


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")


def collect(ids):
    items = []
    for provider_id in ids:
        func = PROVIDERS.get(provider_id)
        if func is None:
            continue
        try:
            item = func()
        except Exception:
            log.exception("status provider %s failed", provider_id)
            item = None
        if item:
            items.append(dict(item, id=provider_id))
    return items


def catalog():
    return [{"id": pid, "title": func.title} for pid, func in PROVIDERS.items()]
