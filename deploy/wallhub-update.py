#!/usr/bin/env python3
"""Обновлятор Wall-HUB. Запускается от root через wallhub-update@<режим>.service.

Режимы:
  check    только узнать, есть ли новая версия;
  install  проверить и, если есть новая версия, поставить её;
  auto     запускается таймером каждые 15 минут: если по расписанию
           (update.schedule) пора, проверяет и при включённой автоустановке
           ставит новую версию;
  rollback вернуть прошлую версию (/opt/wallhub/previous).

Как ставится версия: архив коммита скачивается из GitHub в
/opt/wallhub/releases/<коммит>, затем запускается его install.sh --updater,
который переключает симлинк /opt/wallhub/current и перезапускает сервис.
Если сервис после этого не отвечает новой версией за HEALTH_TIMEOUT секунд,
симлинк возвращается на прошлую версию.

Ход работы пишется в /var/lib/wallhub/update.json (его читает админка)
и /var/lib/wallhub/update.log.
"""

import datetime
import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

APP_DIR = "/opt/wallhub"
RELEASES_DIR = os.path.join(APP_DIR, "releases")
CURRENT = os.path.join(APP_DIR, "current")
PREVIOUS = os.path.join(APP_DIR, "previous")
STATE_DIR = "/var/lib/wallhub"
CONFIG = os.path.join(STATE_DIR, "config.json")
STATUS = os.path.join(STATE_DIR, "update.json")
LOG = os.path.join(STATE_DIR, "update.log")
TOKEN = os.path.join(STATE_DIR, "github-token")
SERVICE_USER = "wallhub"
HEALTH_URL = "http://127.0.0.1:8080/api/info"
HEALTH_TIMEOUT = 60
# После успешного обновления панель перезагружается целиком: так новая версия
# стартует с чистого листа (киоск, драйверы, юниты). Пауза — чтобы экран успел
# показать «Перезагружаю панель…».
REBOOT_DELAY = 5

# Должно совпадать с DEFAULTS["update"] в wallhub/config.py.
DEFAULT_SETTINGS = {
    "repo": "tesa4ello/wall-hub",
    "channel": "stable",
    "branches": {"stable": "stable", "dev": "dev"},
    "auto": True,
    "schedule": "daily",
    "time": "04:00",
}
# Интервалы расписания (для daily — раз в сутки в заданное время).
SCHEDULE_INTERVALS = {"hourly": datetime.timedelta(hours=1), "6h": datetime.timedelta(hours=6)}
# Таймер срабатывает раз в 15 минут, допуск — чтобы не пропустить слот.
SLACK = datetime.timedelta(minutes=5)


