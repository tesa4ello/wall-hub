#!/usr/bin/env python3
"""Обновлятор Wall-HUB. Запускается от root через wallhub-update@<режим>.service.

Режимы:
  check    только узнать, есть ли новая версия;
  install  проверить и, если есть новая версия, поставить её;
  auto     то же, что install, но только при включённой автоустановке
           (иначе как check) — так работает ночной таймер;
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

DEFAULT_SETTINGS = {"repo": "tesa4ello/wall-hub", "channel": "main", "branch": "main", "auto": True}


class UpdateError(Exception):
    pass


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


def settings():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return dict(DEFAULT_SETTINGS, **data.get("update", {}))


def write_status(**fields):
    """Обновляет update.json (атомарно, файл читает сервис wallhub)."""
    try:
        with open(STATUS, encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = {}
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


def github(url, raw=False):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "wallhub-updater"}
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
            raise UpdateError(f"GitHub ответил {e.code}: проверьте токен и доступ к репозиторию")
        raise UpdateError(f"GitHub ответил {e.code}")
    except urllib.error.URLError as e:
        raise UpdateError(f"Нет связи с GitHub: {e.reason}")
    if raw:
        return response
    with response:
        return json.load(response)


def latest(cfg):
    """Последняя версия в выбранном канале: {commit, ref, date, title}."""
    api = f"https://api.github.com/repos/{cfg['repo']}"
    if cfg["channel"] == "releases":
        try:
            release = github(f"{api}/releases/latest")
            ref = release["tag_name"]
        except UpdateError:
            # Релизов может не быть, тогда берём самый свежий тег.
            tags = github(f"{api}/tags?per_page=1")
            if not tags:
                raise UpdateError("В репозитории ещё нет релизов")
            ref = tags[0]["name"]
    else:
        ref = cfg["branch"]
    commit = github(f"{api}/commits/{ref}")
    return {
        "commit": commit["sha"],
        "ref": ref,
        "date": commit["commit"]["committer"]["date"],
        "title": commit["commit"]["message"].splitlines()[0][:200],
    }


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
    write_status(state="installing", message=f"Скачиваю {version['ref']} {version['commit'][:7]}…", started_at=now())
    log(f"install {version['ref']} {version['commit']}")
    release = download(cfg, version)

    write_status(message="Устанавливаю…")
    with open(LOG, "a", encoding="utf-8") as out:
        result = subprocess.run(
            ["bash", os.path.join(release, "install.sh"), "--updater"],
            stdout=out, stderr=subprocess.STDOUT, timeout=25 * 60,
        )
    if result.returncode == 0 and healthy(version["commit"]):
        log("ok")
        write_status(state="idle", result="ok", message="Обновлено", finished_at=now(),
                     current=read_version(CURRENT), available=False)
        return

    reason = f"install.sh завершился с кодом {result.returncode}" if result.returncode else "сервис не запустился"
    log(f"failed: {reason}, rollback to {previous}")
    if previous:
        switch_to(previous)
    write_status(state="idle", result="failed", finished_at=now(), current=read_version(CURRENT),
                 message=f"Не удалось обновиться ({reason}), вернул прошлую версию. Подробности в update.log")


def rollback():
    if not os.path.islink(PREVIOUS):
        raise UpdateError("Прошлой версии нет")
    previous = os.readlink(PREVIOUS)
    current = os.readlink(CURRENT)
    switch_to(previous)
    os.remove(PREVIOUS)
    os.symlink(current, PREVIOUS)
    log(f"rollback to {previous}")
    write_status(state="idle", result="ok", message="Возвращена прошлая версия", finished_at=now(),
                 current=read_version(CURRENT))


def check(cfg):
    write_status(state="checking", message="Проверяю обновления…")
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
        version, available = check(cfg)
        if available and (mode == "install" or (mode == "auto" and cfg.get("auto"))):
            install(cfg, version)
    except UpdateError as e:
        log(f"error: {e}")
        write_status(state="idle", result="failed", message=str(e), finished_at=now())
        return 1
    except Exception as e:
        log(f"error: {e!r}")
        write_status(state="idle", result="failed", message=f"Ошибка обновления: {e}", finished_at=now())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
