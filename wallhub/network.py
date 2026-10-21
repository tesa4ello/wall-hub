"""Раздел «Сеть»: адреса, внешний IP по нескольким сервисам, тест скорости, QR админки."""

import ipaddress
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .system import split_terse

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) WallHub"

# Сервисы, отдающие внешний адрес. Все — только по IPv4, чтобы сравнивать
# одинаковое: если адреса разошлись, значит часть трафика идёт другим путём
# (прокси, балансировка у провайдера).
EXTERNAL_SERVICES = [
    ("Яндекс", "https://ipv4-internet.yandex.net/api/v0/ip", "json"),
    ("Cloudflare", "https://1.1.1.1/cdn-cgi/trace", "trace"),
    ("ipify", "https://api.ipify.org", "text"),
    ("icanhazip", "https://ipv4.icanhazip.com", "text"),
    ("ifconfig.me", "https://ifconfig.me/ip", "text"),
    ("ipinfo.io", "https://ipinfo.io/ip", "text"),
]
EXTERNAL_TTL = 60

YANDEX_PROBES = "https://yandex.ru/internet/api/v0/get-probes"
SPEED_PROVIDERS = {"ookla": "Speedtest", "yandex": "Яндекс Интернетометр"}

SKIP_IFACES = ("lo", "docker", "veth", "br-", "virbr")


class SpeedtestError(Exception):
    """Ошибка теста. Текст переводит браузер: message — исходная строка,
    args_map — значения для подстановки."""

    def __init__(self, message, **args_map):
        super().__init__(message.format(**args_map) if args_map else message)
        self.message = message
        self.args_map = args_map


def _get(url, timeout=6):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(4096).decode("utf-8", "replace")


def parse_ip(kind, body):
    if kind == "json":
        body = json.loads(body)
    elif kind == "trace":
        match = re.search(r"^ip=(\S+)$", body, re.M)
        body = match.group(1) if match else ""
    return str(ipaddress.ip_address(str(body).strip()))


def group_external(results):
    """Сводит ответы сервисов: {addresses: [{ip, services}], failed: [имена]}."""
    addresses = {}
    failed = []
    for name, ip in results:
        if ip:
            addresses.setdefault(ip, []).append(name)
        else:
            failed.append(name)
    ordered = sorted(addresses.items(), key=lambda item: -len(item[1]))
    return {"addresses": [{"ip": ip, "services": names} for ip, names in ordered], "failed": failed}


