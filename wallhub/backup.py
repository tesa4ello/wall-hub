"""Резервная копия настроек панели: экспорт, восстановление, автокопии.

Копия — zip-архив:

  manifest.json          формат, версия панели, дата, имя панели, части
  config.json            настройки (столы, будильники, тема, …) без секретов
  secrets.json           пароль админки (хеш), ключи погоды и Локатора
  tokens/github-token    токены GitHub: общий и каталогов магазина
  tokens/store/<id>
  backgrounds/<файлы>    загруженные фоны
  modules/installed.json модули из магазина: запись и код текущей версии
  modules/<id>/...
  module-data/<id>/...   данные модулей (data_dir), там бывают SSH-ключи

Части выбираются при экспорте и при восстановлении:
settings, backgrounds, modules, secrets (секреты, токены и данные модулей).
Без части secrets пароль, ключи и данные модулей на панели остаются свои.

Автокопии лежат в state/backups (0700): раз в сутки и перед каждым
восстановлением. Они полные, кроме фонов (фоны большие, а восстановление
без части backgrounds их не трогает).
"""

import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__
from .config import DEFAULTS, SECRET_PATHS
from .modules import read as read_module

log = logging.getLogger(__name__)

FORMAT = 1
PARTS = ("settings", "backgrounds", "modules", "secrets")
EXPORT_DEFAULT = ("settings", "backgrounds", "modules")
AUTO_PARTS = ("settings", "modules", "secrets")
# Сколько автокопий хранить по каждой причине: ручные не вытесняют
# ежедневные и сделанные перед восстановлением.
KEEP_AUTO = {"daily": 5, "restore": 3, "manual": 3}
AUTO_EVERY = 24 * 3600
# Загрузка копии и распаковка: фонов может быть много.
MAX_UPLOAD = 256 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
PULL_TIMEOUT = 60
SNAPSHOT_RE = re.compile(r"^auto-\d{8}-\d{6}(-[a-z]+)?\.zip$")
MODULE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
CAT_RE = re.compile(r"^(main|c\d{1,4})$")
BG_RE = re.compile(r"^[0-9a-f]{12}(?:@\d+x\d+)?(?:-blur)?\.(?:jpg|png|webp)$")
# Настройки, которые описывают именно это устройство и при переносе не меняются.
DEVICE_KEYS = ("setup",)


class BackupError(Exception):
    """Текст — исходная русская строка, переводит сервер (tr(e.text, **e.params))."""

    def __init__(self, text, **params):
        super().__init__(text)
        self.text = text
        self.params = params


def _now():
    return datetime.datetime.now().astimezone()


def _split_secrets(data):
    """(настройки без секретов, {путь: значение})."""
    data = json.loads(json.dumps(data))
    found = {}
    for path in SECRET_PATHS:
        node = data
        for key in path[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict) and path[-1] in node:
            found[".".join(path)] = node.pop(path[-1])
    return data, found


def _put_secrets(data, secrets):
    for path in SECRET_PATHS:
        key = ".".join(path)
        if key not in secrets:
            continue
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = secrets[key]


def _add_tree(zf, root, prefix):
    """Файлы каталога в архив (ссылки и особые файлы пропускаем)."""
    if not os.path.isdir(root):
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not os.path.islink(os.path.join(base, d)))
        for name in sorted(files):
            full = os.path.join(base, name)
            if os.path.islink(full) or not os.path.isfile(full) or name.endswith((".pyc", ".tmp")):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            zf.write(full, f"{prefix}/{rel}")


def _panel_url(address):
    """«192.168.1.20», «panel.local:8080», «http://…» -> http://хост[:порт]."""
    text = str(address or "").strip()
    if not text:
        raise BackupError("Укажите адрес старой панели")
    if "://" not in text:
        text = "http://" + text
    url = urllib.parse.urlsplit(text)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise BackupError("Укажите адрес старой панели")
    try:
        port = url.port
    except ValueError:
        raise BackupError("Укажите адрес старой панели")
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    return f"{url.scheme}://{host}{f':{port}' if port else ''}"


