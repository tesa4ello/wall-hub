"""Экран панели: разрешение, масштаб интерфейса и сенсор.

Интерфейс рассчитан на 1024×600 (сетка 16×10). На другом экране Chromium
запускается с --force-device-scale-factor, чтобы виджеты оставались того же
размера относительно экрана: 1920×1080 при масштабе 1.75 — это ~1097×617
точек интерфейса. Масштаб «авто» выбирается по разрешению, его можно задать
и вручную (ui.scale).

Разрешение берём из того, что сообщил киоск (screen × devicePixelRatio), а до
его первого отчёта — из DRM (/sys/class/drm, видно и без X). Сенсор ищем по
udev (ID_INPUT_TOUCHSCREEN), так как Chromium с --touch-events=enabled
сообщает о касаниях и без сенсора.
"""

import glob
import math
import os
import re
import threading

BASE = (1024, 600)
SCALES = ("auto", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.5", "3")
MODE_RE = re.compile(r"^(\d{3,5})x(\d{3,5})")


def auto_scale(width, height):
    """Наибольший шаг 0.25, при котором в экран помещается 1024×600."""
    s = min(width / BASE[0], height / BASE[1])
    return min(3.0, max(0.75, math.floor(s * 4 + 1e-9) / 4))


def drm_outputs(root="/sys/class/drm"):
    """Подключённые выходы: [{connector, width, height}] по предпочтительному режиму."""
    result = []
    for path in sorted(glob.glob(os.path.join(root, "card*-*"))):
        try:
            with open(os.path.join(path, "status"), encoding="ascii") as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(path, "modes"), encoding="ascii") as f:
                first = f.readline().strip()
        except OSError:
            continue
        m = MODE_RE.match(first)
        if m:
            connector = os.path.basename(path).split("-", 1)[1]
            result.append({"connector": connector, "width": int(m.group(1)), "height": int(m.group(2))})
    return result


def touchscreens(devices="/proc/bus/input/devices", udev="/run/udev/data"):
    """Имена устройств ввода, которые udev считает сенсорными экранами."""
    try:
        with open(devices, encoding="utf-8", errors="replace") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return []
    names = []
    for block in blocks:
        name = re.search(r'^N: Name="(.*)"$', block, re.M)
        event = re.search(r"^H: Handlers=.*\bevent(\d+)\b", block, re.M)
        if not name or not event:
            continue
        # /dev/input/eventN — символьное устройство 13:(64+N).
        try:
            with open(os.path.join(udev, f"c13:{64 + int(event.group(1))}"), encoding="utf-8", errors="replace") as f:
                if "E:ID_INPUT_TOUCHSCREEN=1" in f.read().split("\n"):
                    names.append(name.group(1))
        except OSError:
            continue
    return names


class Display:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.lock = threading.Lock()
        self.report = None  # последний отчёт киоска

    def set_report(self, body):
        """Отчёт киоска: размер экрана в точках CSS, devicePixelRatio, окно."""
        try:
            r = {k: float(body[k]) for k in ("screen_w", "screen_h", "dpr", "view_w", "view_h")}
        except (KeyError, TypeError, ValueError):
            return
        if not all(0 < v < 20000 for v in r.values()):
            return
        with self.lock:
            self.report = r

    def _outputs(self):
        if self.dry_run:
            return [{"connector": "HDMI-A-1", "width": 1024, "height": 600}]
        return drm_outputs()

    def _touch(self):
        if self.dry_run:
            return ["Goodix Capacitive TouchScreen"]
        return touchscreens()

    def info(self, scale_setting="auto"):
        outputs = self._outputs()
        with self.lock:
            report = self.report
        if report:
            width = round(report["screen_w"] * report["dpr"])
            height = round(report["screen_h"] * report["dpr"])
            source = "kiosk"
        elif outputs:
            width, height, source = outputs[0]["width"], outputs[0]["height"], "drm"
        else:
            width, height, source = BASE[0], BASE[1], "default"
        auto = auto_scale(width, height)
        scale = auto if scale_setting in (None, "auto") else float(scale_setting)
        devices = self._touch()
        return {
            "width": width,
            "height": height,
            "source": source,
            "connector": outputs[0]["connector"] if outputs else "",
            "scale": scale,
            "scale_auto": auto,
            # Размер интерфейса в точках CSS: под него рисуется холст редактора столов.
            "view": {"width": round(width / scale), "height": round(height / scale)},
            "touch": bool(devices),
            "touch_devices": devices,
        }
