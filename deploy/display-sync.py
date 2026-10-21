#!/usr/bin/env python3
"""Синхронизирует гашение экрана (DPMS) X-сервера с настройкой blank_after_min
и масштаб интерфейса Chromium с тем, что выбрало ядро.

Запускается из kiosk.sh в сессии киоска. Таймер простоя ведёт сам X-сервер:
он гасит подсветку и включает её по первому касанию. Интерфейс в это время
показывает чёрный слой, который поглощает это касание.
"""

import json
import os
import subprocess
import time
import urllib.request

URL = "http://localhost:8080/api/config"
DISPLAY_URL = "http://localhost:8080/api/display"
SCALE_FILE = os.path.expanduser("~/.wallhub-scale")


def apply(minutes):
    if minutes > 0:
        seconds = str(minutes * 60)
        subprocess.run(["xset", "+dpms"], check=False)
        # standby и suspend не используем: на многих панелях они не работают.
        subprocess.run(["xset", "dpms", "0", "0", seconds], check=False)
    else:
        subprocess.run(["xset", "-dpms"], check=False)


def sync_scale():
    """Масштаб интерфейса поменялся (настройка или другой экран): перезапускаем
    Chromium, kiosk.sh запустит его с новым --force-device-scale-factor."""
    try:
        with urllib.request.urlopen(DISPLAY_URL, timeout=5) as response:
            scale = str(json.load(response)["scale"])
        with open(SCALE_FILE, encoding="ascii") as f:
            running = f.read().strip()
    except (OSError, ValueError, KeyError):
        return
    if running and float(running) != float(scale):
        subprocess.run(["pkill", "-x", "chromium"], check=False)


def main():
    current = None
    while True:
        try:
            with urllib.request.urlopen(URL, timeout=5) as response:
                minutes = int(json.load(response)["ui"].get("blank_after_min", 0))
        except (OSError, ValueError, KeyError):
            minutes = current
        if minutes is not None and minutes != current:
            apply(minutes)
            current = minutes
        sync_scale()
        time.sleep(15)


if __name__ == "__main__":
    main()
