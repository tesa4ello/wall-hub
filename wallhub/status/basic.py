"""Базовые провайдеры: Wi‑Fi, нагрузка, память, температура, трафик."""

import glob
import subprocess
import time

from . import provider

# Интерфейсы, которые не считаются в трафике.
SKIP_IFACES = ("lo", "docker", "veth", "br-", "virbr")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def human_rate(bytes_per_sec):
    value = bytes_per_sec * 8
    for unit in ("бит/с", "Кбит/с", "Мбит/с", "Гбит/с"):
        if value < 1000 or unit == "Гбит/с":
            return f"{value:.0f} {unit}" if value >= 10 or unit == "бит/с" else f"{value:.1f} {unit}"
        value /= 1000


_wifi_cache = {"at": 0.0, "ssid": None}


@provider("wifi", "Wi‑Fi")
def wifi():
    try:
        lines = _read("/proc/net/wireless").splitlines()[2:]
    except OSError:
        return None
    if not lines:
        return None
    iface, rest = lines[0].split(":", 1)
    quality = float(rest.split()[1].rstrip("."))
    # Качество в /proc/net/wireless обычно из 70.
    percent = max(0, min(100, round(quality / 70 * 100)))
    now = time.monotonic()
    if now - _wifi_cache["at"] > 30:
        _wifi_cache["at"] = now
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,CONNECTION", "device"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            _wifi_cache["ssid"] = next(
                (line.split(":", 1)[1] for line in out.splitlines() if line.startswith(iface.strip() + ":")),
                None,
            )
        except (OSError, subprocess.TimeoutExpired):
            _wifi_cache["ssid"] = None
    ssid = _wifi_cache["ssid"] or iface.strip()
    level = "ok" if percent >= 50 else "warn" if percent >= 25 else "crit"
    return {"icon": "wifi", "text": f"{percent}%", "title": ssid, "level": level}


_cpu_prev = None


@provider("load", "Нагрузка CPU")
def load():
    global _cpu_prev
    fields = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
    idle = fields[3] + fields[4]
    total = sum(fields)
    percent = 0
    if _cpu_prev:
        d_total = total - _cpu_prev[0]
        d_idle = idle - _cpu_prev[1]
        percent = round(100 * (1 - d_idle / d_total)) if d_total else 0
    _cpu_prev = (total, idle)
    la = _read("/proc/loadavg").split()[:3]
    level = "ok" if percent < 70 else "warn" if percent < 90 else "crit"
    return {"icon": "cpu", "text": f"{percent}%", "title": "Load average: " + " ".join(la), "level": level}


@provider("memory", "Память")
def memory():
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.split()[0])
    total = info["MemTotal"]
    used = total - info.get("MemAvailable", info["MemFree"])
    percent = round(100 * used / total)
    level = "ok" if percent < 80 else "warn" if percent < 92 else "crit"
    return {
        "icon": "mem",
        "text": f"{percent}%",
        "title": f"{used / 1048576:.1f} из {total / 1048576:.1f} ГБ",
        "level": level,
    }


@provider("temp", "Температура SoC")
def temp():
    values = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            values.append(int(_read(path)) / 1000)
        except (OSError, ValueError):
            pass
    if not values:
        return None
    t = max(values)
    level = "ok" if t < 70 else "warn" if t < 80 else "crit"
    return {"icon": "temp", "text": f"{t:.0f}°", "title": "Температура процессора", "level": level}


_net_prev = None


@provider("traffic", "Трафик")
def traffic():
    global _net_prev
    rx = tx = 0
    for line in _read("/proc/net/dev").splitlines()[2:]:
        iface, data = line.split(":", 1)
        if iface.strip().startswith(SKIP_IFACES):
            continue
        parts = data.split()
        rx += int(parts[0])
        tx += int(parts[8])
    now = time.monotonic()
    prev, _net_prev = _net_prev, (now, rx, tx)
    if not prev or now - prev[0] <= 0:
        return {"icon": "traffic", "text": "↓0 ↑0", "title": "Трафик"}
    dt = now - prev[0]
    down = max(0, rx - prev[1]) / dt
    up = max(0, tx - prev[2]) / dt
    return {
        "icon": "traffic",
        "text": f"↓{human_rate(down)} ↑{human_rate(up)}",
        "title": f"Всего: ↓{rx / 1048576:.0f} МБ ↑{tx / 1048576:.0f} МБ",
    }
