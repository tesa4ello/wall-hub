"""Погода для модуля web/modules/weather.

Прогноз и поиск городов берутся с Open-Meteo (ключ не нужен). Ответы
кэшируются: все виджеты одного места получают один и тот же прогноз, а
Open-Meteo опрашивается не чаще раза в FORECAST_TTL. Если сеть пропала,
отдаётся последний полученный прогноз с пометкой stale.

Наружу отдаётся сжатый вид прогноза (см. normalize): время — unix-секунды,
температура — °C, ветер — м/с, давление — гПа, осадки — мм. Единицы
пересчитывает сам виджет.
"""

import http.client
import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request

from . import metno, weather_providers
from .i18n import tr

log = logging.getLogger(__name__)

USER_AGENT = "WallHub (+https://github.com/tesa4ello/wall-hub)"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
# Запасной поиск городов, если Open-Meteo недоступен.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Провайдер, который не ответил, пропускаем столько секунд (если есть другие).
PROVIDER_COOLDOWN = 30 * 60

DEFAULT_SETTINGS = {
    "providers": [{"id": pid, "enabled": not meta["key"]} for pid, meta in weather_providers.PROVIDERS.items()],
    "keys": {},
    "refresh_min": 10,
    "units": {"temp": "c", "wind": "ms", "pressure": "mmhg"},
}


def normalize_settings(settings):
    """Порядок и включённость провайдеров: неизвестные убираются, новые
    (из более свежей версии) добавляются в конец выключенными."""
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    seen, providers = set(), []
    for p in settings.get("providers") or []:
        pid = p.get("id") if isinstance(p, dict) else None
        if pid in weather_providers.PROVIDERS and pid not in seen:
            seen.add(pid)
            providers.append({"id": pid, "enabled": bool(p.get("enabled"))})
    for pid, meta in weather_providers.PROVIDERS.items():
        if pid not in seen:
            providers.append({"id": pid, "enabled": False})
    settings["providers"] = providers
    settings["keys"] = {k: v for k, v in (settings.get("keys") or {}).items() if v}
    try:
        settings["refresh_min"] = max(5, min(60, int(settings.get("refresh_min") or 10)))
    except (TypeError, ValueError):
        settings["refresh_min"] = 10
    units = dict(DEFAULT_SETTINGS["units"], **(settings.get("units") or {}))
    settings["units"] = units
    return settings

FORECAST_TTL = 10 * 60
# Устаревший прогноз ещё показываем, но не дольше суток.
STALE_LIMIT = 24 * 3600
# После ошибки не дёргаем Open-Meteo снова сразу же.
RETRY_AFTER = 60
SEARCH_TTL = 24 * 3600
MAX_ENTRIES = 32

HOURS = 48
DAYS = 10
QUARTERS = 12  # ближайшие 3 часа по 15 минут

CURRENT = ("temperature_2m,apparent_temperature,relative_humidity_2m,is_day,precipitation,"
           "weather_code,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover")
HOURLY = "temperature_2m,precipitation_probability,precipitation,weather_code,is_day,uv_index"
DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
         "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max")
MINUTELY = "precipitation,snowfall"


class WeatherError(Exception):
    pass


def _connect_ipv4_first(host, port, timeout):
    """Как socket.create_connection, но сначала IPv4. На панелях часто есть
    IPv6-адрес без IPv6-маршрута: тогда попытка по IPv6 висит до таймаута."""
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    infos.sort(key=lambda info: info[0] != socket.AF_INET)
    error = None
    for family, kind, proto, _, address in infos:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            return sock
        except OSError as e:
            sock.close()
            error = e
    raise error or OSError(f"cannot resolve {host}")


class _HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect_ipv4_first(self.host, self.port, self.timeout)
        self.sock = sock
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


opener = urllib.request.build_opener(_HTTPSHandler())


def get_json(url, params, timeout=6, limit=2 * 1024 * 1024, headers=None):
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers=dict({"User-Agent": USER_AGENT, "Accept": "application/json"}, **(headers or {})))
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read(limit).decode("utf-8"))
    finally:
        log.info("GET %s: %.1f s", url, time.monotonic() - started)


def place_key(lat, lon):
    # ~1 км: соседние виджеты одного города попадают в одну запись кэша.
    return round(lat, 2), round(lon, 2)


def _round(v, digits=1):
    return None if v is None else round(v, digits)