class UpdateError(Exception):
    """Текст ошибки переводит интерфейс: message — исходная строка (по-русски,
    как ключ в web/i18n/<язык>.json), params — подстановки."""

    def __init__(self, message, http_status=None, **params):
        super().__init__(message.format(**params) if params else message)
        self.message = message
        self.params = params
        self.http_status = http_status


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def read_version(release_dir):
    info = {}
    try:
        with open(os.path.join(release_dir, "VERSION"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key:
                    info[key] = value
    except OSError:
        pass
    return info


# Стабильная ветка называлась main до выпуска 1.0.0.
BRANCH_ALIASES = {"stable": "main", "main": "stable"}


def settings():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cfg = dict(DEFAULT_SETTINGS, **data.get("update", {}))
    cfg["branches"] = dict(DEFAULT_SETTINGS["branches"], **cfg.get("branches", {}))
    # До 1.0.0 стабильной веткой была main, теперь stable.
    if cfg["branches"]["stable"] == "main":
        cfg["branches"]["stable"] = "stable"
    if cfg["channel"] not in cfg["branches"]:
        cfg["channel"] = "stable"
    return cfg


def read_status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_status(**fields):
    """Обновляет update.json (атомарно, файл читает сервис wallhub)."""
    status = read_status()
    # message переводится в интерфейсе, подстановки для него — в message_args.
    if "message" in fields:
        fields.setdefault("message_args", {})
    status.update(fields)
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    user = pwd.getpwnam(SERVICE_USER)
    os.chown(tmp, user.pw_uid, user.pw_gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, STATUS)


def log(message):
    line = f"{now()} {message}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- GitHub --------------------------------------------------------------------


def github(url, raw=False, accept="application/vnd.github+json"):
    headers = {"Accept": accept, "User-Agent": "wallhub-updater"}
    try:
        with open(TOKEN, encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except OSError:
        pass
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise UpdateError("GitHub ответил {code}: проверьте токен и доступ к репозиторию", http_status=e.code, code=e.code)
        raise UpdateError("GitHub ответил {code}", http_status=e.code, code=e.code)
    except urllib.error.URLError as e:
        raise UpdateError("Нет связи с GitHub: {reason}", reason=str(e.reason))
    if raw:
        return response
    with response:
        return json.load(response)


def latest(cfg):
    """Последняя версия в выбранном канале: {commit, ref, date, title, version}.

    Канал — это ветка из update.branches; особое значение "tags" означает
    последний релиз (или тег) вместо ветки.
    """
    api = f"https://api.github.com/repos/{cfg['repo']}"
    ref = cfg["branches"][cfg["channel"]]
    if ref == "tags":
        try:
            release = github(f"{api}/releases/latest")
            ref = release["tag_name"]
        except UpdateError:
            # Релизов может не быть, тогда берём самый свежий тег.
            tags = github(f"{api}/tags?per_page=1")
            if not tags:
                raise UpdateError("В репозитории ещё нет релизов")
            ref = tags[0]["name"]
    try:
        commit = github(f"{api}/commits/{ref}")
    except UpdateError as e:
        if e.http_status not in (404, 422):
            raise
        # stable и main — одна ветка до и после переименования: пробуем другое имя.
        if ref not in BRANCH_ALIASES:
            raise UpdateError("В репозитории нет ветки {ref} (или нет доступа к нему)", ref=ref)
        try:
            commit = github(f"{api}/commits/{BRANCH_ALIASES[ref]}")
        except UpdateError as e2:
            if e2.http_status in (404, 422):
                raise UpdateError("В репозитории нет ветки {ref} (или нет доступа к нему)", ref=ref)
            raise
        ref = BRANCH_ALIASES[ref]
    return {
        "commit": commit["sha"],
        "ref": ref,
        "date": commit["commit"]["committer"]["date"],
        "title": commit["commit"]["message"].splitlines()[0][:200],
        "version": remote_version(api, commit["sha"]),
    }


def remote_version(api, sha):
    """Номер версии (__version__) в указанном коммите, если его удалось узнать."""
    try:
        with github(f"{api}/contents/wallhub/__init__.py?ref={sha}", raw=True,
                    accept="application/vnd.github.raw") as response:
            text = response.read(65536).decode("utf-8", "replace")
    except UpdateError:
        return ""
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)', text, re.M)
    return match.group(1) if match else ""


def due(cfg, last, now):
    """Пора ли автопроверке по расписанию. last — время прошлой автопроверки."""
    schedule = cfg.get("schedule", "daily")
    if schedule in SCHEDULE_INTERVALS:
        return last is None or now - last >= SCHEDULE_INTERVALS[schedule] - SLACK
    if schedule == "daily":
        hour, minute = map(int, cfg.get("time", "04:00").split(":"))
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot:
            slot -= datetime.timedelta(days=1)
        return last is None or last < slot
    return False


def download(cfg, version):
    """Скачивает и распаковывает архив коммита в releases/<коммит>."""
    release = os.path.join(RELEASES_DIR, version["commit"][:12])
    os.makedirs(RELEASES_DIR, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".download-", dir=RELEASES_DIR)
    try:
        url = f"https://api.github.com/repos/{cfg['repo']}/tarball/{version['commit']}"
        archive = os.path.join(work, "src.tar.gz")
        with github(url, raw=True) as response, open(archive, "wb") as f:
            shutil.copyfileobj(response, f)
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        (top,) = [d for d in os.listdir(work) if os.path.isdir(os.path.join(work, d))]
        shutil.rmtree(release, ignore_errors=True)
        os.rename(os.path.join(work, top), release)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(release, "VERSION"), "w", encoding="utf-8") as f:
        f.write(f"commit={version['commit']}\nref={version['ref']}\ndate={version['date']}\n")
    return release


# --- установка и откат -------------------------------------------------------------


def healthy(commit):
    """Ждёт, пока сервис ответит нужной версией."""
    request = urllib.request.Request(HEALTH_URL, headers={"Host": "localhost:8080"})
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                if json.load(response).get("build", {}).get("commit") == commit:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(2)
    return False


def switch_to(target):
    """Атомарно направляет current на target (путь вида releases/<id>)."""
    tmp = CURRENT + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, CURRENT)
    subprocess.run(["systemctl", "restart", "wallhub.service"], check=False)


