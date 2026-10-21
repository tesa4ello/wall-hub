#!/usr/bin/env python3
"""Синхронизирует гашение экрана (DPMS) X-сервера с настройкой blank_after_min.

Запускается из kiosk.sh в сессии киоска. Таймер простоя ведёт сам X-сервер:
он гасит подсветку и включает её по первому касанию. Интерфейс в это время
показывает чёрный слой, который поглощает это касание.
"""

import json
import subprocess
import time
import urllib.request

URL = "http://localhost:8080/api/config"


def apply(minutes):
    if minutes > 0:
        seconds = str(minutes * 60)
        subprocess.run(["xset", "+dpms"], check=False)
        # standby и suspend не используем: на многих панелях они не работают.
        subprocess.run(["xset", "dpms", "0", "0", seconds], check=False)
    else:
        subprocess.run(["xset", "-dpms"], check=False)


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
        time.sleep(15)


if __name__ == "__main__":
    main()