def _rows(block, fields, start, count):
    """Колонки Open-Meteo {"time": [...], "x": [...]} -> список словарей."""
    times = block.get("time") or []
    rows = []
    for i in range(start, min(len(times), start + count)):
        row = {"time": times[i]}
        for out, src, digits in fields:
            values = block.get(src) or []
            value = values[i] if i < len(values) else None
            row[out] = _round(value, digits) if isinstance(value, float) else value
        rows.append(row)
    return rows


def _first_index(times, now, step):
    """Индекс интервала, в который попадает now (или 0)."""
    for i, t in enumerate(times):
        if t + step > now:
            return i
    return max(0, len(times) - 1)


def normalize(raw, now=None):
    now = time.time() if now is None else now
    cur = raw.get("current") or {}
    hourly = raw.get("hourly") or {}
    daily = raw.get("daily") or {}
    minutely = raw.get("minutely_15") or {}

    h0 = _first_index(hourly.get("time") or [], now, 3600)
    m0 = _first_index(minutely.get("time") or [], now, 0)
    uv = (hourly.get("uv_index") or [None] * (h0 + 1))[h0] if hourly.get("time") else None

    return {
        "place": {
            "lat": raw.get("latitude"),
            "lon": raw.get("longitude"),
            "timezone": raw.get("timezone") or "UTC",
            "utc_offset": raw.get("utc_offset_seconds", 0),
        },
        "current": {
            "time": cur.get("time"),
            "temp": _round(cur.get("temperature_2m")),
            "feels": _round(cur.get("apparent_temperature")),
            "humidity": cur.get("relative_humidity_2m"),
            "precip": _round(cur.get("precipitation")),
            "code": cur.get("weather_code"),
            "day": bool(cur.get("is_day", 1)),
            "pressure": _round(cur.get("pressure_msl")),
            "wind": _round(cur.get("wind_speed_10m")),
            "wind_dir": cur.get("wind_direction_10m"),
            "gusts": _round(cur.get("wind_gusts_10m")),
            "clouds": cur.get("cloud_cover"),
            "uv": _round(uv),
        },
        "hourly": [
            dict(r, day=bool(r["day"]))
            for r in _rows(hourly, [("temp", "temperature_2m", 1), ("code", "weather_code", 0),
                                    ("day", "is_day", 0), ("pop", "precipitation_probability", 0),
                                    ("precip", "precipitation", 1)], h0, HOURS)
        ],
        "daily": _rows(daily, [("code", "weather_code", 0), ("tmax", "temperature_2m_max", 1),
                               ("tmin", "temperature_2m_min", 1), ("pop", "precipitation_probability_max", 0),
                               ("precip", "precipitation_sum", 1), ("sunrise", "sunrise", 0),
                               ("sunset", "sunset", 0), ("uv", "uv_index_max", 1),
                               ("wind", "wind_speed_10m_max", 1)], 0, DAYS),
        "minutely": _rows(minutely, [("precip", "precipitation", 2), ("snow", "snowfall", 2)], m0, QUARTERS),
    }


