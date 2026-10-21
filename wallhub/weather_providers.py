"""Провайдеры погоды с ключом: OpenWeatherMap, WeatherAPI.com, Яндекс Погода.

Каждый переводит ответ своего API в общий вид прогноза (см. weather.normalize):
коды погоды WMO, unix-время, °C, м/с, гПа, мм. Без ключа работают Open-Meteo
(weather.py) и MET Norway (metno.py); список всех провайдеров — PROVIDERS.
"""

from datetime import datetime, timedelta, timezone as dt_timezone

from . import metno

# id -> описание для админки. min_ttl: не чаще (секунды), чтобы уложиться в
# бесплатный лимит запросов.
PROVIDERS = {
    "open-meteo": {
        "title": "Open-Meteo", "key": False, "min_ttl": 0, "site": "https://open-meteo.com",
        "note": "Без ключа, поминутные осадки. Бесплатно для некоммерческого использования (CC BY 4.0). Бывает недоступен у российских провайдеров.",
    },
    "met.no": {
        "title": "MET Norway", "key": False, "min_ttl": 0, "site": "https://api.met.no",
        "note": "Без ключа, метеослужба Норвегии, весь мир. Бесплатно (CC BY 4.0).",
    },
    "openweathermap": {
        "title": "OpenWeatherMap", "key": True, "min_ttl": 0, "site": "https://openweathermap.org/api",
        "note": "Ключ бесплатного тарифа: 1000 запросов в сутки, прогноз на 5 дней с шагом 3 часа.",
    },
    "weatherapi": {
        "title": "WeatherAPI.com", "key": True, "min_ttl": 0, "site": "https://www.weatherapi.com",
        "note": "Ключ бесплатного тарифа: 1 млн запросов в месяц, прогноз на 3 дня по часам.",
    },
    "yandex": {
        "title": "Яндекс Погода", "key": True, "min_ttl": 30 * 60, "site": "https://yandex.ru/pogoda/b2b",
        "note": "Ключ API Яндекс Погоды (платный, есть пробный период). Запрос не чаще раза в 30 минут.",
    },
}
DEFAULT_ORDER = list(PROVIDERS)


def _round(v, digits=1):
    return None if v is None else round(v, digits)


def _zone(tz, offset=None):
    """Часовой пояс по имени, иначе по смещению (Etc/GMT±N для целых часов)."""
    zone = metno._zone(tz) if tz else None
    if zone is not None and zone is not dt_timezone.utc:
        return zone, tz
    if offset is not None and offset % 3600 == 0:
        name = "UTC" if offset == 0 else f"Etc/GMT{-offset // 3600:+d}"
        return metno._zone(name), name
    if offset is not None:
        return dt_timezone(timedelta(seconds=offset)), "UTC"
    return dt_timezone.utc, "UTC"


def _place(lat, lon, zone, name, now):
    offset = datetime.fromtimestamp(now, zone).utcoffset() or timedelta(0)
    return {"lat": lat, "lon": lon, "timezone": name, "utc_offset": int(offset.total_seconds())}


def _midnight(t, zone):
    d = datetime.fromtimestamp(t, zone).date()
    return d, int(datetime(d.year, d.month, d.day, tzinfo=zone).timestamp())


def _daily_from(entries, zone, lat, lon, now, days, codes_pick=None):
    """Дни из почасовых/трёхчасовых точек: {time, temp, code, pop, precip, uv, wind}."""
    by_day = {}
    for e in entries:
        day, midnight = _midnight(e["time"], zone)
        agg = by_day.setdefault(day, {"time": midnight, "temps": [], "pop": [], "precip": 0.0,
                                      "uv": [], "wind": [], "codes": []})
        if e.get("temp") is not None:
            agg["temps"].append(e["temp"])
        if e.get("pop") is not None:
            agg["pop"].append(e["pop"])
        agg["precip"] += e.get("precip") or 0.0
        if e.get("uv") is not None:
            agg["uv"].append(e["uv"])
        if e.get("wind") is not None:
            agg["wind"].append(e["wind"])
        hour = datetime.fromtimestamp(e["time"], zone).hour
        if e.get("code") is not None:
            agg["codes"].append((9 <= hour <= 18, e["code"]))
    today = datetime.fromtimestamp(now, zone).date()
    daily = []
    for day in sorted(by_day):
        agg = by_day[day]
        if day < today or not agg["temps"] or len(daily) >= days:
            continue
        codes = [c for daytime, c in agg["codes"] if daytime] or [c for _, c in agg["codes"]] or [3]
        rise, sett = metno.sun_times(agg["time"] + 12 * 3600, lat, lon)
        daily.append({
            "time": agg["time"], "code": max(codes, key=metno._severity),
            "tmax": _round(max(agg["temps"])), "tmin": _round(min(agg["temps"])),
            "pop": _round(max(agg["pop"]), 0) if agg["pop"] else None,
            "precip": _round(agg["precip"]), "sunrise": rise, "sunset": sett,
            "uv": _round(max(agg["uv"])) if agg["uv"] else None,
            "wind": _round(max(agg["wind"])) if agg["wind"] else None,
        })
    return daily


