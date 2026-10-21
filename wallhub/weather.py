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

from .i18n import tr

log = logging.getLogger(__name__)

USER_AGENT = "WallHub (+https://github.com/tesa4ello/wall-hub)"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

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


def get_json(url, params, timeout=6, limit=2 * 1024 * 1024):
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
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
    def __init__(self, fetch=get_json, clock=time.time):
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._key_locks = {}
        # key -> {"data", "at", "failed_at"}
        self._forecasts = {}
        self._searches = {}

    def _key_lock(self, key):
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def forecast(self, lat, lon):
        key = place_key(lat, lon)
        with self._key_lock(key):
            entry = self._forecasts.get(key)
            now = self._clock()
            if entry and "data" in entry and now - entry["at"] < FORECAST_TTL:
                return self._result(entry, now)
            if entry and now - entry.get("failed_at", 0) < RETRY_AFTER:
                if "data" not in entry:
                    raise WeatherError(entry["error"])
                return self._result(entry, now)
            try:
                raw = self._fetch(FORECAST_URL, {
                    "latitude": key[0], "longitude": key[1],
                    "current": CURRENT, "hourly": HOURLY, "daily": DAILY,
                    "minutely_15": MINUTELY, "forecast_minutely_15": QUARTERS + 4,
                    "forecast_days": DAYS, "timezone": "auto", "timeformat": "unixtime",
                    "wind_speed_unit": "ms",
                })
                if raw.get("error"):
                    raise WeatherError(raw.get("reason") or "error")
                entry = {"data": normalize(raw, now), "at": now}
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

    def _result(self, entry, now):
        stale = now - entry["at"] >= FORECAST_TTL + RETRY_AFTER
        return dict(entry["data"], updated=entry["at"], stale=stale)

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
            raw = self._fetch(GEOCODING_URL, {"name": query, "count": 8, "language": language, "format": "json"})
        except Exception as e:
            log.warning("city search failed: %s", e)
            raise WeatherError(tr("Поиск города не удался: {error}", error=e))
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
        self._store(self._searches, key, {"data": results, "at": now})
        return results


def fake_fetch(url, params):
    """Правдоподобный ответ Open-Meteo без сети (WALLHUB_FAKE_WEATHER=1):
    для разработки интерфейса и скриншотов. Дождь начинается минут через 30."""
    import math

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
