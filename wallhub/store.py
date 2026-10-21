"""Магазин модулей: каталоги на GitHub, установка, обновление и удаление.

Каталог — git-репозиторий, где модули лежат в modules/<id>/, а CI собирает
индекс catalog.json (см. tesa4ello/wallhub-modules). Панель читает индекс из
последнего коммита ветки, а модуль ставит из архива того же коммита в
/var/lib/wallhub/modules/<id>/<версия>-<коммит>; symlink current указывает на
рабочую версию, previous — на прошлую.

Модули без серверной части — просто файлы, поэтому их ставит сам сервис,
без root и без обновлятора.
"""

import datetime
import io
import json
import logging
import os
import re
import shutil
import tarfile
import threading
import urllib.error
import urllib.request

from .modules import ID_RE, compatible, read

log = logging.getLogger(__name__)

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_CATALOGS = 10


class StoreError(Exception):
    """message — исходная (русская) строка для tr(), params — подстановки."""

    def __init__(self, message, **params):
        super().__init__(message)
        self.message = message
        self.params = params


def parse_repo(text):
    """«owner/repo» из ссылки на GitHub или короткой записи."""
    text = (text or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+/[^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$", text)
    repo = m.group(1) if m else text
    if not REPO_RE.match(repo):
        raise StoreError("Нужна ссылка на репозиторий GitHub или запись вида owner/repo")
    return repo


def version_key(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, config, modules_dir, token_path, builtin_ids, on_change=lambda: None, fetch=None):
        self.config = config
        self.dir = modules_dir
        self.token_path = token_path
        self.builtin_ids = set(builtin_ids)
        self.on_change = on_change
        self.fetch = fetch or self._fetch
        self.index = {}  # id каталога -> {commit, modules, fetched_at} или {error}
        self.lock = threading.Lock()

    # --- каталоги -------------------------------------------------------------

    def catalogs(self):
        """Подключённые каталоги по порядку: основной, затем добавленные."""
        store = self.config.get("store")
        main = dict(store["main"], id="main", main=True)
        return [main] + [dict(c, main=False) for c in store.get("catalogs", [])]

    def add_catalog(self, url, branch="main"):
        repo = parse_repo(url)
        branch = (branch or "main").strip()
        if not BRANCH_RE.match(branch):
            raise StoreError("Некорректное имя ветки")
        extra = self.config.get("store", "catalogs")
        if any(c["repo"].lower() == repo.lower() for c in self.catalogs()):
            raise StoreError("Этот каталог уже подключён")
        if len(extra) >= MAX_CATALOGS:
            raise StoreError("Не больше {n} каталогов", n=MAX_CATALOGS)
        cat_id = "c" + str(max([int(c["id"][1:]) for c in extra if c["id"][1:].isdigit()] + [0]) + 1)
        catalog = {"id": cat_id, "repo": repo, "branch": branch, "enabled": True}
        self._refresh_one(dict(catalog, main=False))  # проверяем, что каталог читается
        self.config.update({"store": {"catalogs": extra + [catalog]}})
        return catalog

    def set_catalog(self, cat_id, enabled=None, move=None, branch=None):
        if cat_id == "main":
            patch = {}
            if enabled is not None:
                patch["enabled"] = bool(enabled)
            if branch:
                if not BRANCH_RE.match(branch):
                    raise StoreError("Некорректное имя ветки")
                patch["branch"] = branch
            self.config.update({"store": {"main": patch}})
            self.index.pop("main", None)
            return
        extra = self.config.get("store", "catalogs")
        pos = next((i for i, c in enumerate(extra) if c["id"] == cat_id), None)
        if pos is None:
            raise StoreError("Нет такого каталога")
        if enabled is not None:
            extra[pos]["enabled"] = bool(enabled)
        if branch:
            if not BRANCH_RE.match(branch):
                raise StoreError("Некорректное имя ветки")
            extra[pos]["branch"] = branch
            self.index.pop(cat_id, None)
        if move in (-1, 1) and 0 <= pos + move < len(extra):
            extra[pos], extra[pos + move] = extra[pos + move], extra[pos]
        self.config.update({"store": {"catalogs": extra}})

    def remove_catalog(self, cat_id):
        extra = self.config.get("store", "catalogs")
        if cat_id == "main" or not any(c["id"] == cat_id for c in extra):
            raise StoreError("Нет такого каталога")
        self.config.update({"store": {"catalogs": [c for c in extra if c["id"] != cat_id]}})
        self.index.pop(cat_id, None)

    # --- GitHub -----------------------------------------------------------------

    def _token(self):
        try:
            with open(self.token_path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _fetch(self, url, accept="application/vnd.github+json", limit=MAX_ARCHIVE):
        headers = {"Accept": accept, "User-Agent": "wallhub-store"}
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                data = r.read(limit + 1)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise StoreError("GitHub ответил {code}: проверьте ссылку, токен и доступ к репозиторию", code=e.code)
            raise StoreError("GitHub ответил {code}", code=e.code)
        except (urllib.error.URLError, OSError) as e:
            raise StoreError("Нет связи с GitHub: {reason}", reason=str(getattr(e, "reason", e)))
        if len(data) > limit:
            raise StoreError("Архив каталога больше {mb} МБ", mb=limit // 1024 // 1024)
        return data

    def _refresh_one(self, cat):
        api = f"https://api.github.com/repos/{cat['repo']}"
        try:
            commit = json.loads(self.fetch(f"{api}/commits/{cat['branch']}"))["sha"]
            raw = self.fetch(f"{api}/contents/catalog.json?ref={commit}", accept="application/vnd.github.raw")
            data = json.loads(raw)
            modules = [m for m in data.get("modules", []) if isinstance(m, dict) and ID_RE.match(str(m.get("id", "")))]
        except StoreError as e:
            self.index[cat["id"]] = {"error": e.message, "error_args": e.params, "fetched_at": now()}
            raise
        except (ValueError, KeyError, TypeError):
            self.index[cat["id"]] = {"error": "В каталоге нет корректного catalog.json", "error_args": {},
                                     "fetched_at": now()}
            raise StoreError("В каталоге нет корректного catalog.json")
        self.index[cat["id"]] = {"commit": commit, "modules": modules, "fetched_at": now()}

    def refresh(self):
        """Перечитывает индексы всех включённых каталогов. Ошибки остаются в index."""
        for cat in self.catalogs():
            if cat["enabled"]:
                try:
                    self._refresh_one(cat)
                except StoreError as e:
                    log.warning("каталог %s: %s", cat["repo"], e.message.format(**e.params))

    # --- установленные ------------------------------------------------------------

    def _installed_path(self):
        return os.path.join(self.dir, "installed.json")

    def installed(self):
        try:
            with open(self._installed_path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_installed(self, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._installed_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._installed_path())

    def listing(self):
        """Модули всех каталогов для админки, с учётом установленных."""
        if not self.index:
            self.refresh()
        installed = self.installed()
        seen, rows = set(), []
        catalogs = []
        for cat in self.catalogs():
            entry = self.index.get(cat["id"], {}) if cat["enabled"] else {}
            catalogs.append({k: cat[k] for k in ("id", "repo", "branch", "enabled", "main")} | {
                "error": entry.get("error"), "error_args": entry.get("error_args", {}),
                "commit": entry.get("commit", ""), "fetched_at": entry.get("fetched_at")})
            for m in entry.get("modules", []):
                if m["id"] in seen:
                    continue  # побеждает каталог выше по списку
                seen.add(m["id"])
                inst = installed.get(m["id"])
                rows.append({
                    "id": m["id"], "version": m.get("version", ""), "title": m.get("title", m["id"]),
                    "description": m.get("description", ""), "i18n": m.get("i18n", {}),
                    "widgets": m.get("widgets", []), "backend": bool(m.get("backend")),
                    "catalog": cat["id"], "builtin": m["id"] in self.builtin_ids,
                    "compatible": compatible(m.get("core")),
                    "installed": inst.get("version") if inst else None,
                    "update": bool(inst and version_key(m.get("version")) > version_key(inst.get("version"))),
                })
        # Установленные модули, которых больше нет ни в одном каталоге.
        for mid, inst in sorted(installed.items()):
            if mid not in seen:
                rows.append({"id": mid, "version": inst.get("version", ""), "title": inst.get("title", mid),
                             "description": "", "i18n": inst.get("i18n", {}), "widgets": [], "backend": False,
                             "catalog": inst.get("catalog"), "builtin": False, "compatible": True,
                             "installed": inst.get("version"), "update": False, "orphan": True})
        return {"catalogs": catalogs, "modules": rows}

    # --- установка ----------------------------------------------------------------

    def install(self, module_id):
        """Ставит или обновляет модуль из каталога, где он найден первым."""
        with self.lock:
            self.refresh()
            row = next((m for m in self.listing()["modules"] if m["id"] == module_id and not m.get("orphan")), None)
            if row is None:
                raise StoreError("Модуля {id} нет в подключённых каталогах", id=module_id)
            if row["builtin"]:
                raise StoreError("Модуль {id} уже входит в ядро", id=module_id)
            if not row["compatible"]:
                raise StoreError("Модуль {id} не подходит к этой версии панели", id=module_id)
            cat = next(c for c in self.catalogs() if c["id"] == row["catalog"])
            commit = self.index[cat["id"]]["commit"]
            data = self.fetch(f"https://api.github.com/repos/{cat['repo']}/tarball/{commit}",
                              accept="application/vnd.github+json")
            target = self._unpack(data, module_id, f"{row['version']}-{commit[:7]}")
            module = read(module_id, target, "installed")
            if module.error:
                shutil.rmtree(target, ignore_errors=True)
                raise StoreError("Модуль {id} не прошёл проверку: {error}", id=module_id, error=module.error)
            self._switch(module_id, os.path.basename(target))
            installed = self.installed()
            installed[module_id] = {
                "version": module.meta.get("version", row["version"]), "catalog": cat["id"], "repo": cat["repo"],
                "commit": commit, "installed_at": now(), "title": module.meta.get("title", module_id),
                "i18n": row["i18n"],
            }
            self._save_installed(installed)
        log.info("модуль %s %s установлен из %s@%s", module_id, row["version"], cat["repo"], commit[:7])
        self.on_change()
        return installed[module_id]

    def _unpack(self, data, module_id, name):
        base = os.path.join(self.dir, module_id)
        target = os.path.join(base, name)
        tmp = os.path.join(base, f".{name}.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        found = False
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                members = []
                for member in tar.getmembers():
                    parts = member.name.split("/")
                    # <корень архива>/modules/<id>/...
                    if len(parts) < 4 or parts[1:3] != ["modules", module_id]:
                        continue
                    if not (member.isfile() or member.isdir()):
                        raise StoreError("В модуле {id} есть ссылки или особые файлы", id=module_id)
                    member.name = "/".join(parts[3:])
                    members.append(member)
                    found = found or member.name == "module.json"
                if not found:
                    raise StoreError("В архиве каталога нет модуля {id}", id=module_id)
                tar.extractall(tmp, members=members, filter="data")
        except tarfile.TarError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise StoreError("Архив каталога повреждён: {error}", error=str(e))
        except StoreError:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        shutil.rmtree(target, ignore_errors=True)
        os.rename(tmp, target)
        return target

    def _switch(self, module_id, name):
        """current → новая версия, previous → бывшая current, остальные удаляются."""
        base = os.path.join(self.dir, module_id)
        current = os.path.join(base, "current")
        old = os.readlink(current) if os.path.islink(current) else None
        tmp = current + ".new"
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(name, tmp)
        os.replace(tmp, current)
        previous = os.path.join(base, "previous")
        if old and old != name:
            if os.path.lexists(previous):
                os.remove(previous)
            os.symlink(old, previous)
        keep = {name, os.readlink(previous) if os.path.islink(previous) else None, "current", "previous"}
        for entry in os.listdir(base):
            if entry not in keep and not entry.startswith("."):
                shutil.rmtree(os.path.join(base, entry), ignore_errors=True)

    def remove(self, module_id):
        installed = self.installed()
        if module_id not in installed:
            raise StoreError("Модуль {id} не установлен", id=module_id)
        with self.lock:
            shutil.rmtree(os.path.join(self.dir, module_id), ignore_errors=True)
            del installed[module_id]
            self._save_installed(installed)
            # Виджеты модуля убираем со столов.
            desks = self.config.get("desktops")
            for desk in desks:
                desk["widgets"] = [w for w in desk["widgets"] if not w.get("type", "").startswith(module_id + "/")]
            self.config.update({"desktops": desks})
        log.info("модуль %s удалён", module_id)
        self.on_change()

    def updates_available(self):
        return sum(1 for m in self.listing()["modules"] if m["update"]) if self.index else 0