# --- OpenWeatherMap (бесплатные 2.5/weather и 2.5/forecast) -------------------------

OWM_WEATHER = "https://api.openweathermap.org/data/2.5/weather"
OWM_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"


def owm_code(code):
    if code is None:
        return None
    if 200 <= code < 300:
        return 95
    if 300 <= code < 400:
        return {300: 51, 301: 53, 302: 55}.get(code, 53)
    if 500 <= code < 600:
        return {500: 61, 501: 63, 502: 65, 503: 65, 504: 65, 511: 66,
                520: 80, 521: 81, 522: 82, 531: 82}.get(code, 63)
    if 600 <= code < 700:
        return {600: 71, 601: 73, 602: 75, 620: 85, 621: 86, 622: 86}.get(code, 66 if code < 620 else 73)
    if 700 <= code < 800:
        return 45
    return {800: 0, 801: 1, 802: 2}.get(code, 3)


def openweathermap(fetch, lat, lon, tz, key, now, hours, days):
    params = {"lat": lat, "lon": lon, "appid": key, "units": "metric", "lang": "ru"}
    cur = fetch(OWM_WEATHER, params)
    fc = fetch(OWM_FORECAST, params)
    zone, tz_name = _zone(tz, (fc.get("city") or {}).get("timezone", cur.get("timezone")))
    w = (cur.get("weather") or [{}])[0]
    main = cur.get("main") or {}
    wind = cur.get("wind") or {}
    rain = (cur.get("rain") or {}).get("1h", 0) + (cur.get("snow") or {}).get("1h", 0)
    current = {
        "time": cur.get("dt", int(now)), "temp": _round(main.get("temp")), "feels": _round(main.get("feels_like")),
        "humidity": main.get("humidity"), "precip": _round(rain), "code": owm_code(w.get("id")),
        "day": str(w.get("icon", "d")).endswith("d"),
        "pressure": _round(main.get("sea_level") or main.get("pressure")),
        "wind": _round(wind.get("speed")), "wind_dir": wind.get("deg"), "gusts": _round(wind.get("gust")),
        "clouds": (cur.get("clouds") or {}).get("all"), "uv": None,
    }
    entries = []
    for item in fc.get("list") or []:
        t = item.get("dt")
        iw = (item.get("weather") or [{}])[0]
        amount = (item.get("rain") or {}).get("3h", 0) + (item.get("snow") or {}).get("3h", 0)
        entries.append({
            "time": t, "temp": _round((item.get("main") or {}).get("temp")), "code": owm_code(iw.get("id")),
            "day": (item.get("sys") or {}).get("pod", "d") == "d",
            "pop": _round((item.get("pop") or 0) * 100, 0),
            # Осадки за 3 часа -> на час, как у остальных.
            "precip": _round(amount / 3), "wind": (item.get("wind") or {}).get("speed"),
        })
    hourly = [dict((k, e[k]) for k in ("time", "temp", "code", "day", "pop", "precip"))
              for e in entries if e["time"] + 3 * 3600 > now][:hours]
    for e in entries:
        e["precip"] = (e["precip"] or 0) * 3
    daily = _daily_from(entries, zone, lat, lon, now, days)
    sys_ = cur.get("sys") or {}
    if daily and sys_.get("sunrise"):
        daily[0]["sunrise"], daily[0]["sunset"] = sys_["sunrise"], sys_.get("sunset")
    return {"place": _place(lat, lon, zone, tz_name, now), "current": current, "hourly": hourly,
            "daily": daily, "minutely": [], "source": "openweathermap"}