def install(cfg, version):
    previous = os.readlink(CURRENT) if os.path.islink(CURRENT) else None
    # phase: этап для экрана панели (download → install → verify, при сбое rollback).
    write_status(state="installing", job="install", phase="download", message="Скачиваю {ref} {commit}…",
                 message_args={"ref": version["ref"], "commit": version["commit"][:7]},
                 target=version.get("version") or "", result=None, started_at=now(), reboot=False)
    log(f"install {version['ref']} {version['commit']}")
    release = download(cfg, version)

    write_status(phase="install", message="Устанавливаю…")
    with open(LOG, "a", encoding="utf-8") as out:
        result = subprocess.run(
            ["bash", os.path.join(release, "install.sh"), "--updater"],
            stdout=out, stderr=subprocess.STDOUT, timeout=25 * 60,
        )
    if result.returncode == 0:
        write_status(phase="verify", message="Проверяю, что сервис запустился…")
    if result.returncode == 0 and healthy(version["commit"]):
        log("ok, reboot")
        write_status(phase="reboot", message="Перезагружаю панель…")
        time.sleep(REBOOT_DELAY)
        write_status(state="idle", phase=None, result="ok", message="Обновлено", finished_at=now(),
                     current=read_version(CURRENT), available=False, reboot=True)
        subprocess.run(["systemctl", "reboot"], check=False)
        return

    if result.returncode:
        message, args = "Не удалось обновиться (install.sh завершился с кодом {code}), вернул прошлую версию. Подробности в update.log", {"code": result.returncode}
    else:
        message, args = "Не удалось обновиться (сервис не запустился), вернул прошлую версию. Подробности в update.log", {}
    log(f"failed: {message.format(**args)}, rollback to {previous}")
    if previous:
        write_status(phase="rollback", message="Возвращаю прошлую версию…")
        switch_to(previous)
    write_status(state="idle", phase=None, result="failed", finished_at=now(), current=read_version(CURRENT),
                 message=message, message_args=args)


def rollback():
    if not os.path.islink(PREVIOUS):
        raise UpdateError("Прошлой версии нет")
    previous = os.readlink(PREVIOUS)
    current = os.readlink(CURRENT)
    write_status(state="installing", job="rollback", phase="rollback", message="Возвращаю прошлую версию…",
                 target="", result=None, started_at=now())
    switch_to(previous)
    os.remove(PREVIOUS)
    os.symlink(current, PREVIOUS)
    log(f"rollback to {previous}")
    write_status(state="idle", phase=None, result="ok", message="Возвращена прошлая версия", finished_at=now(),
                 current=read_version(CURRENT))


def check(cfg):
    write_status(state="checking", job="check", message="Проверяю обновления…")
    version = latest(cfg)
    current = read_version(CURRENT)
    available = version["commit"] != current.get("commit")
    write_status(state="idle", checked_at=now(), latest=version, current=current, available=available,
                 channel=cfg["channel"], message="Есть новая версия" if available else "Установлена последняя версия")
    return version, available


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode not in ("check", "install", "auto", "rollback"):
        print(__doc__)
        return 2
    os.makedirs(STATE_DIR, exist_ok=True)
    # Два обновления одновременно (таймер и кнопка) не нужны.
    lock = open(os.path.join(STATE_DIR, "update.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Обновление уже идёт")
        return 0
    cfg = settings()
    try:
        if mode == "rollback":
            rollback()
            return 0
        if mode == "auto":
            current = datetime.datetime.now().astimezone()
            try:
                last = datetime.datetime.fromisoformat(read_status()["auto_at"])
            except (KeyError, ValueError):
                last = None
            if not due(cfg, last, current):
                return 0
            write_status(auto_at=now())
        version, available = check(cfg)
        if available and (mode == "install" or (mode == "auto" and cfg.get("auto"))):
            install(cfg, version)
    except UpdateError as e:
        log(f"error: {e}")
        write_status(state="idle", phase=None, result="failed", message=e.message, message_args=e.params,
                     finished_at=now())
        return 1
    except Exception as e:
        log(f"error: {e!r}")
        write_status(state="idle", phase=None, result="failed", message="Ошибка обновления: {error}",
                     message_args={"error": str(e)}, finished_at=now())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
