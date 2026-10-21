"""Настройка сетевых интерфейсов: Ethernet и IPv4 (DHCP или вручную).

Wi‑Fi и Ethernet настраиваются через NetworkManager (nmcli). Ethernet на
Armbian поначалу держит netplan + systemd-networkd, и NetworkManager его не
трогает («unmanaged»); такой интерфейс панель только показывает, а кнопка
«Управлять из панели» запускает от root deploy/netplan-release-ethernet.py
(юнит wallhub-net-ethernet.service): тот переносит настройки в NetworkManager
и сам откатывает всё назад, если связь пропала.

Смена адреса тоже проверяется: после применения ждём адрес и отвечающий шлюз,
иначе возвращаем прежние настройки. Через этот интерфейс может работать и
админка, и SSH, поэтому применение идёт в фоне, а результат видно по статусу.
"""

import ipaddress
import json
import logging
import os
import socket
import subprocess
import threading
import time

from .i18n import tr
from .system import CommandError, split_terse

log = logging.getLogger(__name__)

KINDS = {"wifi": "wifi", "ethernet": "ethernet"}
MIGRATE_UNIT = "wallhub-net-ethernet.service"
VERIFY_TIMEOUT = 25


class NetConfigError(ValueError):
    pass


def parse_ipv4(method, address="", gateway="", dns=()):
    """Проверяет настройки IPv4 из админки. Возвращает чистый словарь."""
    if method == "auto":
        return {"method": "auto", "address": "", "gateway": "", "dns": [str(ipaddress.IPv4Address(d)) for d in _dns(dns)]}
    if method != "manual":
        raise NetConfigError(tr("Неизвестный способ настройки"))
    try:
        iface = ipaddress.IPv4Interface(str(address).strip())
    except ValueError:
        raise NetConfigError(tr("Адрес в виде 192.168.1.50/24"))
    if iface.network.prefixlen in (0, 32) or iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        raise NetConfigError(tr("Адрес в виде 192.168.1.50/24"))
    gw = ""
    if str(gateway or "").strip():
        try:
            gw = ipaddress.IPv4Address(str(gateway).strip())
        except ValueError:
            raise NetConfigError(tr("Шлюз должен быть IPv4-адресом"))
        if gw not in iface.network or gw == iface.ip:
            raise NetConfigError(tr("Шлюз должен быть в той же сети, что и адрес"))
        gw = str(gw)
    return {"method": "manual", "address": str(iface), "gateway": gw, "dns": [str(d) for d in _dns(dns)]}


def _dns(dns):
    if isinstance(dns, str):
        dns = dns.replace(",", " ").split()
    result = []
    for item in dns or ():
        try:
            result.append(ipaddress.IPv4Address(str(item).strip()))
        except ValueError:
            raise NetConfigError(tr("DNS — IPv4-адреса через пробел или запятую"))
    if len(result) > 3:
        raise NetConfigError(tr("Не больше трёх DNS-серверов"))
    return result