class Weather:
    def __init__(self, fetch=get_json, clock=time.time, local_tz=lambda: None, settings=lambda: None):
        self._fetch = fetch
        self._clock = clock
        # Часовой пояс панели: для провайдеров, которые его не сообщают.
        self._local_tz = local_tz
        # Настройки раздела «Погода» в админке (config["weather"]).
        self._settings = settings
        # id провайдера -> {"at", "ok", "error", "ms"}: последний запрос.
        self.status = {}
        self._lock = threading.Lock()
        self._key_locks = {}
        # key -> {"data", "at", "failed_at"}
        self._forecasts = {}
        self._searches = {}

    def _key_lock(self, key):
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def forecast(self, lat, lon, tz=None):
        key = place_key(lat, lon)
        with self._key_lock(key):
            entry = self._forecasts.get(key)
            now = self._clock()
            if entry and "data" in entry and now - entry["at"] < entry.get("ttl", FORECAST_TTL):
                return self._result(entry, now)
            if entry and now - entry.get("failed_at", 0) < RETRY_AFTER:
                if "data" not in entry:
                    raise WeatherError(entry["error"])
                return self._result(entry, now)
            try:
                data, ttl = self._fetch_any(key, tz, now)
                entry = {"data": data, "at": now, "ttl": ttl}
                self._store(self._forecasts, key, entry)
            except Exception as e:
                log.warning("weather for %s failed: %s", key, e)
                if entry and "data" in entry and now - entry["at"] < STALE_LIMIT:
                    entry["failed_at"] = now
                    return self._result(entry, now)
                # Прогноза нет: запоминаем ошибку, чтобы не ждать сеть на каждый запрос.
                error = tr("Не удалось получить погоду: {error}", error=e)
                self._store(self._forecasts, key, {"at": 0, "failed_at": now, "error": error})
                raise WeatherError(error)
            return self._result(entry, now)

    def settings(self):
        return normalize_settings(self._settings())

    def clear(self):
        """Настройки поменялись: следующий запрос идёт к провайдерам заново."""
        with self._lock:
            self._forecasts.clear()

    def _fetch_any(self, key, tz, now):
        """Прогноз с первого ответившего провайдера в порядке из настроек.
        Недавно не ответивших пропускаем, пока есть кого спросить."""
        settings = self.settings()
        usable = [p["id"] for p in settings["providers"] if p["enabled"]
                  and (not weather_providers.PROVIDERS[p["id"]]["key"] or settings["keys"].get(p["id"]))]
        if not usable:
            raise WeatherError(tr("Нет включённых источников погоды"))
        fresh = [pid for pid in usable if self.status.get(pid, {}).get("ok", True)
                 or now - self.status[pid]["at"] >= PROVIDER_COOLDOWN]
        errors = []
        for pid in fresh or usable:
            try:
                data = self._fetch_provider(pid, key, tz, now, settings["keys"].get(pid))
            except Exception as e:
                errors.append(f"{weather_providers.PROVIDERS[pid]['title']}: {e}")
                continue
            ttl = max(settings["refresh_min"] * 60, weather_providers.PROVIDERS[pid]["min_ttl"])
            return dict(data, refresh=ttl), ttl
        raise WeatherError("; ".join(errors))

    def _fetch_provider(self, pid, key, tz, now, api_key=None):
        started = time.monotonic()
        try:
            data = self._fetch_source(pid, key, tz, now, api_key)
        except Exception as e:
            log.warning("weather from %s for %s failed: %s", pid, key, e)
            self.status[pid] = {"at": now, "ok": False, "error": str(e), "ms": int((time.monotonic() - started) * 1000)}
            raise
        self.status[pid] = {"at": now, "ok": True, "error": None, "ms": int((time.monotonic() - started) * 1000)}
        return data

    def test(self, pid, lat, lon, tz=None):
        """Проверка одного провайдера из админки (без кэша)."""
        if pid not in weather_providers.PROVIDERS:
            raise WeatherError(tr("Неизвестный источник погоды"))
        settings = self.settings()
        api_key = settings["keys"].get(pid)
        if weather_providers.PROVIDERS[pid]["key"] and not api_key:
            raise WeatherError(tr("Для этого источника нужен ключ"))
        try:
            data = self._fetch_provider(pid, place_key(lat, lon), tz, self._clock(), api_key)
        except Exception as e:
            raise WeatherError(str(e))
        return {"temp": data["current"]["temp"], "code": data["current"]["code"], "ms": self.status[pid]["ms"]}

    def _fetch_source(self, source, key, tz, now, api_key=None):
        tz = tz or self._local_tz()
        if source in weather_providers.FETCHERS:
            return weather_providers.FETCHERS[source](self._fetch, key[0], key[1], tz, api_key, now, HOURS, DAYS)
        if source == "met.no":
            raw = self._fetch(metno.URL, {"lat": key[0], "lon": key[1]})
            return metno.normalize(raw, key[0], key[1], tz or "UTC", now, HOURS, DAYS)
        raw = self._fetch(FORECAST_URL, {
            "latitude": key[0], "longitude": key[1],
            "current": CURRENT, "hourly": HOURLY, "daily": DAILY,
            "minutely_15": MINUTELY, "forecast_minutely_15": QUARTERS + 4,
            "forecast_days": DAYS, "timezone": "auto", "timeformat": "unixtime",
            "wind_speed_unit": "ms",
        })
        if raw.get("error"):
            raise WeatherError(raw.get("reason") or "error")
        return dict(normalize(raw, now), source="open-meteo")

    def _result(self, entry, now):
        stale = now - entry["at"] >= entry.get("ttl", FORECAST_TTL) + RETRY_AFTER
        return dict(entry["data"], updated=entry["at"], stale=stale, units=self.settings()["units"])

    def _store(self, table, key, entry):
        with self._lock:
            table[key] = entry
            while len(table) > MAX_ENTRIES:
                del table[min(table, key=lambda k: table[k]["at"])]

    def search(self, query, language="ru"):
        """Поиск города по названию: до 8 вариантов."""
        query = " ".join(str(query).split())[:80]
        if len(query) < 2:
            return []
        key = (query.lower(), language)
        now = self._clock()
        with self._lock:
            cached = self._searches.get(key)
        if cached and now - cached["at"] < SEARCH_TTL:
            return cached["data"]
        try:
            results = self._search_open_meteo(query, language)
        except Exception as e:
            log.warning("city search via open-meteo failed: %s", e)
            try:
                results = self._search_nominatim(query, language)
            except Exception as e2:
                log.warning("city search via nominatim failed: %s", e2)
                raise WeatherError(tr("Поиск города не удался: {error}", error=e2))
        self._store(self._searches, key, {"data": results, "at": now})
        return results

    def _search_open_meteo(self, query, language):
        raw = self._fetch(GEOCODING_URL, {"name": query, "count": 8, "language": language, "format": "json"})
        results = []
        for r in raw.get("results") or []:
            if r.get("latitude") is None or r.get("longitude") is None:
                continue
            region = ", ".join(x for x in (r.get("admin1"), r.get("country")) if x and x != r.get("name"))
            results.append({
                "name": r.get("name", ""),
                "region": region,
                "lat": round(r["latitude"], 4),
                "lon": round(r["longitude"], 4),
                "timezone": r.get("timezone"),
            })
        return results

    def _search_nominatim(self, query, language):
        raw = self._fetch(NOMINATIM_URL, {"q": query, "format": "jsonv2", "limit": 8,
                                          "addressdetails": 1, "accept-language": language})
        results = []
        for r in raw if isinstance(raw, list) else []:
            address = r.get("address") or {}
            name = next((address[f] for f in ("city", "town", "village", "hamlet", "municipality") if address.get(f)),
                        r.get("name") or "")
            region = ", ".join(x for x in (address.get("state"), address.get("country")) if x and x != name)
            try:
                results.append({"name": name, "region": region, "lat": round(float(r["lat"]), 4),
                                "lon": round(float(r["lon"]), 4), "timezone": None})
            except (KeyError, ValueError):
                continue
        return results


