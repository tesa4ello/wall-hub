"""Системные действия: Wi‑Fi (NetworkManager), питание, часовой пояс.

Права на эти действия сервис получает через polkit-правила из deploy/.
В режиме dry_run команды не выполняются, а Wi‑Fi отдаёт тестовые данные:
так интерфейс можно разрабатывать на обычном компьютере.
"""

import logging
import subprocess
import threading
import time

from .i18n import tr

log = logging.getLogger(__name__)


class CommandError(Exception):
    """Ошибка системной команды с понятным пользователю текстом."""


def split_terse(line):
    """Разбирает строку `nmcli -t`: поля через ':', экранирование '\\'."""
    fields, current, escaped = [], [], False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


class System:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._fake_active = "Home"
        # «Дача» сохранена, но не видна: для списка известных сетей.
        self._fake_saved = {"Home", "Дача"}
        self._fake_radio = True
        self._lock = threading.Lock()

    def _run(self, args, timeout=30):
        log.info("run: %s", " ".join(a if "password" not in a else "***" for a in args))
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise CommandError(tr("Команда {name} не найдена", name=args[0]))
        except subprocess.TimeoutExpired:
            raise CommandError(tr("Команда не успела выполниться"))
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip()
            # nmcli пишет "Error: ...", оставляем суть.
            message = message.removeprefix("Error: ")
            raise CommandError(message or tr("Код ошибки {code}", code=proc.returncode))
        return proc.stdout

    # --- Wi‑Fi -----------------------------------------------------------

    def wifi_device(self):
        out = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        for line in out.splitlines():
            device, kind, state, connection = (split_terse(line) + [""] * 4)[:4]
            if kind == "wifi":
                return {"device": device, "state": state, "connection": connection}
        return None

    def wifi_state(self, rescan=False, brief=False):
        """Состояние Wi‑Fi: радио, подключение, видимые и сохранённые сети.

        brief — только радио и подключение, без списка сетей: список может
        запустить сканирование на несколько секунд, а шторке он не нужен.
        """
        if self.dry_run:
            state = self._fake_wifi_state()
            signal = next((n["signal"] for n in state["networks"] if n["active"]), None)
            state["signal"] = signal
            return dict(state, networks=[], saved=[]) if brief else state
        with self._lock:
            radio = self._run(["nmcli", "radio", "wifi"]).strip() == "enabled"
            device = self.wifi_device()
            saved = [] if brief else self._saved_wifi()
            networks = []
            if radio and device and not brief:
                out = self._run([
                    "nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                    "device", "wifi", "list", "--rescan", "yes" if rescan else "auto",
                ], timeout=40)
                seen = {}
                for line in out.splitlines():
                    in_use, ssid, signal, security = (split_terse(line) + [""] * 4)[:4]
                    if not ssid:
                        continue
                    item = {
                        "ssid": ssid,
                        "signal": int(signal or 0),
                        "secure": bool(security and security != "--"),
                        "security": security,
                        "active": in_use.strip() == "*",
                        "saved": ssid in saved,
                    }
                    # Одна сеть может вещать несколько точек: берём лучшую.
                    prev = seen.get(ssid)
                    if prev is None or item["active"] or (not prev["active"] and item["signal"] > prev["signal"]):
                        seen[ssid] = item
                networks = sorted(seen.values(), key=lambda n: (not n["active"], -n["signal"]))
            signal = None
            if brief and radio and device and device["state"] == "connected":
                # Уровень текущей сети из кэша NetworkManager, без сканирования.
                out = self._run(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "device", "wifi", "list", "--rescan", "no"])
                for line in out.splitlines():
                    use, sig = (split_terse(line) + ["", ""])[:2]
                    if use.strip() == "*" and sig.isdigit():
                        signal = int(sig)
                        break
            return {
                "available": device is not None,
                "radio": radio,
                "signal": signal if brief else next((n["signal"] for n in networks if n["active"]), None),
                "device": device["device"] if device else None,
                "connection": device["connection"] if device and device["state"] == "connected" else None,
                "networks": networks,
                "saved": sorted(saved),
            }

    def wifi_bssids(self):
        """Видимые точки Wi‑Fi для Яндекс Локатора: [{bssid, signal %}]."""
        if self.dry_run:
            return [{"bssid": "00:11:22:33:44:55", "signal": 70}]
        with self._lock:
            out = self._run(["nmcli", "-t", "-f", "BSSID,SIGNAL", "device", "wifi", "list", "--rescan", "auto"], timeout=40)
        points = []
        for line in out.splitlines():
            bssid, signal = (split_terse(line) + [""] * 2)[:2]
            if bssid:
                points.append({"bssid": bssid.lower(), "signal": int(signal or 0)})
        return sorted(points, key=lambda p: -p["signal"])

    def _saved_wifi(self):
        out = self._run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
        saved = set()
        for line in out.splitlines():
            name, kind = (split_terse(line) + [""])[:2]
            if kind == "802-11-wireless":
                saved.add(name)
        return saved

    def wifi_connect(self, ssid, password="", hidden=False):
        if not ssid:
            raise CommandError(tr("Не указано имя сети"))
        if self.dry_run:
            time.sleep(1)
            if password == "wrong":
                raise CommandError(tr("Неверный пароль"))
            self._fake_active = ssid
            self._fake_saved.add(ssid)
            return
        with self._lock:
            if not password and ssid in self._saved_wifi():
                self._run(["nmcli", "connection", "up", "id", ssid], timeout=60)
                return
            args = ["nmcli", "device", "wifi", "connect", ssid]
            if password:
                args += ["password", password]
            if hidden:
                args += ["hidden", "yes"]
            self._run(args, timeout=60)

    def wifi_disconnect(self):
        if self.dry_run:
            self._fake_active = None
            return
        device = self.wifi_device()
        if device:
            self._run(["nmcli", "device", "disconnect", device["device"]])

    def wifi_forget(self, ssid):
        """Удаляет сохранённую сеть Wi‑Fi, в том числе ту, что сейчас не видна.
        Только профили Wi‑Fi: Ethernet и прочие подключения так не удалить."""
        if self.dry_run:
            if ssid not in self._fake_saved:
                raise CommandError(tr("Сеть {ssid} не сохранена", ssid=ssid))
            self._fake_saved.discard(ssid)
            if self._fake_active == ssid:
                self._fake_active = None
            return
        with self._lock:
            if ssid not in self._saved_wifi():
                raise CommandError(tr("Сеть {ssid} не сохранена", ssid=ssid))
            self._run(["nmcli", "connection", "delete", "id", ssid])

    def wifi_radio(self, enabled):
        if self.dry_run:
            self._fake_radio = enabled
            return
        self._run(["nmcli", "radio", "wifi", "on" if enabled else "off"])

    def radio_enabled(self):
        if self.dry_run:
            return self._fake_radio
        return self._run(["nmcli", "radio", "wifi"]).strip() == "enabled"

    def other_connection(self):
        """Есть ли связь не через Wi‑Fi (например, кабель)."""
        if self.dry_run:
            return False
        out = self._run(["nmcli", "-t", "-f", "TYPE,STATE", "device"])
        for line in out.splitlines():
            kind, state = (split_terse(line) + [""])[:2]
            if kind not in ("wifi", "loopback", "wifi-p2p") and state == "connected":
                return True
        return False

    def _fake_wifi_state(self):
        names = [("Home", 82, "WPA2"), ("Office-5G", 64, "WPA2 WPA3"), ("Guest", 41, ""), ("Сосед", 23, "WPA2")]
        networks = []
        if self._fake_radio:
            networks = [
                {"ssid": n, "signal": s, "secure": bool(sec), "security": sec,
                 "active": n == self._fake_active, "saved": n in self._fake_saved}
                for n, s, sec in names
            ]
            networks.sort(key=lambda n: (not n["active"], -n["signal"]))
        return {
            "available": True,
            "radio": self._fake_radio,
            "device": "wlan0",
            "connection": self._fake_active if self._fake_radio else None,
            "networks": networks,
            "saved": sorted(self._fake_saved),
        }

    # --- Питание ----------------------------------------------------------

    def power(self, action):
        if action not in ("reboot", "poweroff"):
            raise CommandError(tr("Неизвестное действие"))
        if self.dry_run:
            log.warning("dry-run: %s", action)
            return
        # Даём HTTP-ответу уйти до того, как система начнёт гаситься.
        threading.Timer(1.0, self._run, args=(["systemctl", action],)).start()

    # --- Время ------------------------------------------------------------

    def timezones(self):
        if self.dry_run:
            return ["Europe/Moscow", "Europe/Kaliningrad", "Asia/Yekaterinburg", "UTC"]
        return self._run(["timedatectl", "list-timezones"]).split()

    def timezone(self):
        if self.dry_run:
            return "Europe/Moscow"
        return self._run(["timedatectl", "show", "-p", "Timezone", "--value"]).strip()

    def set_timezone(self, tz):
        if tz not in self.timezones():
            raise CommandError(tr("Неизвестный часовой пояс"))
        if self.dry_run:
            return
        self._run(["timedatectl", "set-timezone", tz])

    def hostname(self):
        import socket
        return socket.gethostname()