# --- WeatherAPI.com ---------------------------------------------------------------------

WEATHERAPI_URL = "https://api.weatherapi.com/v1/forecast.json"

WEATHERAPI_CODES = {
    1000: 0, 1003: 2, 1006: 3, 1009: 3, 1030: 45, 1063: 80, 1066: 85, 1069: 66, 1072: 56,
    1087: 95, 1114: 73, 1117: 75, 1135: 45, 1147: 48, 1150: 51, 1153: 53, 1168: 56, 1171: 57,
    1180: 61, 1183: 61, 1186: 63, 1189: 63, 1192: 65, 1195: 65, 1198: 66, 1201: 67, 1204: 66,
    1207: 67, 1210: 71, 1213: 71, 1216: 73, 1219: 73, 1222: 75, 1225: 75, 1237: 77, 1240: 80,
    1243: 81, 1246: 82, 1249: 66, 1252: 67, 1255: 85, 1258: 86, 1261: 77, 1264: 77,
    1273: 95, 1276: 95, 1279: 95, 1282: 95,
}


def _clock_on(day, text, zone):
    """'07:12 AM' в местную дату -> unix."""
    try:
        t = datetime.strptime(text.strip(), "%I:%M %p")
    except (AttributeError, ValueError):
        return None
    return int(datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=zone).timestamp())


