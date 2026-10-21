"""Запасной источник погоды: MET Norway (api.met.no), без ключа.

Нужен там, где Open-Meteo недоступен (например, его режет провайдер).
Ответ locationforecast/2.0 переводится в тот же вид, что отдаёт
weather.normalize: коды погоды WMO, unix-время, °C, м/с, гПа, мм.
Поминутного прогноза у MET Norway для наших широт нет: виджет осадков
тогда показывает почасовой.
"""

import math
from datetime import datetime, timedelta, timezone as dt_timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"

# Символы MET Norway (без суффикса _day/_night) -> код WMO.
SYMBOLS = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3, "fog": 45,
    "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
    "lightsleet": 66, "sleet": 66, "heavysleet": 67,
    "lightsleetshowers": 66, "sleetshowers": 66, "heavysleetshowers": 67,
    "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "lightsnowshowers": 85, "snowshowers": 86, "heavysnowshowers": 86,
}


def symbol_code(symbol):
    """'lightrainshowers_day' -> (80, True|False|None)."""
    if not symbol:
        return None, None
    name, _, suffix = symbol.partition("_")
    day = {"day": True, "night": False, "polartwilight": False}.get(suffix)
    if "thunder" in name:
        return 95, day
    return SYMBOLS.get(name, 3), day


# --- восход и закат (тот же упрощённый алгоритм NOAA, что в web/lib/theme.js) ---

RAD = math.pi / 180
J1970, J2000, J0 = 2440588, 2451545, 0.0009
OBLIQUITY = RAD * 23.4397
SUNRISE_ALT = RAD * -0.833


def sun_times(noon_utc, lat, lon):
    """Восход и закат (unix) для дня, в который попадает noon_utc (unix).
    Полярный день или ночь -> (None, None)."""
    lw = RAD * -lon
    phi = RAD * lat
    d = noon_utc / 86400 - 0.5 + J1970 - J2000
    n = round(d - J0 - lw / (2 * math.pi))
    ds = J0 + lw / (2 * math.pi) + n
    m = RAD * (357.5291 + 0.98560028 * ds)
    c = RAD * (1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m))
    lng = m + c + RAD * 102.9372 + math.pi
    dec = math.asin(math.sin(OBLIQUITY) * math.sin(lng))
    j_noon = J2000 + ds + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lng)
    cos_h = (math.sin(SUNRISE_ALT) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if not -1 <= cos_h <= 1:
        return None, None
    w = math.acos(cos_h)
    j_set = J2000 + J0 + (w + lw) / (2 * math.pi) + n + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lng)
    j_rise = j_noon - (j_set - j_noon)
    to_unix = lambda j: int((j + 0.5 - J1970) * 86400)
    return to_unix(j_rise), to_unix(j_set)


def _zone(tz):
    if ZoneInfo and tz:
        try:
            return ZoneInfo(tz)
        except Exception:
            pass
    return dt_timezone.utc


def _round(v, digits=1):
    return None if v is None else round(v, digits)


