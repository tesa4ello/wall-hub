"""Модули виджетов: штатные (web/modules) и сторонние.

Модуль — папка с module.json, module.js и, по желанию, module.css. Штатные
модули лежат в ядре. Сторонние ставятся из магазина в
/var/lib/wallhub/modules/<id>/ (сам модуль в подпапке current или прямо там),
а при разработке подключаются флагом --modules-dir <папка с модулями>.

Модуль с тем же id из источника выше по списку побеждает: штатные, затем
--modules-dir, затем установленные. Сторонний модуль грузится, только если
его поле core совпадает с мажорной версией API ядра (CORE_API).
"""

import json
import logging
import os
import re

from . import CORE_API

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Эти файлы модуля браузеру не отдаются.
PRIVATE = ("backend", "requirements.txt")


class Module:
    def __init__(self, module_id, path, source, meta=None, error=None):
        self.id = module_id
        self.path = path
        self.source = source  # builtin | dev | installed
        self.meta = meta or {}
        self.error = error

    @property
    def builtin(self):
        return self.source == "builtin"

    def info(self):
        return {
            "id": self.id,
            "version": self.meta.get("version", ""),
            "title": self.meta.get("title", self.id),
            "description": self.meta.get("description", ""),
            "source": self.source,
            "error": self.error,
            # admin.js: своя страница настроек модуля в админке.
            "admin": os.path.isfile(os.path.join(self.path, "admin.js")),
            "backend": bool(self.meta.get("backend")),
        }


def compatible(core):
    return str(core or "").split(".")[0] == CORE_API.split(".")[0]


def read(module_id, path, source):
    try:
        with open(os.path.join(path, "module.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        return Module(module_id, path, source, error=f"module.json: {e}")
    if source != "builtin":
        if meta.get("id") != module_id:
            return Module(module_id, path, source, meta, "id в module.json не совпадает с папкой")
        if not compatible(meta.get("core")):
            return Module(module_id, path, source, meta,
                          f"нужно ядро с API {meta.get('core')}, у этого ядра {CORE_API}")
    return Module(module_id, path, source, meta)


class Registry:
    def __init__(self, builtin_dir, dev_dirs=(), installed_dir=None):
        self.sources = [(builtin_dir, "builtin")]
        self.sources += [(d, "dev") for d in dev_dirs]
        if installed_dir:
            self.sources.append((installed_dir, "installed"))

    def scan(self):
        """Все найденные модули {id: Module}, включая сломанные (с error)."""
        found = {}
        for root, source in self.sources:
            try:
                names = sorted(os.listdir(root))
            except OSError:
                continue
            for name in names:
                if name in found or name.startswith((".", "_")) or not ID_RE.match(name):
                    continue
                path = os.path.join(root, name)
                if source == "installed" and os.path.isdir(os.path.join(path, "current")):
                    path = os.path.join(path, "current")
                if not os.path.isfile(os.path.join(path, "module.json")):
                    continue
                module = read(name, os.path.realpath(path), source)
                if module.error:
                    log.warning("модуль %s (%s) не загружен: %s", name, source, module.error)
                found[name] = module
        return found

    def loaded(self):
        return {k: m for k, m in self.scan().items() if not m.error}

    def static_file(self, module_id, rel):
        """Путь к файлу модуля для браузера или None."""
        module = self.loaded().get(module_id)
        if module is None or not rel or rel.split("/")[0] in PRIVATE:
            return None
        full = os.path.realpath(os.path.join(module.path, rel))
        if not full.startswith(module.path + os.sep) or not os.path.isfile(full):
            return None
        return full