def fake_metno(lat, lon, now=None):
    """Правдоподобный ответ MET Norway: по часам на 60 часов, дальше по 6."""
    import math
    from datetime import datetime, timezone as tzone

    now = int(time.time() if now is None else now)
    start = now - now % 3600
    times = [start + i * 3600 for i in range(60)] + [start + 60 * 3600 + i * 6 * 3600 for i in range(30)]
    series = []
    for i, t in enumerate(times):
        local_h = ((t + 3 * 3600) % 86400) / 3600
        temp = round(8 + 4 * math.sin((local_h - 9) / 24 * 2 * math.pi) - (t - start) / 86400 * 0.5, 1)
        rain = 0.6 if 2 <= (t - start) / 3600 <= 5 else 0.0
        night = "_night" if local_h < 7 or local_h >= 18 else "_day"
        data = {"instant": {"details": {
            "air_temperature": temp, "air_pressure_at_sea_level": 1008.4, "cloud_area_fraction": 80.0,
            "relative_humidity": 85.0, "wind_from_direction": 240.0, "wind_speed": 5.1, "wind_speed_of_gust": 11.0,
            "ultraviolet_index_clear_sky": 0.4}}}
        sym = "rain" if rain else ("partlycloudy" + night)
        data["next_6_hours"] = {"summary": {"symbol_code": sym}, "details": {
            "air_temperature_max": temp + 1, "air_temperature_min": temp - 1,
            "precipitation_amount": rain * 3, "probability_of_precipitation": 70.0 if rain else 10.0}}
        if i < 60:
            data["next_1_hours"] = {"summary": {"symbol_code": sym}, "details": {
                "precipitation_amount": rain, "probability_of_precipitation": 80.0 if rain else 5.0}}
        series.append({"time": datetime.fromtimestamp(t, tzone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data})
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat, 10]},
            "properties": {"meta": {"units": {}}, "timeseries": series}}


