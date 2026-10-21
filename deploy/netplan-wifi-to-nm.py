#!/usr/bin/env python3
"""Передаёт Wi‑Fi из netplan (systemd-networkd) под управление NetworkManager.

Armbian настраивает Wi‑Fi при первом входе через netplan с renderer networkd,
и тогда NetworkManager считает wlan0 «unmanaged»: панель не может ни искать
сети, ни подключаться. Скрипт:

  * находит в /etc/netplan файлы, где описаны только wifis (ethernet не трогает);
  * переносит их сети в профили NetworkManager (keyfile, права 0600);
  * убирает эти файлы в /etc/netplan/wallhub-disabled/ и применяет netplan.

Повторный запуск ничего не делает, если переносить уже нечего.
"""

import glob
import os
import shutil
import subprocess
import sys
import uuid

import yaml

NETPLAN_DIR = "/etc/netplan"
BACKUP_DIR = os.path.join(NETPLAN_DIR, "wallhub-disabled")
NM_DIR = "/etc/NetworkManager/system-connections"
# Ключи верхнего уровня, при которых файл целиком про Wi‑Fi.
WIFI_ONLY_KEYS = {"version", "renderer", "wifis"}


def log(message):
    print(f"  {message}")


def keyfile(iface, ssid, ap):
    """Профиль NetworkManager в формате keyfile."""
    auth = ap.get("auth") or {}
    password = ap.get("password") or auth.get("password")
    key_mgmt = auth.get("key-management", "psk" if password else "none")
    if key_mgmt not in ("psk", "none", "sae"):
        raise ValueError(f"тип защиты {key_mgmt} не поддерживается")
    lines = [
        "[connection]",
        f"id={ssid}",
        f"uuid={uuid.uuid4()}",
        "type=wifi",
        f"interface-name={iface}",
        "autoconnect=true",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid}",
    ]
    if ap.get("hidden"):
        lines.append("hidden=true")
    if password:
        lines += [
            "",
            "[wifi-security]",
            f"key-mgmt={'sae' if key_mgmt == 'sae' else 'wpa-psk'}",
            f"psk={str(password).strip()}",
        ]
    lines += ["", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def main():
    moved = 0
    for path in sorted(glob.glob(os.path.join(NETPLAN_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        network = data.get("network") or {}
        wifis = network.get("wifis")
        if not wifis:
            continue
        if not set(network) <= WIFI_ONLY_KEYS or set(data) != {"network"}:
            log(f"{path}: кроме Wi‑Fi там есть другие настройки, оставляю как есть")
            continue

        profiles = []
        try:
            for iface, cfg in wifis.items():
                for ssid, ap in ((cfg or {}).get("access-points") or {}).items():
                    profiles.append((iface, str(ssid), keyfile(iface, str(ssid), ap or {})))
        except ValueError as e:
            log(f"{path}: {e}, оставляю как есть")
            continue

        os.makedirs(NM_DIR, exist_ok=True)
        for iface, ssid, content in profiles:
            target = os.path.join(NM_DIR, f"wallhub-{iface}-{ssid}.nmconnection".replace("/", "_"))
            if not os.path.exists(target):
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                log(f"сеть «{ssid}» перенесена в NetworkManager")

        os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
        shutil.move(path, os.path.join(BACKUP_DIR, os.path.basename(path)))
        log(f"{path} перенесён в {BACKUP_DIR}/")
        moved += 1

    if not moved:
        return 0

    # netplan перестаёт держать wlan0 и сообщает NetworkManager, что
    # интерфейс больше не «unmanaged»; перезапуск NM перечитывает это.
    subprocess.run(["netplan", "apply"], check=True)
    # wpa_supplicant, запущенный netplan для networkd, держит адаптер.
    subprocess.run(["systemctl", "stop", "netplan-wpa-*.service"], check=False)
    subprocess.run(["systemctl", "restart", "NetworkManager"], check=True)
    subprocess.run(["nmcli", "connection", "reload"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