class Network:
    def __init__(self, state_dir, admin_port, dry_run=False):
        self.dry_run = dry_run
        self.admin_port = admin_port
        self.results_path = os.path.join(state_dir, "speedtest.json")
        self._external = None
        self._external_at = 0.0
        self._external_lock = threading.Lock()
        self._speed_lock = threading.Lock()
        self.speed = {"running": None, "phase": "", "results": self._load_results()}

    # --- локальная сеть --------------------------------------------------------

    def _cmd(self, args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def local(self):
        """Интерфейсы с адресами, шлюз и DNS."""
        if self.dry_run:
            return {
                "interfaces": [
                    {"name": "wlan0", "type": "wifi", "connection": "Home", "addresses": ["192.168.1.57/24"]},
                    {"name": "end0", "type": "ethernet", "connection": "", "addresses": []},
                ],
                "gateway": "192.168.1.1",
                "dns": ["192.168.1.1", "1.1.1.1"],
            }
        devices = {}
        for line in self._cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,CONNECTION", "device"]).splitlines():
            parts = split_terse(line)
            if len(parts) >= 3:
                devices[parts[0]] = {"type": parts[1], "connection": parts[2]}
        interfaces = []
        try:
            addrs = json.loads(self._cmd(["ip", "-j", "-4", "addr", "show"]) or "[]")
        except ValueError:
            addrs = []
        for item in addrs:
            name = item.get("ifname", "")
            if name.startswith(SKIP_IFACES):
                continue
            device = devices.get(name, {})
            interfaces.append({
                "name": name,
                "type": device.get("type", ""),
                "connection": device.get("connection", ""),
                "addresses": [f"{a['local']}/{a['prefixlen']}" for a in item.get("addr_info", [])],
            })
        try:
            routes = json.loads(self._cmd(["ip", "-j", "-4", "route", "show", "default"]) or "[]")
        except ValueError:
            routes = []
        dns = []
        for line in self._cmd(["nmcli", "-t", "-f", "IP4.DNS", "device", "show"]).splitlines():
            server = line.partition(":")[2]
            if server and server not in dns:
                dns.append(server)
        return {
            "interfaces": interfaces,
            "gateway": routes[0].get("gateway", "") if routes else "",
            "dns": dns,
        }

    @staticmethod
    def connected(local):
        for iface in local["interfaces"]:
            for addr in iface["addresses"]:
                if not ipaddress.ip_interface(addr).ip.is_link_local:
                    return True
        return False

    def admin(self, local, admin_url):
        """Адрес админки и QR-код (SVG), только когда панель в сети."""
        if not self.connected(local):
            return None
        try:
            import segno
        except ImportError:
            return {"url": admin_url, "qr": None}
        qr = segno.make(admin_url, error="m")
        svg = qr.svg_inline(scale=1, border=2, dark="#000", light=None, omitsize=True)
        return {"url": admin_url, "qr": svg}

    # --- внешний адрес -------------------------------------------------------------

    def external(self, refresh=False):
        with self._external_lock:
            fresh = time.monotonic() - self._external_at < EXTERNAL_TTL
            if self._external is None or refresh or not fresh:
                self._external = self._fetch_external()
                self._external_at = time.monotonic()
            return self._external

    def _fetch_external(self):
        if self.dry_run:
            time.sleep(0.5)
            results = [("Яндекс", "203.0.113.24"), ("Cloudflare", "198.51.100.7"), ("ipify", "203.0.113.24"),
                       ("icanhazip", "203.0.113.24"), ("ifconfig.me", None), ("ipinfo.io", "203.0.113.24")]
        else:
            def ask(service):
                name, url, kind = service
                try:
                    return name, parse_ip(kind, _get(url))
                except Exception as e:
                    log.info("external ip %s: %s", name, e)
                    return name, None

            with ThreadPoolExecutor(len(EXTERNAL_SERVICES)) as pool:
                results = list(pool.map(ask, EXTERNAL_SERVICES))
        return dict(group_external(results), checked_at=time.time())

    # --- тест скорости ---------------------------------------------------------------

    def _load_results(self):
        try:
            with open(self.results_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def speed_status(self):
        return self.speed

    def start_speedtest(self, provider):
        if provider not in SPEED_PROVIDERS:
            raise ValueError(provider)
        if not self._speed_lock.acquire(blocking=False):
            return False
        self.speed.update(running=provider, phase="Подготовка…")
        threading.Thread(target=self._run_speedtest, args=(provider,), daemon=True, name="speedtest").start()
        return True

    def _phase(self, text):
        self.speed["phase"] = text

    def _run_speedtest(self, provider):
        try:
            if self.dry_run:
                result = self._fake_speedtest(provider)
            elif provider == "ookla":
                result = self._ookla()
            else:
                result = self._yandex()
        except Exception as e:
            log.warning("speedtest %s failed: %s", provider, e)
            if isinstance(e, SpeedtestError):
                result = {"error": e.message, "error_args": e.args_map}
            else:
                result = {"error": "Тест не удался: {error}", "error_args": {"error": str(e)}}
        result["at"] = time.time()
        self.speed["results"] = dict(self.speed["results"], **{provider: result})
        try:
            tmp = self.results_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.speed["results"], f, ensure_ascii=False)
            os.replace(tmp, self.results_path)
        except OSError as e:
            log.warning("cannot save speedtest results: %s", e)
        self.speed.update(running=None, phase="")
        self._speed_lock.release()

    def _fake_speedtest(self, provider):
        for phase in ("Пинг…", "Загрузка…", "Отдача…"):
            self._phase(phase)
            time.sleep(1)
        base = 94.3 if provider == "ookla" else 88.1
        return {"download": base, "upload": base / 2.3, "ping": 7.4, "server": "Тестовый сервер, Москва"}

    def _ookla(self):
        try:
            import speedtest
        except ImportError:
            raise SpeedtestError("Не установлен модуль speedtest-cli")
        try:
            self._phase("Выбираю сервер…")
            st = speedtest.Speedtest(secure=True)
            st.get_best_server()
            self._phase("Загрузка…")
            st.download()
            self._phase("Отдача…")
            st.upload(pre_allocate=False)
        except speedtest.SpeedtestException as e:
            raise SpeedtestError("Speedtest: {error}", error=str(e))
        r = st.results
        server = r.server or {}
        return {
            "download": round(r.download / 1e6, 1),
            "upload": round(r.upload / 1e6, 1),
            "ping": round(r.ping, 1),
            "server": ", ".join(x for x in (server.get("sponsor"), server.get("name")) if x),
        }

    def _yandex(self):
        """Тот же протокол, что у yandex.ru/internet: сервис выдаёт набор
        проб (адреса для пинга, скачивания и отдачи), скорость меряем сами."""
        try:
            probes = json.loads(self._fetch(YANDEX_PROBES))
        except (OSError, ValueError) as e:
            raise SpeedtestError("Интернетометр недоступен: {error}", error=str(e))

        def urls(kind):
            section = probes.get(kind) or {}
            items = section.get("probes", section) if isinstance(section, dict) else section
            return [p["url"] if isinstance(p, dict) else p for p in items or []]

        latency, download, upload = urls("latency"), urls("download"), urls("upload")
        if not download:
            raise SpeedtestError("Интернетометр вернул неожиданный ответ")

        self._phase("Пинг…")
        pings = []
        for url in latency[:5]:
            started = time.monotonic()
            try:
                self._fetch(url, limit=1024)
                pings.append((time.monotonic() - started) * 1000)
            except OSError:
                pass

        self._phase("Загрузка…")
        down = max((self._measure_download(url) for url in download[:4]), default=0)

        self._phase("Отдача…")
        up = max((self._measure_upload(url) for url in upload[:3]), default=0)
        if not down:
            raise SpeedtestError("Не удалось измерить скорость загрузки")
        return {
            "download": round(down / 1e6, 1),
            "upload": round(up / 1e6, 1) if up else None,
            "ping": round(min(pings), 1) if pings else None,
            "server": "yandex.ru/internet",
        }

    def _fetch(self, url, limit=1 << 20):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read(limit).decode("utf-8", "replace")

    @staticmethod
    def _measure_download(url, seconds=8):
        """Бит/с при скачивании url (не дольше seconds)."""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        total = 0
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                started = time.monotonic()
                while time.monotonic() - started < seconds:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                elapsed = time.monotonic() - started
        except OSError as e:
            log.info("yandex download %s: %s", url, e)
            return 0
        return total * 8 / elapsed if elapsed > 0.05 else 0

    @staticmethod
    def _measure_upload(url, size=8 << 20):
        """Бит/с при отправке size байт случайных данных."""
        payload = random.randbytes(size)
        request = urllib.request.Request(url, data=payload, method="POST", headers={
            "User-Agent": USER_AGENT, "Content-Type": "application/octet-stream"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read(1024)
        except OSError as e:
            log.info("yandex upload %s: %s", url, e)
            return 0
        elapsed = time.monotonic() - started
        return size * 8 / elapsed if elapsed > 0.05 else 0
