"""Местоположение панели.

Координаты панели (ui.latitude/longitude) нужны теме «по солнцу» и
виджетам, у которых выбрано «как у панели» (погода). Способы (ui.location_mode):

  * manual — координаты и название задаются вручную (или поиском города);
  * ip     — по внешнему IP, без ключей, точность — город;
  * yandex — Яндекс Локатор по видимым точкам Wi‑Fi, точнее. Нужен ключ
             API, он хранится в location.yandex_key и наружу не отдаётся.

В автоматических режимах фоновый поток уточняет место раз в REFRESH секунд
и сразу после смены режима. Если Локатор не ответил, берётся место по IP.
"""

import json
import logging
import threading
import time
import urllib.request

from .i18n import tr
from .system import CommandError

log = logging.getLogger(__name__)

MODES = ("manual", "ip", "yandex")
REFRESH = 6 * 3600
RETRY = 10 * 60
# Меньше этого сдвига (градусы, ~1 км) координаты не перезаписываем.
EPSILON = 0.01

USER_AGENT = "WallHub (+https://github.com/tesa4ello/wall-hub)"
YANDEX_LOCATOR_URL = "https://locator.api.maps.yandex.ru/v1/locate?apikey={key}"
# Сервисы геопозиции по IP (без ключа), по порядку.
IP_SERVICES = (
    ("ipwho.is", "https://ipwho.is/?lang={lang}"),
    ("ipapi.co", "https://ipapi.co/json/"),
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat={lat}&lon={lon}&accept-language={lang}"


class LocationError(Exception):
    pass


def _request_json(url, body=None, timeout=10):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(256 * 1024).decode("utf-8"))


def parse_ip_service(name, data):
    """Ответ сервиса геопозиции по IP -> {lat, lon, name} или None."""
    if name == "ipwho.is" and not data.get("success", False):
        return None
    if data.get("error"):
        return None
    lat, lon = data.get("latitude"), data.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return {"lat": round(lat, 4), "lon": round(lon, 4), "name": data.get("city") or "", "accuracy": None}


def parse_yandex(data):
    point = (data.get("location") or {}).get("point") or {}
    lat, lon = point.get("lat"), point.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    accuracy = (data.get("location") or {}).get("accuracy")
    return {"lat": round(lat, 5), "lon": round(lon, 5), "name": "", "accuracy": accuracy}


def signal_to_dbm(percent):
    # NetworkManager отдаёт уровень в процентах; Локатору нужен dBm.
    return int(round(min(100, max(0, percent)) / 2 - 100))


class Locator:
    def __init__(self, config, system, request=_request_json):
        self.config = config
        self.system = system
        self._request = request
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.last = {"at": None, "source": None, "error": None, "accuracy": None}

    # --- способы ---------------------------------------------------------

    def by_ip(self, lang):
        errors = []
        for name, url in IP_SERVICES:
            try:
                found = parse_ip_service(name, self._request(url.format(lang=lang)))
            except Exception as e:
                errors.append(f"{name}: {e}")
                continue
            if found:
                return dict(found, source="ip")
            errors.append(f"{name}: no data")
        raise LocationError(tr("Не удалось определить место по IP: {error}", error="; ".join(errors)))

    def by_yandex(self, key, lang):
        if not key:
            raise LocationError(tr("Не задан ключ Яндекс Локатора"))
        try:
            points = self.system.wifi_bssids()
        except CommandError as e:
            points = []
            log.warning("wifi scan for locator failed: %s", e)
        body = {"wifi": [{"bssid": p["bssid"], "signal_strength": signal_to_dbm(p["signal"])} for p in points[:30]]}
        try:
            found = parse_yandex(self._request(YANDEX_LOCATOR_URL.format(key=key), body))
        except Exception as e:
            raise LocationError(tr("Яндекс Локатор не ответил: {error}", error=e))
        if not found:
            raise LocationError(tr("Яндекс Локатор не нашёл место"))
        found["name"] = self.place_name(found["lat"], found["lon"], lang)
        return dict(found, source="yandex")

    def place_name(self, lat, lon, lang):
        """Название населённого пункта по координатам (OpenStreetMap)."""
        try:
            data = self._request(NOMINATIM_URL.format(lat=lat, lon=lon, lang=lang))
        except Exception as e:
            log.info("reverse geocoding failed: %s", e)
            return ""
        address = data.get("address") or {}
        for field in ("city", "town", "village", "municipality", "county", "state"):
            if address.get(field):
                return address[field]
        return ""

    def locate(self, mode=None):
        """Определяет место выбранным способом и сохраняет его в настройках."""
        ui = self.config.get("ui")
        mode = mode or ui.get("location_mode", "manual")
        lang = ui.get("language", "ru")
        with self._lock:
            try:
                if mode == "yandex":
                    try:
                        found = self.by_yandex(self.config.get("location", "yandex_key"), lang)
                    except LocationError as e:
                        log.warning("yandex locator: %s; falling back to IP", e)
                        found = dict(self.by_ip(lang), fallback=str(e))
                elif mode == "ip":
                    found = self.by_ip(lang)
                else:
                    raise LocationError(tr("Местоположение задано вручную"))
            except LocationError as e:
                self.last = {"at": time.time(), "source": None, "error": str(e), "accuracy": None}
                raise
            self.last = {"at": time.time(), "source": found["source"], "error": found.get("fallback"),
                         "accuracy": found.get("accuracy")}
            patch = {}
            if abs(found["lat"] - ui["latitude"]) >= EPSILON or abs(found["lon"] - ui["longitude"]) >= EPSILON:
                patch.update(latitude=found["lat"], longitude=found["lon"])
            if found["name"] and found["name"] != ui.get("location_name"):
                patch["location_name"] = found["name"]
            if patch:
                self.config.update({"ui": patch})
            return dict(found, changed=bool(patch))

    # --- фоновое уточнение --------------------------------------------------

    def wake(self):
        self._wake.set()

    def run_forever(self):
        delay = 5
        while True:
            self._wake.wait(delay)
            self._wake.clear()
            if self.config.get("ui", "location_mode") == "manual":
                delay = REFRESH
                continue
            try:
                self.locate()
                delay = REFRESH
            except LocationError as e:
                log.warning("location: %s", e)
                delay = RETRY

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True, name="location").start()