_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def _translit(text):
    """Имя панели для имени файла: «Кухня» → «Kukhnya»."""
    out = []
    for ch in str(text):
        low = ch.lower()
        if low in _TRANSLIT:
            lat = _TRANSLIT[low]
            out.append(lat.capitalize() if ch != low else lat)
        else:
            out.append(ch)
    return "".join(out)


class Backups:
    def __init__(self, config, state_dir, backgrounds_dir, modules_dir, data_dir, update_token, store_tokens,
                 before_restore=lambda parts: None, on_restore=lambda parts: None, clock=time.time):
        self.config = config
        self.state_dir = state_dir
        self.dir = os.path.join(state_dir, "backups")
        self.backgrounds_dir = backgrounds_dir
        self.modules_dir = modules_dir
        self.data_dir = data_dir
        self.update_token = update_token
        self.store_tokens = store_tokens
        # before_restore останавливает серверные части модулей, on_restore
        # перечитывает модули и запускает их снова.
        self.before_restore = before_restore
        self.on_restore = on_restore
        self.clock = clock
        self.lock = threading.Lock()
        self._stop = threading.Event()

    def _ensure_dir(self):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)

    # --- создание ---------------------------------------------------------------

    def build(self, path, parts):
        """Пишет архив с частями parts в path."""
        parts = [p for p in PARTS if p in parts]
        if not parts:
            raise BackupError("Выберите, что сохранить")
        data = self.config.get()
        plain, secrets = _split_secrets(data)
        installed = self._installed()
        manifest = {
            "format": FORMAT, "app": "wall-hub", "version": __version__,
            "created": _now().isoformat(timespec="seconds"),
            "panel": data["ui"].get("panel_name") or socket.gethostname(),
            "parts": parts,
            "modules": {mid: e.get("version", "") for mid, e in installed.items()} if "modules" in parts else {},
            "alarms": len(data.get("alarms", [])), "desktops": len(data.get("desktops", [])),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if "settings" in parts:
                zf.writestr("config.json", json.dumps(plain, ensure_ascii=False, indent=2))
            if "secrets" in parts:
                zf.writestr("secrets.json", json.dumps(secrets, ensure_ascii=False, indent=2))
                if os.path.isfile(self.update_token):
                    zf.write(self.update_token, "tokens/github-token")
                _add_tree(zf, self.store_tokens, "tokens/store")
                _add_tree(zf, self.data_dir, "module-data")
            if "backgrounds" in parts and os.path.isdir(self.backgrounds_dir):
                for name in sorted(os.listdir(self.backgrounds_dir)):
                    if BG_RE.match(name):
                        zf.write(os.path.join(self.backgrounds_dir, name), f"backgrounds/{name}")
            if "modules" in parts:
                zf.writestr("modules/installed.json", json.dumps(installed, ensure_ascii=False, indent=2))
                for mid in installed:
                    current = os.path.join(self.modules_dir, mid, "current")
                    if os.path.isdir(current):
                        _add_tree(zf, os.path.realpath(current), f"modules/{mid}")
        return manifest

    def export(self, parts):
        """Временный файл с копией и имя для скачивания. Файл удаляет вызывающий."""
        self._ensure_dir()
        fd, path = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=self.dir)
        os.close(fd)
        try:
            manifest = self.build(path, parts)
        except BaseException:
            os.remove(path)
            raise
        panel = re.sub(r"[^A-Za-z0-9_-]+", "-", _translit(manifest["panel"])).strip("-") or "panel"
        stamp = _now().strftime("%Y%m%d-%H%M")
        return path, f"wallhub-{panel}-{stamp}.zip"

    def _installed(self):
        try:
            with open(os.path.join(self.modules_dir, "installed.json"), encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if MODULE_RE.match(k)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # --- автокопии --------------------------------------------------------------

    def snapshot(self, reason="", protect=None):
        """Полная копия (без фонов) в state/backups; по каждой причине хранится
        KEEP_AUTO последних. protect — автокопия, которую сейчас
        восстанавливают: её не удаляем."""
        with self.lock:
            self._ensure_dir()
            stamp = datetime.datetime.fromtimestamp(self.clock()).strftime("%Y%m%d-%H%M%S")
            name = f"auto-{stamp}{'-' + reason if reason else ''}.zip"
            path = os.path.join(self.dir, name)
            self.build(path + ".tmp", AUTO_PARTS)
            os.chmod(path + ".tmp", 0o600)
            os.replace(path + ".tmp", path)
            self._prune(protect)
        log.info("автокопия настроек %s", name)
        return name

    def _prune(self, protect=None):
        seen = {}
        for old in self.snapshots():
            reason = old["reason"] if old["reason"] in KEEP_AUTO else "daily"
            seen[reason] = seen.get(reason, 0) + 1
            if seen[reason] > KEEP_AUTO[reason] and old["name"] != protect:
                os.remove(os.path.join(self.dir, old["name"]))

    def snapshots(self):
        """Автокопии, новые первыми."""
        try:
            names = [n for n in os.listdir(self.dir) if SNAPSHOT_RE.match(n)]
        except FileNotFoundError:
            return []
        rows = []
        for name in sorted(names, reverse=True):
            full = os.path.join(self.dir, name)
            try:
                with zipfile.ZipFile(full) as zf:
                    manifest = json.loads(zf.read("manifest.json"))
            except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                continue
            rows.append({"name": name, "size": os.path.getsize(full), "created": manifest.get("created"),
                         "version": manifest.get("version"), "reason": name[len("auto-") + 15:-4].lstrip("-")})
        return rows

    def snapshot_path(self, name):
        if not SNAPSHOT_RE.match(str(name)) or not os.path.isfile(os.path.join(self.dir, name)):
            raise BackupError("Нет такой копии")
        return os.path.join(self.dir, name)

    def _auto_due(self):
        last = self.snapshots()
        if not last:
            return True
        newest = last[0]
        if newest.get("version") != __version__:
            return True  # панель обновилась: копия уже на новой версии
        mtime = os.path.getmtime(os.path.join(self.dir, newest["name"]))
        return self.clock() - mtime >= AUTO_EVERY

    def start(self):
        threading.Thread(target=self._run, name="backups", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # Первая проверка — через минуту после запуска, потом раз в час.
        delay = 60
        while not self._stop.wait(delay):
            delay = 3600
            try:
                if self._auto_due():
                    self.snapshot("daily")
            except Exception:
                log.exception("автокопия не удалась")

    # --- загрузка и проверка ----------------------------------------------------

    def upload_path(self):
        self._ensure_dir()
        return os.path.join(self.dir, "upload.zip")

    def save_upload(self, stream, length):
        """Принимает файл копии из запроса и возвращает её описание."""
        if length <= 0:
            raise BackupError("Файл пустой")
        if length > MAX_UPLOAD:
            raise BackupError("Файл больше {n} МБ", n=MAX_UPLOAD // (1024 * 1024))
        path = self.upload_path()
        with open(path + ".tmp", "wb") as f:
            left = length
            while left:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        os.chmod(path + ".tmp", 0o600)
        os.replace(path + ".tmp", path)
        try:
            return self.inspect(path)
        except BackupError:
            os.remove(path)
            raise

    def pull(self, address, password, opener=None):
        """Забирает полную копию со старой панели по сети (её админка, вход
        по паролю) и кладёт как загруженную. Старой панели нужна версия 1.1.0+."""
        base = _panel_url(address)
        # Старая панель в той же сети: системный прокси (если он задан) только мешает.
        opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())

        def call(path, data=None):
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            return opener.open(req, timeout=PULL_TIMEOUT)

        try:
            call("/api/login", json.dumps({"password": str(password)}).encode()).close()
            parts = ",".join(PARTS)
            with call(f"/api/admin/backup/export?parts={parts}") as resp:
                length = int(resp.headers.get("Content-Length") or 0)
                if not length:
                    raise BackupError("Старая панель не отдала копию")
                info = self.save_upload(resp, length)
            try:
                call("/api/logout", b"{}").close()
            except (OSError, urllib.error.URLError):
                pass
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise BackupError("Неверный пароль старой панели")
            if e.code == 404:
                raise BackupError("Старая панель не умеет отдавать копию: обновите её до 1.1.0")
            raise BackupError("Старая панель ответила ошибкой {code}", code=e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            reason = getattr(e, "reason", e)
            raise BackupError("Старая панель не отвечает: {error}", error=str(reason))
        return info

    def source(self, name):
        """Путь к копии: "upload" — загруженная, иначе имя автокопии."""
        if name == "upload":
            path = self.upload_path()
            if not os.path.isfile(path):
                raise BackupError("Сначала загрузите файл копии")
            return path
        return self.snapshot_path(name)

    def inspect(self, path):
        """Проверяет архив и возвращает manifest с найденными частями."""
        try:
            with zipfile.ZipFile(path) as zf:
                total = 0
                for info in zf.infolist():
                    name = info.filename
                    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                        raise BackupError("Файл копии повреждён")
                    total += info.file_size
                if total > MAX_UNPACKED:
                    raise BackupError("Файл копии повреждён")
                manifest = self._json(zf, "manifest.json")
                names = set(zf.namelist())
        except (OSError, zipfile.BadZipFile):
            raise BackupError("Это не файл копии Wall-HUB")
        if not isinstance(manifest, dict) or manifest.get("app") != "wall-hub":
            raise BackupError("Это не файл копии Wall-HUB")
        if not isinstance(manifest.get("format"), int) or manifest["format"] > FORMAT:
            raise BackupError("Копия сделана более новой версией панели, сначала обновите панель")
        found = []
        if "config.json" in names:
            found.append("settings")
        if any(n.startswith("backgrounds/") for n in names):
            found.append("backgrounds")
        if "modules/installed.json" in names:
            found.append("modules")
        if "secrets.json" in names:
            found.append("secrets")
        keep = ("version", "created", "panel", "modules", "alarms", "desktops")
        return {k: manifest.get(k) for k in keep} | {"parts": found}

    @staticmethod
    def _json(zf, name):
        try:
            info = zf.getinfo(name)
        except KeyError:
            raise BackupError("Это не файл копии Wall-HUB")
        if info.file_size > MAX_JSON:
            raise BackupError("Файл копии повреждён")
        try:
            return json.loads(zf.read(name))
        except ValueError:
            raise BackupError("Файл копии повреждён")

    # --- восстановление ---------------------------------------------------------

    def restore(self, source, parts):
        """Восстанавливает части parts из копии source ("upload" или имя
        автокопии). Перед этим делает автокопию текущего состояния."""
        path = self.source(source)
        info = self.inspect(path)
        parts = [p for p in PARTS if p in parts and p in info["parts"]]
        if not parts:
            raise BackupError("Выберите, что восстановить")
        # Восстановление из автокопии тоже даёт новую автокопию: её можно
        # вернуть, если выбрали не ту.
        before = self.snapshot("restore", protect=source)
        self.before_restore(parts)
        try:
            self._apply(path, parts)
        finally:
            self.on_restore(parts)
        if source == "upload":
            # Загруженная копия может хранить пароль и ключи: лишняя копия
            # секретов на диске не нужна.
            with contextlib.suppress(OSError):
                os.remove(path)
        else:
            with self.lock:
                self._prune()
        log.info("восстановлено из копии %s: %s", source, ", ".join(parts))
        return {"parts": parts, "snapshot": before}

    def _apply(self, path, parts):
        with self.lock, zipfile.ZipFile(path) as zf:
            if "secrets" in parts:
                self._restore_files(zf)
            if "backgrounds" in parts:
                self._restore_backgrounds(zf)
            if "modules" in parts:
                self._restore_modules(zf)
            self._restore_config(zf, "settings" in parts, "secrets" in parts)

    def _restore_config(self, zf, settings, secrets):
        current = self.config.get()
        if settings:
            data = self._json(zf, "config.json")
            if not isinstance(data, dict):
                raise BackupError("Файл копии повреждён")
            data.pop("_revision", None)
            data.pop("revision", None)
            for key in DEVICE_KEYS:
                if key in current:
                    data[key] = current[key]
        else:
            data = current
        _, own = _split_secrets(current)
        _put_secrets(data, own)
        if secrets:
            stored = self._json(zf, "secrets.json")
            if isinstance(stored, dict):
                _put_secrets(data, stored)
        self._fix_backgrounds(data)
        self.config.replace(data)

    def _fix_backgrounds(self, data):
        """Фон, которого нет на этой панели, заменяем встроенным."""
        ui = data.get("ui")
        if not isinstance(ui, dict):
            return
        have = set()
        if os.path.isdir(self.backgrounds_dir):
            have = {n.split(".")[0].split("@")[0].removesuffix("-blur") for n in os.listdir(self.backgrounds_dir)}
        for key in ("background", "background_dark"):
            value = ui.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{12}", value) and value not in have:
                ui[key] = DEFAULTS["ui"][key]

    def _members(self, zf, prefix):
        for info in zf.infolist():
            if info.filename.startswith(prefix) and not info.is_dir():
                yield info, info.filename[len(prefix):]

    @staticmethod
    def _write(zf, info, target, mode=0o600):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target + ".tmp", "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(target + ".tmp", mode)
        os.replace(target + ".tmp", target)

    def _restore_files(self, zf):
        for info, rel in self._members(zf, "tokens/"):
            if rel == "github-token":
                self._write(zf, info, self.update_token)
            elif rel.startswith("store/") and CAT_RE.match(rel[len("store/"):]):
                os.makedirs(self.store_tokens, mode=0o700, exist_ok=True)
                self._write(zf, info, os.path.join(self.store_tokens, rel[len("store/"):]))
        # Данные модулей заменяем целиком по модулю: старые файлы не смешиваются с новыми.
        by_module = {}
        for info, rel in self._members(zf, "module-data/"):
            mid, _, rest = rel.partition("/")
            if MODULE_RE.match(mid) and rest:
                by_module.setdefault(mid, []).append((info, rest))
        for mid, files in by_module.items():
            target = os.path.join(self.data_dir, mid)
            tmp = target + ".restore"
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, mode=0o700)
            for info, rest in files:
                self._write(zf, info, os.path.join(tmp, *rest.split("/")))
            shutil.rmtree(target, ignore_errors=True)
            os.replace(tmp, target)

    def _restore_backgrounds(self, zf):
        os.makedirs(self.backgrounds_dir, exist_ok=True)
        for info, rel in self._members(zf, "backgrounds/"):
            if BG_RE.match(rel):
                self._write(zf, info, os.path.join(self.backgrounds_dir, rel), mode=0o644)

    def _restore_modules(self, zf):
        entries = self._json(zf, "modules/installed.json")
        if not isinstance(entries, dict):
            raise BackupError("Файл копии повреждён")
        installed = self._installed()
        stamp = datetime.datetime.fromtimestamp(self.clock()).strftime("%Y%m%d%H%M%S")
        for mid, entry in entries.items():
            if not MODULE_RE.match(mid) or not isinstance(entry, dict):
                continue
            files = [(info, rel) for info, rel in self._members(zf, f"modules/{mid}/")]
            if not files:
                continue
            base = os.path.join(self.modules_dir, mid)
            name = f"{re.sub(r'[^0-9A-Za-z.-]', '', str(entry.get('version', '')))[:20]}-restore-{stamp}"
            target = os.path.join(base, name)
            shutil.rmtree(target, ignore_errors=True)
            for info, rel in files:
                self._write(zf, info, os.path.join(target, *rel.split("/")), mode=0o644)
            module = read_module(mid, target, "installed")
            if module.error:
                shutil.rmtree(target, ignore_errors=True)
                log.warning("модуль %s из копии не прошёл проверку: %s", mid, module.error)
                continue
            current = os.path.join(base, "current")
            previous = os.path.join(base, "previous")
            old = os.readlink(current) if os.path.islink(current) else None
            os.symlink(name, current + ".new")
            os.replace(current + ".new", current)
            if old and old != name:
                if os.path.lexists(previous):
                    os.remove(previous)
                os.symlink(old, previous)
            # Как store._switch: остаются только current и previous.
            keep = {name, os.readlink(previous) if os.path.islink(previous) else None, "current", "previous"}
            for entry_name in os.listdir(base):
                if entry_name not in keep and not entry_name.startswith("."):
                    shutil.rmtree(os.path.join(base, entry_name), ignore_errors=True)
            installed[mid] = {k: v for k, v in entry.items() if isinstance(k, str)}
        os.makedirs(self.modules_dir, exist_ok=True)
        tmp = os.path.join(self.modules_dir, "installed.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(installed, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(self.modules_dir, "installed.json"))