def fake_fetch(url, params, headers=None):
    """Правдоподобный ответ Open-Meteo без сети (WALLHUB_FAKE_WEATHER=1):
    для разработки интерфейса и скриншотов. Дождь начинается минут через 30.
    WALLHUB_FAKE_WEATHER=metno: Open-Meteo «недоступен», ответ даёт MET Norway."""
    import math
    import os

    if url == metno.URL:
        return fake_metno(params["lat"], params["lon"])
    if os.environ.get("WALLHUB_FAKE_WEATHER") == "metno" and url in (FORECAST_URL, GEOCODING_URL):
        raise OSError("timed out")
    if url == NOMINATIM_URL:
        return [{"lat": "59.7227", "lon": "30.4136", "name": "Пушкин",
                 "address": {"town": "Пушкин", "state": "Санкт-Петербург", "country": "Россия"}}]

    if url == GEOCODING_URL:
        return {"results": [
            {"name": "Москва", "latitude": 55.7522, "longitude": 37.6156, "country": "Россия", "admin1": "Москва", "timezone": "Europe/Moscow"},
            {"name": "Мосальск", "latitude": 54.4927, "longitude": 34.9871, "country": "Россия", "admin1": "Калужская область", "timezone": "Europe/Moscow"},
        ]}
    now = int(time.time())
    offset = 3 * 3600
    hour0 = now - now % 3600
    day0 = (now + offset) - (now + offset) % 86400 - offset

    def temp_at(t):
        local_h = ((t + offset) % 86400) / 3600
        day = (t - day0) // 86400
        return 9 + 5 * math.sin((local_h - 9) / 24 * 2 * math.pi) - day * 0.8 + (day % 3)

    hours = [hour0 + i * 3600 for i in range(-1, HOURS + 2)]
    rain = lambda t: 0.0 if t < now + 1800 or t > now + 4 * 3600 else round(0.4 + 1.6 * math.sin((t - now - 1800) / 9000 * math.pi), 2)
    day_t = lambda t: 1 if 7 <= ((t + offset) % 86400) / 3600 < 18 else 0
    code = lambda t: 61 if rain(t) > 0.3 else (63 if rain(t) > 1 else (3 if t < now + 6 * 3600 else 2))
    quarters = [now - now % 900 + i * 900 for i in range(QUARTERS + 4)]
    days = [day0 + i * 86400 for i in range(DAYS)]
    day_codes = [61, 3, 2, 1, 0, 80, 71, 3, 2, 95]
    return {
        "latitude": params["latitude"], "longitude": params["longitude"],
        "timezone": "Europe/Moscow", "utc_offset_seconds": offset,
        "current": {"time": now, "temperature_2m": round(temp_at(now), 1), "apparent_temperature": round(temp_at(now) - 2.4, 1),
                    "relative_humidity_2m": 78, "is_day": day_t(now), "precipitation": 0.0, "weather_code": 3,
                    "pressure_msl": 1012.6, "wind_speed_10m": 4.2, "wind_direction_10m": 225, "wind_gusts_10m": 9.1, "cloud_cover": 88},
        "hourly": {
            "time": hours,
            "temperature_2m": [round(temp_at(t), 1) for t in hours],
            "precipitation_probability": [min(95, int(rain(t) * 50) + (10 if t > now + 20 * 3600 else 0)) for t in hours],
            "precipitation": [rain(t) for t in hours],
            "weather_code": [code(t) for t in hours],
            "is_day": [day_t(t) for t in hours],
            "uv_index": [round(max(0.0, 3 * math.sin((((t + offset) % 86400) / 3600 - 7) / 11 * math.pi)), 1) for t in hours],
        },
        "daily": {
            "time": days,
            "weather_code": day_codes[:DAYS],
            "temperature_2m_max": [round(max(temp_at(d + h * 3600) for h in range(24)), 1) for d in days],
            "temperature_2m_min": [round(min(temp_at(d + h * 3600) for h in range(24)), 1) for d in days],
            "precipitation_probability_max": [80, 20, 10, 5, 0, 60, 70, 30, 10, 85],
            "precipitation_sum": [3.4, 0.2, 0.0, 0.0, 0.0, 2.1, 4.0, 0.3, 0.0, 6.2],
            "sunrise": [d + 4 * 3600 + 25 * 60 for d in days],
            "sunset": [d + 14 * 3600 + 40 * 60 for d in days],
            "uv_index_max": [2.1, 3.0, 3.4, 3.8, 4.0, 2.2, 1.5, 2.8, 3.1, 1.9],
            "wind_speed_10m_max": [6.1, 5.0, 3.2, 2.8, 2.5, 7.4, 8.1, 4.4, 3.0, 9.5],
        },
        "minutely_15": {
            "time": quarters,
            "precipitation": [round(rain(t) / 4, 2) for t in quarters],
            "snowfall": [0.0 for _ in quarters],
        },
    }
