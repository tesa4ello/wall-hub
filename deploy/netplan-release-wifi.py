#!/usr/bin/env python3
"""Отдаёт Wi‑Fi адаптер из netplan (systemd-networkd) NetworkManager-у.

Armbian настраивает Wi‑Fi при первом входе через netplan с renderer networkd,
и тогда NetworkManager считает wlan0 «unmanaged»: панель не может ни искать
сети, ни подключаться. Скрипт убирает из /etc/netplan файлы, где описан только
Wi‑Fi (ethernet не трогает), в /etc/netplan/wallhub-disabled/. Сети из них не
переносятся: сеть выбирают на самой панели при первом запуске.

Повторный запуск ничего не делает, если убирать уже нечего.
"""

import glob
import os
import shutil
import subprocess
import sys

import yaml

NETPLAN_DIR = "/etc/netplan"
BACKUP_DIR = os.path.join(NETPLAN_DIR, "wallhub-disabled")
# Ключи верхнего уровня, при которых файл целиком про Wi‑Fi.
WIFI_ONLY_KEYS = {"version", "renderer", "wifis"}


def log(message):
    print(f"  {message}")


def main():
    moved = 0
    for path in sorted(glob.glob(os.path.join(NETPLAN_DIR, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        network = data.get("network") or {}
        if not network.get("wifis"):
            continue
        if not set(network) <= WIFI_ONLY_KEYS or set(data) != {"network"}:
            log(f"{path}: кроме Wi‑Fi там есть другие настройки, оставляю как есть")
            continue
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