class NetConfig:
    def __init__(self, system, state_dir):
        self.system = system
        self.dry_run = system.dry_run
        self.migrate_path = os.path.join(state_dir, "net-migrate.json")
        self.lock = threading.Lock()
        # Результат последнего применения по интерфейсу: {device: {state, message, ...}}.
        self.jobs = {}
        self._fake = {
            "eth0": {"type": "ethernet", "state": "unmanaged", "connection": "", "managed": False, "profile": "",
                     "ipv4": {"method": "auto", "address": "", "gateway": "", "dns": []},
                     "current": {"addresses": ["192.168.1.20/24"], "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}},
            "wlan0": {"type": "wifi", "state": "connected", "connection": "Home", "managed": True, "profile": "Home",
                      "ipv4": {"method": "auto", "address": "", "gateway": "", "dns": []},
                      "current": {"addresses": ["192.168.1.34/24"], "gateway": "192.168.1.1", "dns": ["192.168.1.1"]}},
        }

    def _run(self, args, timeout=30):
        return self.system._run(args, timeout=timeout)

    # --- чтение -----------------------------------------------------------

    def devices(self):
        """Wi‑Fi и Ethernet интерфейсы с текущими адресами и настройкой IPv4."""
        if self.dry_run:
            return [dict(v, device=k, job=self.jobs.get(k)) for k, v in self._fake.items()]
        out = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        result = []
        for line in out.splitlines():
            device, kind, state, connection = (split_terse(line) + [""] * 4)[:4]
            if kind not in KINDS:
                continue
            managed = state != "unmanaged"
            item = {"device": device, "type": kind, "state": state, "managed": managed,
                    "connection": connection if connection != "--" else "", "job": self.jobs.get(device)}
            item["current"] = self._current(device) if managed else self._current_ip(device)
            profile = item["connection"] or (self._profile_for(device) if managed and kind == "ethernet" else "")
            item["profile"] = profile
            item["ipv4"] = self._profile_ipv4(profile) if profile else None
            result.append(item)
        return result

    def _current(self, device):
        out = self._run(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", device])
        cur = {"addresses": [], "gateway": "", "dns": []}
        for line in out.splitlines():
            key, _, value = line.partition(":")
            if not value or value == "--":
                continue
            if key.startswith("IP4.ADDRESS"):
                cur["addresses"].append(value)
            elif key == "IP4.GATEWAY":
                cur["gateway"] = value
            elif key.startswith("IP4.DNS"):
                cur["dns"].append(value)
        return cur

    def _current_ip(self, device):
        """Адреса интерфейса, которым управляет не NetworkManager (через ip)."""
        cur = {"addresses": [], "gateway": "", "dns": []}
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", device], capture_output=True, text=True, timeout=5).stdout
            cur["addresses"] = [line.split()[3] for line in out.splitlines() if len(line.split()) > 3]
            out = subprocess.run(["ip", "-4", "route", "show", "default", "dev", device], capture_output=True, text=True, timeout=5).stdout
            parts = out.split()
            if "via" in parts:
                cur["gateway"] = parts[parts.index("via") + 1]
        except (OSError, subprocess.SubprocessError):
            pass
        return cur

    def _profile_for(self, device):
        """Сохранённый профиль NetworkManager для проводного интерфейса."""
        out = self._run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"])
        candidates = []
        for line in out.splitlines():
            name, kind, dev = (split_terse(line) + [""] * 3)[:3]
            if kind == "802-3-ethernet":
                candidates.append(name)
        for name in candidates:
            iface = self._run(["nmcli", "-g", "connection.interface-name", "connection", "show", name]).strip()
            if iface == device:
                return name
        return ""

    def _profile_ipv4(self, profile):
        out = self._run(["nmcli", "-g", "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns", "connection", "show", profile])
        lines = (out.splitlines() + [""] * 4)[:4]
        return {
            "method": "manual" if lines[0].strip() == "manual" else "auto",
            "address": lines[1].split(",")[0].strip(),
            "gateway": lines[2].strip(),
            "dns": [d for d in lines[3].replace(",", " ").split() if d],
        }

    def migrate_status(self):
        try:
            with open(self.migrate_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # --- изменение --------------------------------------------------------

    def set_ipv4(self, device, settings):
        """Применяет настройку IPv4 в фоне с проверкой связи и откатом."""
        devices = {d["device"]: d for d in self.devices()}
        dev = devices.get(device)
        if dev is None:
            raise NetConfigError(tr("Интерфейс {name} не найден", name=device))
        if not dev["managed"]:
            raise NetConfigError(tr("Интерфейсом {name} пока управляет система, а не панель", name=device))
        if (self.jobs.get(device) or {}).get("state") == "applying":
            raise NetConfigError(tr("Настройки {name} ещё применяются", name=device))
        if dev["type"] == "wifi" and not dev["profile"]:
            raise NetConfigError(tr("Сначала подключитесь к сети Wi‑Fi"))
        self.jobs[device] = {"state": "applying", "started": time.time()}
        threading.Thread(target=self._apply, args=(dev, settings), daemon=True, name=f"net-{device}").start()

    def _apply(self, dev, settings):
        device = dev["device"]
        try:
            if self.dry_run:
                time.sleep(2)
                fake = self._fake[device]
                fake["ipv4"] = dict(settings)
                if settings["method"] == "manual":
                    fake["current"] = {"addresses": [settings["address"]], "gateway": settings["gateway"], "dns": settings["dns"]}
                self.jobs[device] = {"state": "ok"}
                return
            profile = dev["profile"]
            if not profile:
                profile = f"Wired {device}"
                self._run(["nmcli", "connection", "add", "type", "ethernet", "ifname", device, "con-name", profile,
                           "connection.autoconnect", "yes"])
            old = self._profile_ipv4(profile)
            self._modify(profile, settings)
            ok, reason = self._up_and_verify(profile, device, settings)
            if ok:
                self.jobs[device] = {"state": "ok"}
                return
            log.warning("%s: связь не появилась (%s), возвращаю прежние настройки", device, reason)
            self._modify(profile, old)
            self._up_and_verify(profile, device, old)
            self.jobs[device] = {"state": "rolled_back", "message": reason}
        except CommandError as e:
            self.jobs[device] = {"state": "failed", "message": str(e)}
        except Exception as e:  # noqa: BLE001 — фоновый поток не должен молча умирать
            log.exception("настройка %s не применилась", device)
            self.jobs[device] = {"state": "failed", "message": str(e)}

    def _modify(self, profile, s):
        manual = s["method"] == "manual"
        self._run([
            "nmcli", "connection", "modify", profile,
            "ipv4.method", "manual" if manual else "auto",
            "ipv4.addresses", s["address"] if manual else "",
            "ipv4.gateway", s["gateway"] if manual else "",
            "ipv4.dns", " ".join(s["dns"]),
            "ipv4.ignore-auto-dns", "yes" if s["dns"] else "no",
        ])

    def _up_and_verify(self, profile, device, s):
        """Поднимает профиль и ждёт адрес и ответ шлюза. (ok, причина)."""
        try:
            self._run(["nmcli", "connection", "up", profile], timeout=60)
        except CommandError as e:
            return False, str(e)
        deadline = time.monotonic() + VERIFY_TIMEOUT
        while time.monotonic() < deadline:
            cur = self._current(device)
            gateway = cur["gateway"] or s.get("gateway")
            if cur["addresses"] and (not gateway or self._ping(gateway)):
                return True, ""
            time.sleep(2)
        return False, tr("нет адреса или шлюз не отвечает")

    @staticmethod
    def _ping(host):
        """Отвечает ли шлюз: ping, а если ICMP недоступен сервису — по ARP
        (датаграмма на шлюз заставляет ядро узнать его MAC-адрес)."""
        try:
            if subprocess.run(["ping", "-c", "1", "-W", "2", host], capture_output=True, timeout=5).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(b"", (host, 9))
            time.sleep(1)
            out = subprocess.run(["ip", "neigh", "show", host], capture_output=True, text=True, timeout=5).stdout
            return "lladdr" in out and "FAILED" not in out and "INCOMPLETE" not in out
        except (OSError, subprocess.SubprocessError):
            return False

    def manage(self, device):
        """Передаёт проводной интерфейс из netplan в NetworkManager (от root, с откатом)."""
        if self.dry_run:
            fake = self._fake.get(device)
            if fake:
                fake.update(managed=True, state="connected", connection=f"Wired {device}", profile=f"Wired {device}")
            with open(self.migrate_path, "w", encoding="utf-8") as f:
                json.dump({"state": "done", "device": device}, f)
            return
        self._run(["systemctl", "start", "--no-block", MIGRATE_UNIT])
