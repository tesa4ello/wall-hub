#!/usr/bin/env python3
"""Передаёт проводные интерфейсы из netplan (systemd-networkd) NetworkManager-у.

Запускается от root юнитом wallhub-net-ethernet.service по кнопке
«Управлять из панели» в админке. Через кабель может работать и сама админка,
и SSH, поэтому всё делается с откатом:

1. Из файлов /etc/netplan, где описан только Ethernet, берём настройки
   (DHCP или адреса, шлюз, DNS) для каждого проводного интерфейса.
2. Создаём такие же профили в NetworkManager.
3. Убираем файлы в /etc/netplan/wallhub-disabled/, применяем netplan,
   перезапускаем NetworkManager и поднимаем профили.
4. Ждём адрес и ответ шлюза. Не дождались — возвращаем файлы netplan,
   удаляем профили и применяем всё как было.

Ход и итог пишутся в /var/lib/wallhub/net-migrate.json (его читает админка).
"""

import datetime
import fnmatch
import glob
import json
import os
import pwd
import shutil
import subprocess
import sys
import time

import yaml

NETPLAN_DIR = "/etc/netplan"
BACKUP_DIR = os.path.join(NETPLAN_DIR, "wallhub-disabled")
STATUS = "/var/lib/wallhub/net-migrate.json"
ETHERNET_ONLY_KEYS = {"version", "renderer", "ethernets"}
VERIFY_TIMEOUT = 45


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def status(state, message="", message_args=None, **extra):
    """message — русский текст (ключ перевода), message_args — подстановки."""
    data = {"state": state, "message": message, "message_args": message_args or {}, "at": now(), **extra}
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    try:
        user = pwd.getpwnam("wallhub")
        os.chown(tmp, user.pw_uid, user.pw_gid)
    except KeyError:
        pass
    os.replace(tmp, STATUS)
    print(f"{state}: {message} {message_args or ''}")


def run(args, check=True, timeout=90):
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout)


def ethernet_devices():
    out = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"]).stdout
    return [line.split(":")[0] for line in out.splitlines() if line.split(":")[1:2] == ["ethernet"]]


def settings_for(device, ethernets):
    """Настройка устройства из секции ethernets netplan или None."""
    for name, conf in ethernets.items():
        conf = conf or {}
        match = conf.get("match") or {}
        pattern = match.get("name")
        if name == device or (pattern and fnmatch.fnmatch(device, pattern)):
            ns = conf.get("nameservers") or {}
            gateway = conf.get("gateway4") or ""
            for route in conf.get("routes") or []:
                if route.get("to") in ("default", "0.0.0.0/0") and route.get("via"):
                    gateway = route["via"]
            addresses = [a for a in conf.get("addresses") or [] if ":" not in str(a)]
            return {
                "method": "auto" if conf.get("dhcp4") or not addresses else "manual",
                "address": str(addresses[0]) if addresses else "",
                "gateway": str(gateway),
                "dns": [d for d in ns.get("addresses") or [] if ":" not in str(d)],
            }
    return None


def connected(device, gateway):
    out = run(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY", "device", "show", device], check=False).stdout
    addresses = [line for line in out.splitlines() if line.startswith("IP4.ADDRESS") and line.split(":", 1)[1]]
    gw = gateway
    for line in out.splitlines():
        if line.startswith("IP4.GATEWAY:") and line.split(":", 1)[1] not in ("", "--"):
            gw = line.split(":", 1)[1]
    if not addresses:
        return False
    return not gw or run(["ping", "-c", "1", "-W", "2", gw], check=False).returncode == 0


def main():
    status("running", "Переношу настройки Ethernet в NetworkManager")
    files, ethernets = [], {}
    for path in sorted(glob.glob(os.path.join(NETPLAN_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        network = data.get("network") or {}
        if not network.get("ethernets"):
            continue
        if not set(network) <= ETHERNET_ONLY_KEYS or set(data) != {"network"}:
            status("failed", "В {path} кроме Ethernet есть другие настройки, переносить автоматически небезопасно", {"path": path})
            return 1
        files.append(path)
        ethernets.update(network["ethernets"])

    devices = ethernet_devices()
    if not files:
        # netplan Ethernet не держит: достаточно отдать устройства NetworkManager.
        for device in devices:
            run(["nmcli", "device", "set", device, "managed", "yes"], check=False)
        status("done", "Ethernet уже под NetworkManager")
        return 0

    plan = {d: settings_for(d, ethernets) or {"method": "auto", "address": "", "gateway": "", "dns": []} for d in devices}
    created, moved = [], []
    try:
        for device, s in plan.items():
            name = f"Wired {device}"
            run(["nmcli", "connection", "delete", "id", name], check=False)
            args = ["nmcli", "connection", "add", "type", "ethernet", "ifname", device, "con-name", name,
                    "connection.autoconnect", "yes", "ipv4.method", s["method"]]
            if s["method"] == "manual":
                args += ["ipv4.addresses", s["address"]]
                if s["gateway"]:
                    args += ["ipv4.gateway", s["gateway"]]
            if s["dns"]:
                args += ["ipv4.dns", " ".join(s["dns"])]
            run(args)
            created.append(name)

        os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
        for path in files:
            target = os.path.join(BACKUP_DIR, os.path.basename(path))
            shutil.move(path, target)
            moved.append((target, path))
        run(["netplan", "apply"], check=False)
        run(["systemctl", "restart", "NetworkManager"])
        time.sleep(3)
        for device in plan:
            run(["nmcli", "device", "set", device, "managed", "yes"], check=False)
            run(["nmcli", "connection", "up", f"Wired {device}"], check=False)

        deadline = time.monotonic() + VERIFY_TIMEOUT
        pending = dict(plan)
        while pending and time.monotonic() < deadline:
            for device in list(pending):
                if connected(device, pending[device]["gateway"]):
                    del pending[device]
            time.sleep(2)
        # Кабель вообще не вставлен — связи и не было, это не повод откатывать.
        unplugged = {d for d in pending if open(f"/sys/class/net/{d}/carrier").read().strip() != "1"} if pending else set()
        failed = set(pending) - unplugged
        if not failed:
            status("done", "Ethernet теперь настраивается из панели", devices=list(plan))
            return 0
    except (subprocess.SubprocessError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)

    # Откат: возвращаем netplan как было.
    for name in created:
        run(["nmcli", "connection", "delete", "id", name], check=False)
    for target, path in moved:
        shutil.move(target, path)
    run(["netplan", "apply"], check=False)
    run(["systemctl", "restart", "NetworkManager"], check=False)
    status("rolled_back", "Связь через Ethernet не поднялась, вернул прежние настройки")
    return 1


if __name__ == "__main__":
    sys.exit(main())