def weatherapi(fetch, lat, lon, tz, key, now, hours, days):
    raw = fetch(WEATHERAPI_URL, {"key": key, "q": f"{lat},{lon}", "days": days, "aqi": "no", "alerts": "no"})
    if raw.get("error"):
        raise ValueError(raw["error"].get("message", "error"))
    zone, tz_name = _zone((raw.get("location") or {}).get("tz_id") or tz)
    c = raw.get("current") or {}
    kph = lambda v: None if v is None else round(v / 3.6, 1)
    current = {
        "time": c.get("last_updated_epoch", int(now)), "temp": _round(c.get("temp_c")),
        "feels": _round(c.get("feelslike_c")), "humidity": c.get("humidity"), "precip": _round(c.get("precip_mm")),
        "code": WEATHERAPI_CODES.get((c.get("condition") or {}).get("code"), 3), "day": bool(c.get("is_day", 1)),
        "pressure": _round(c.get("pressure_mb")), "wind": kph(c.get("wind_kph")), "wind_dir": c.get("wind_degree"),
        "gusts": kph(c.get("gust_kph")), "clouds": c.get("cloud"), "uv": _round(c.get("uv")),
    }
    hourly, daily = [], []
    for fd in (raw.get("forecast") or {}).get("forecastday") or []:
        for h in fd.get("hour") or []:
            t = h.get("time_epoch")
            if t is None or t + 3600 <= now or len(hourly) >= hours:
                continue
            hourly.append({
                "time": t, "temp": _round(h.get("temp_c")),
                "code": WEATHERAPI_CODES.get((h.get("condition") or {}).get("code"), 3),
                "day": bool(h.get("is_day", 1)),
                "pop": max(h.get("chance_of_rain") or 0, h.get("chance_of_snow") or 0),
                "precip": _round(h.get("precip_mm")),
            })
        d = fd.get("day") or {}
        try:
            date = datetime.strptime(fd["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if date < datetime.fromtimestamp(now, zone).date():
            continue
        astro = fd.get("astro") or {}
        daily.append({
            "time": int(datetime(date.year, date.month, date.day, tzinfo=zone).timestamp()),
            "code": WEATHERAPI_CODES.get((d.get("condition") or {}).get("code"), 3),
            "tmax": _round(d.get("maxtemp_c")), "tmin": _round(d.get("mintemp_c")),
            "pop": max(d.get("daily_chance_of_rain") or 0, d.get("daily_chance_of_snow") or 0),
            "precip": _round(d.get("totalprecip_mm")),
            "sunrise": _clock_on(date, astro.get("sunrise"), zone), "sunset": _clock_on(date, astro.get("sunset"), zone),
            "uv": _round(d.get("uv")), "wind": kph(d.get("maxwind_kph")),
        })
    return {"place": _place(lat, lon, zone, tz_name, now), "current": current, "hourly": hourly,
            "daily": daily[:days], "minutely": [], "source": "weatherapi"}


# --- Яндекс Погода (REST API v2) ------------------------------------------------------------

YANDEX_URL = "https://api.weather.yandex.ru/v2/forecast"

YANDEX_CONDITIONS = {
    "clear": 0, "partly-cloudy": 2, "cloudy": 3, "overcast": 3, "fog": 45,
    "drizzle": 51, "light-rain": 61, "rain": 63, "moderate-rain": 63, "heavy-rain": 65,
    "continuous-heavy-rain": 65, "showers": 81, "wet-snow": 66, "light-snow": 71, "snow": 73,
    "snow-showers": 86, "hail": 96, "thunderstorm": 95, "thunderstorm-with-rain": 95,
    "thunderstorm-with-hail": 96,
}
WIND_DIRS = {"n": 0, "ne": 45, "e": 90, "se": 135, "s": 180, "sw": 225, "w": 270, "nw": 315}


def _yandex_pressure(item):
    if item.get("pressure_pa") is not None:
        return item["pressure_pa"]
    if item.get("pressure_mm") is not None:
        return round(item["pressure_mm"] / 0.750062, 1)
    return None


def yandex(fetch, lat, lon, tz, key, now, hours, days):
    raw = fetch(YANDEX_URL, {"lat": lat, "lon": lon, "limit": min(days, 7), "hours": "true", "extra": "false"},
                headers={"X-Yandex-Weather-Key": key})
    tzinfo = (raw.get("info") or {}).get("tzinfo") or {}
    zone, tz_name = _zone(tzinfo.get("name") or tz, tzinfo.get("offset"))
    f = raw.get("fact") or {}
    code = lambda c: YANDEX_CONDITIONS.get(c, 3)
    current = {
        "time": f.get("obs_time", raw.get("now", int(now))), "temp": f.get("temp"), "feels": f.get("feels_like"),
        "humidity": f.get("humidity"), "precip": None, "code": code(f.get("condition")),
        "day": f.get("daytime", "d") == "d", "pressure": _round(_yandex_pressure(f)),
        "wind": _round(f.get("wind_speed")), "wind_dir": WIND_DIRS.get(f.get("wind_dir")),
        "gusts": _round(f.get("wind_gust")),
        "clouds": None if f.get("cloudness") is None else round(f["cloudness"] * 100),
        "uv": f.get("uv_index"),
    }
    hourly, daily = [], []
    for fc in raw.get("forecasts") or []:
        for h in fc.get("hours") or []:
            t = h.get("hour_ts")
            if t is None or t + 3600 <= now or len(hourly) >= hours:
                continue
            hourly.append({
                "time": t, "temp": h.get("temp"), "code": code(h.get("condition")),
                "day": h.get("daytime", "d") == "d", "pop": h.get("prec_prob"), "precip": _round(h.get("prec_mm")),
            })
        parts = fc.get("parts") or {}
        named = [parts[p] for p in ("night", "morning", "day", "evening") if isinstance(parts.get(p), dict)]
        temps = [v for p in named for v in (p.get("temp_min"), p.get("temp_max"), p.get("temp")) if v is not None]
        t0 = fc.get("date_ts")
        if t0 is None or not temps or t0 + 86400 <= now:
            continue
        date = datetime.fromtimestamp(t0, zone).date()
        hm = lambda s: (int(datetime(date.year, date.month, date.day, *map(int, s.split(":")), tzinfo=zone).timestamp())
                        if isinstance(s, str) and ":" in s else None)
        day_part = parts.get("day") or parts.get("day_short") or {}
        daily.append({
            "time": t0, "code": code(day_part.get("condition")),
            "tmax": max(temps), "tmin": min(temps),
            "pop": max((p.get("prec_prob") or 0 for p in named), default=None),
            "precip": _round(sum(p.get("prec_mm") or 0 for p in named)),
            "sunrise": hm(fc.get("sunrise")), "sunset": hm(fc.get("sunset")),
            "uv": day_part.get("uv_index"), "wind": max((p.get("wind_speed") or 0 for p in named), default=None),
        })
    return {"place": _place(lat, lon, zone, tz_name, now), "current": current, "hourly": hourly,
            "daily": daily[:days], "minutely": [], "source": "yandex"}


FETCHERS = {"openweathermap": openweathermap, "weatherapi": weatherapi, "yandex": yandex}