def normalize(raw, lat, lon, tz, now, hours=48, days=10):
    zone = _zone(tz)
    series = []
    for item in (raw.get("properties") or {}).get("timeseries") or []:
        try:
            t = int(datetime.fromisoformat(item["time"].replace("Z", "+00:00")).timestamp())
        except (KeyError, ValueError):
            continue
        series.append((t, item.get("data") or {}))
    series.sort(key=lambda x: x[0])
    if not series:
        raise ValueError("empty forecast")

    def local_day(t):
        return datetime.fromtimestamp(t, zone).date()

    sun_cache = {}

    def sun_for(day):
        if day not in sun_cache:
            noon = datetime(day.year, day.month, day.day, 12, tzinfo=zone).timestamp()
            sun_cache[day] = sun_times(noon, lat, lon)
        return sun_cache[day]

    def is_day(t, symbol_day):
        if symbol_day is not None:
            return symbol_day
        rise, sett = sun_for(local_day(t))
        if rise is None:
            return abs(lat) < 66 or (datetime.fromtimestamp(t, zone).month in (4, 5, 6, 7, 8, 9)) == (lat > 0)
        return rise <= t < sett

    # Текущий час: последняя точка не позже сейчас (или первая).
    cur_i = 0
    for i, (t, _) in enumerate(series):
        if t <= now:
            cur_i = i
    hourly = []
    for i, (t, data) in enumerate(series):
        if t + 3600 <= now or len(hourly) >= hours:
            continue
        if i + 1 < len(series) and series[i + 1][0] - t > 3600:
            break  # дальше шаг 6 часов
        inst = (data.get("instant") or {}).get("details") or {}
        nxt = data.get("next_1_hours") or {}
        code, day = symbol_code((nxt.get("summary") or {}).get("symbol_code"))
        details = nxt.get("details") or {}
        hourly.append({
            "time": t,
            "temp": _round(inst.get("air_temperature")),
            "code": code,
            "day": is_day(t, day),
            "pop": _round(details.get("probability_of_precipitation"), 0),
            "precip": _round(details.get("precipitation_amount")),
        })

    t0, d0 = series[cur_i]
    inst = (d0.get("instant") or {}).get("details") or {}
    nxt = d0.get("next_1_hours") or d0.get("next_6_hours") or {}
    code, day = symbol_code((nxt.get("summary") or {}).get("symbol_code"))
    temp = inst.get("air_temperature")
    wind = inst.get("wind_speed")
    humidity = inst.get("relative_humidity")
    current = {
        "time": t0,
        "temp": _round(temp),
        "feels": _round(feels_like(temp, wind, humidity)),
        "humidity": _round(humidity, 0),
        "precip": _round(((nxt.get("details") or {}).get("precipitation_amount"))),
        "code": code,
        "day": is_day(now, day),
        "pressure": _round(inst.get("air_pressure_at_sea_level")),
        "wind": _round(wind),
        "wind_dir": _round(inst.get("wind_from_direction"), 0),
        "gusts": _round(inst.get("wind_speed_of_gust")),
        "clouds": _round(inst.get("cloud_area_fraction"), 0),
        "uv": _round(inst.get("ultraviolet_index_clear_sky")),
    }

    # Дни: собираем по местной дате.
    by_day = {}
    for i, (t, data) in enumerate(series):
        day = local_day(t)
        agg = by_day.setdefault(day, {"temps": [], "pop": [], "precip": 0.0, "uv": [], "wind": [], "codes": []})
        inst = (data.get("instant") or {}).get("details") or {}
        if inst.get("air_temperature") is not None:
            agg["temps"].append(inst["air_temperature"])
        if inst.get("ultraviolet_index_clear_sky") is not None:
            agg["uv"].append(inst["ultraviolet_index_clear_sky"])
        if inst.get("wind_speed") is not None:
            agg["wind"].append(inst["wind_speed"])
        step = series[i + 1][0] - t if i + 1 < len(series) else 6 * 3600
        block = data.get("next_1_hours") if step <= 3600 else data.get("next_6_hours")
        block = block or data.get("next_6_hours") or data.get("next_1_hours") or {}
        details = block.get("details") or {}
        agg["precip"] += details.get("precipitation_amount") or 0.0
        if details.get("probability_of_precipitation") is not None:
            agg["pop"].append(details["probability_of_precipitation"])
        for key in ("air_temperature_max", "air_temperature_min"):
            if details.get(key) is not None:
                agg["temps"].append(details[key])
        code6, _ = symbol_code(((data.get("next_6_hours") or {}).get("summary") or {}).get("symbol_code"))
        local_hour = datetime.fromtimestamp(t, zone).hour
        if code6 is not None and 6 <= local_hour <= 12:
            agg["codes"].append(code6)
        elif code6 is not None:
            agg.setdefault("other_codes", []).append(code6)

    daily = []
    today = local_day(now)
    for day in sorted(by_day):
        if day < today or len(daily) >= days:
            continue
        agg = by_day[day]
        if not agg["temps"]:
            continue
        codes = agg["codes"] or agg.get("other_codes") or [3]
        midnight = int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())
        rise, sett = sun_for(day)
        daily.append({
            "time": midnight,
            "code": max(codes, key=_severity),
            "tmax": _round(max(agg["temps"])),
            "tmin": _round(min(agg["temps"])),
            "pop": _round(max(agg["pop"]), 0) if agg["pop"] else None,
            "precip": _round(agg["precip"]),
            "sunrise": rise,
            "sunset": sett,
            "uv": _round(max(agg["uv"])) if agg["uv"] else None,
            "wind": _round(max(agg["wind"])) if agg["wind"] else None,
        })

    offset = datetime.fromtimestamp(now, zone).utcoffset() or timedelta(0)
    return {
        "place": {"lat": lat, "lon": lon, "timezone": getattr(zone, "key", "UTC"),
                  "utc_offset": int(offset.total_seconds())},
        "current": current,
        "hourly": hourly,
        "daily": daily,
        "minutely": [],
        "source": "met.no",
    }


def _severity(code):
    # Для дня берём «самую заметную» погоду: гроза > снег > дождь > облака.
    order = [95, 86, 75, 73, 85, 71, 67, 66, 82, 65, 81, 63, 80, 61, 45, 3, 2, 1, 0]
    return len(order) - order.index(code) if code in order else 0


def feels_like(temp, wind, humidity):
    """Ощущаемая температура: ветро-холодовой индекс в холод, иначе
    упрощённый индекс жары по Стедману (как приблизительно у Open-Meteo)."""
    if temp is None:
        return None
    wind = wind or 0.0
    if temp <= 10 and wind > 1.3:
        v = (wind * 3.6) ** 0.16
        return 13.12 + 0.6215 * temp - 11.37 * v + 0.3965 * temp * v
    if humidity is None:
        return temp
    e = humidity / 100 * 6.105 * math.exp(17.27 * temp / (237.7 + temp))
    return temp + 0.33 * e - 0.70 * wind - 4.0
