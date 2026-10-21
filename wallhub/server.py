"""HTTP-сервер: статика интерфейса и JSON API.

Один и тот же обработчик обслуживает два слушателя:
  * киоск — 127.0.0.1:8080, доверенный, без пароля;
  * админка — 0.0.0.0:80, всё API только после входа по паролю. Пока
    пароль не задан, первый вошедший в админку его придумывает.
"""

import base64
import binascii
import json
import logging
import mimetypes
import os
import re
import socket
import subprocess
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import BUILD, __version__, auth, desktops, i18n, status
from .background import ID_RE as BG_ID_RE, BackgroundError, Backgrounds
from .modules import Registry
from .location import MODES as LOCATION_MODES, LocationError, Locator
from .i18n import tr
from .network import SPEED_PROVIDERS, Network
from .update import CHANNELS, SCHEDULES, Updates, normalize_settings
from .system import CommandError
from .weather import Weather, WeatherError, fake_fetch

log = logging.getLogger(__name__)

COOKIE = "wallhub_session"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_BODY = 64 * 1024
# Загрузка фона: две картинки в base64 внутри JSON.
MAX_UPLOAD = 16 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, message, code=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.code = code


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_ui(patch):
    """Проверяет экранные настройки, пришедшие из браузера."""
    if not isinstance(patch, dict):
        raise ApiError(tr("Ожидается объект"))
    rules = {
        "theme": lambda v: v in ("light", "dark"),
        "theme_mode": lambda v: v in ("manual", "schedule", "sun"),
        "latitude": lambda v: _number(v) and -90 <= v <= 90,
        "longitude": lambda v: _number(v) and -180 <= v <= 180,
        "location_mode": lambda v: v in LOCATION_MODES,
        "location_name": lambda v: isinstance(v, str) and len(v) <= 80,
        "night_dim": lambda v: _int(v) and 0 <= v <= 90,
        "blank_after_min": lambda v: _int(v) and 0 <= v <= 240,
        "day_start": lambda v: isinstance(v, str) and TIME_RE.match(v),
        "night_start": lambda v: isinstance(v, str) and TIME_RE.match(v),
        "dim_after_min": lambda v: _int(v) and 0 <= v <= 240,
        "dim_level": lambda v: _int(v) and 0 <= v <= 95,
        "language": lambda v: v in i18n.languages(),
        "background": lambda v: v == "" or (isinstance(v, str) and BG_ID_RE.match(v)),
        "background_dark": lambda v: v in ("", "same") or (isinstance(v, str) and BG_ID_RE.match(v)),
        "bg_dim": lambda v: _int(v) and 0 <= v <= 80,
        "tiles": lambda v: v in ("baked", "blur", "frosted", "solid"),
    }
    for key, value in patch.items():
        check = rules.get(key)
        if check is None:
            raise ApiError(tr("Неизвестная настройка: {key}", key=key))
        if not check(value):
            raise ApiError(tr("Недопустимое значение: {key}", key=key))
    return patch


class StatusCollector(threading.Thread):
    """Опрашивает провайдеров статусной строки в фоне раз в interval секунд.

    Провайдеры считают скорость по разнице показаний, поэтому их вызывает
    только этот поток, а клиенты получают готовый снимок.
    """

    def __init__(self, config, interval=2.0):
        super().__init__(daemon=True, name="status")
        self.config = config
        self.interval = interval
        self.snapshot = []

    def run(self):
        while True:
            # Опрашиваем только пункты, которые показывает хоть один виджет.
            self.snapshot = status.collect(desktops.used_providers(self.config.get("desktops")))
            time.sleep(self.interval)


class WifiWatchdog(threading.Thread):
    """Включает Wi‑Fi обратно, если он выключен, а другой связи нет уже
    TIMEOUT секунд. Иначе случайно выключенный Wi‑Fi отрезает админку, а
    NetworkManager помнит это и после перезагрузки."""

    TIMEOUT = 10 * 60
    INTERVAL = 30

    def __init__(self, system):
        super().__init__(daemon=True, name="wifi-watchdog")
        self.system = system
        self.offline_since = None

    def check(self, now):
        try:
            cut_off = not self.system.radio_enabled() and not self.system.other_connection()
        except CommandError:
            return
        if not cut_off:
            self.offline_since = None
            return
        if self.offline_since is None:
            self.offline_since = now
        elif now - self.offline_since >= self.TIMEOUT:
            log.warning("Wi-Fi is off and there is no other connection: turning Wi-Fi back on")
            self.offline_since = None
            try:
                self.system.wifi_radio(True)
            except CommandError as e:
                log.warning("cannot turn Wi-Fi on: %s", e)

    def run(self):
        while True:
            self.check(time.monotonic())
            time.sleep(self.INTERVAL)


class App:
    """Общее состояние, которое нужно обработчикам запросов."""

    def __init__(self, config, system, web_root, admin_port, modules_dirs=()):
        self.config = config
        self.system = system
        self.web_root = os.path.realpath(web_root)
        self.admin_port = admin_port
        state_dir = os.path.dirname(os.path.abspath(config.path))
        self.sessions = auth.Sessions(os.path.join(state_dir, "sessions.json"))
        self.updates = Updates(state_dir, system.dry_run)
        self.network = Network(state_dir, admin_port, system.dry_run)
        self.backgrounds = Backgrounds(os.path.join(state_dir, "backgrounds"), os.path.join(self.web_root, "backgrounds"))
        self.login_lock = threading.Lock()
        # Тема, которую сейчас показывает экран (сообщает сам киоск), чтобы
        # админка выглядела так же.
        self.kiosk_theme = None
        self.started = time.time()
        self.modules = Registry(os.path.join(self.web_root, "modules"), modules_dirs,
                                os.path.join(state_dir, "modules"))
        i18n.load(self.web_root, self.modules.loaded())
        i18n.set_language(config.get("ui", "language"))
        status.load_all()
        self.collector = StatusCollector(config)
        self.collector.start()
        self.wifi_watchdog = WifiWatchdog(system)
        self.wifi_watchdog.start()
        self.locator = Locator(config, system)
        self.locator.start()
        # WALLHUB_FAKE_WEATHER=1: выдуманный прогноз без сети (для разработки).
        self.weather = Weather(fetch=fake_fetch) if os.environ.get("WALLHUB_FAKE_WEATHER") else Weather()

    def widget_catalog(self):
        return desktops.load_catalog(self.modules.loaded())

    def admin_url(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 53))
            ip = sock.getsockname()[0]
        except OSError:
            ip = socket.gethostname()
        finally:
            sock.close()
        port = "" if self.admin_port == 80 else f":{self.admin_port}"
        return f"http://{ip}{port}/"


def decode_image(value):
    """Картинка из data:-URL или голого base64."""
    if not isinstance(value, str) or not value:
        raise BackgroundError(tr("Нет картинки"))
    try:
        return base64.b64decode(value.partition(",")[2] if value.startswith("data:") else value, validate=True)
    except (binascii.Error, ValueError):
        raise BackgroundError(tr("Картинка повреждена"))


def check_new_password(body):
    new = str(body.get("new", ""))
    if len(new) < 6:
        raise ApiError(tr("Пароль должен быть не короче 6 символов"))
    return new


def make_handler(app, trusted, port):
    """Создаёт класс обработчика для одного слушателя."""

    allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"WallHub/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # --- ответы ----------------------------------------------------

        def send_json(self, data, code=HTTPStatus.OK, headers=None):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def send_error_json(self, message, code):
            self.send_json({"error": message}, code)

        def send_file(self, path, ctype=None, cache="no-cache"):
            ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Интерфейс обновляется вместе с пакетом, кешировать его незачем.
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # --- запрос ----------------------------------------------------

        def read_json(self, limit=MAX_BODY):
            if self.headers.get_content_type() != "application/json":
                # Заодно защищает от CSRF: обычная HTML-форма так не отправит.
                raise ApiError(tr("Ожидается application/json"), HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ApiError(tr("Слишком большой запрос"), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                raise ApiError(tr("Некорректный JSON"))

        def session_token(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get(COOKIE)
            return morsel.value if morsel else None

        def authorized(self):
            return trusted or app.sessions.valid(self.session_token())

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def dispatch(self, method):
            url = urlsplit(self.path)
            # Киоск слушает только localhost; проверка Host отсекает DNS rebinding.
            if trusted and self.headers.get("Host") not in allowed_hosts:
                self.send_error_json("Forbidden", HTTPStatus.FORBIDDEN)
                return
            if url.path.startswith("/api/"):
                self.handle_api(method, url.path[len("/api"):], parse_qs(url.query))
            elif method in ("GET", "HEAD") and url.path.startswith("/bg/"):
                self.handle_background(url.path[len("/bg/"):])
            elif method in ("GET", "HEAD"):
                self.handle_static(url.path)
            else:
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)

        def handle_static(self, path):
            # Файлы модулей: штатные, установленные и из --modules-dir.
            if path.startswith("/modules/"):
                _, _, rest = path.partition("/modules/")
                module_id, _, rel = rest.partition("/")
                full = app.modules.static_file(module_id, rel)
                if full is None:
                    self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                    return
                self.send_file(full)
                return
            if path == "/":
                path = "/index.html" if trusted else "/admin/index.html"
            elif path.endswith("/"):
                path += "index.html"
            full = os.path.realpath(os.path.join(app.web_root, path.lstrip("/")))
            if not full.startswith(app.web_root + os.sep) or not os.path.isfile(full):
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                return
            self.send_file(full)

        def handle_background(self, name):
            # В админке фоны (возможно, личные фото) видны только после входа.
            found = app.backgrounds.file(name) if self.authorized() else None
            if found is None:
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                return
            # Имя файла уникально для каждой загрузки, содержимое не меняется.
            self.send_file(found[0], found[1], cache="private, max-age=31536000, immutable")

        def handle_api(self, method, path, query):
            # Язык общий для экрана и админки: ошибки API приходят на нём же.
            i18n.set_language(app.config.get("ui", "language"))
            route = ROUTES.get((method, path))
            if route is None:
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                return
            func, public = route
            if not public and not self.authorized():
                self.send_error_json(tr("Нужно войти"), HTTPStatus.UNAUTHORIZED)
                return
            try:
                limit = MAX_UPLOAD if path == "/admin/background" else MAX_BODY
                body = self.read_json(limit) if method == "POST" else None
                result = func(self, body=body, query=query)
            except ApiError as e:
                self.send_error_json(str(e), e.code)
                return
            except CommandError as e:
                self.send_error_json(str(e), HTTPStatus.BAD_GATEWAY)
                return
            except Exception:
                log.exception("API %s %s failed", method, path)
                self.send_error_json(tr("Внутренняя ошибка"), HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if isinstance(result, tuple):
                self.send_json(*result)
            else:
                self.send_json(result if result is not None else {"ok": True})

        # --- маршруты API ------------------------------------------------

        def api_session(self, **_):
            return {
                "authorized": self.authorized(),
                "trusted": trusted,
                "setup": not app.config.get("admin", "password_hash"),
                "language": app.config.get("ui", "language"),
            }

        def start_session(self):
            token = app.sessions.create()
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={auth.SESSION_TTL}"
            return {"ok": True}, HTTPStatus.OK, {"Set-Cookie": cookie}

        def api_setup_password(self, body, **_):
            # Первый вход: пароля ещё нет, его задаёт тот, кто первым открыл админку.
            new = check_new_password(body)
            with app.login_lock:
                if app.config.get("admin", "password_hash"):
                    raise ApiError(tr("Пароль уже задан, войдите с ним"), HTTPStatus.CONFLICT)
                app.config.update({"admin": {"password_hash": auth.hash_password(new)}})
            app.sessions.clear()
            return self.start_session()

        def api_login(self, body, **_):
            if trusted:
                return {"ok": True}
            stored = app.config.get("admin", "password_hash")
            # Попытки входа выполняются строго по одной, с паузой после неудачи.
            with app.login_lock:
                if not stored or not auth.check_password(str(body.get("password", "")), stored):
                    time.sleep(auth.FAIL_DELAY)
                    raise ApiError(tr("Неверный пароль"), HTTPStatus.UNAUTHORIZED)
            return self.start_session()

        def api_logout(self, **_):
            app.sessions.drop(self.session_token())
            return {"ok": True}, HTTPStatus.OK, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"}

        def api_config(self, **_):
            return app.config.public()

        def api_theme(self, **_):
            # Публично: страница входа тоже показывается в теме и на языке панели.
            return {"theme": app.kiosk_theme, "language": app.config.get("ui", "language")}

        def api_revision(self, query, **_):
            theme = (query.get("theme") or [None])[0]
            if trusted and theme in ("dark", "light"):
                app.kiosk_theme = theme
            # build: киоск перезагружает страницу, когда версия сменилась.
            # update: шестерёнка на экране показывает значок новой версии.
            return {
                "revision": app.config.revision,
                "build": BUILD.get("commit", ""),
                "update": app.updates.available(BUILD.get("commit", "")),
                # progress: ход обновления, панель показывает его поверх столов.
                "progress": app.updates.progress(),
                "version": __version__,
            }

        def api_settings(self, body, **_):
            patch = validate_ui(body.get("ui", {}))
            for key in ("background", "background_dark"):
                if patch.get(key) not in (None, "", "same") and not app.backgrounds.exists(patch[key]):
                    raise ApiError(tr("Нет такого фона"))
            mode_changed = "location_mode" in patch and patch["location_mode"] != app.config.get("ui", "location_mode")
            revision = app.config.update({"ui": patch})
            if mode_changed:
                app.locator.wake()
            return {"revision": revision}

        # Местоположение панели

        def api_location(self, **_):
            return {
                "mode": app.config.get("ui", "location_mode"),
                "name": app.config.get("ui", "location_name"),
                "lat": app.config.get("ui", "latitude"),
                "lon": app.config.get("ui", "longitude"),
                "yandex_key_set": bool(app.config.get("location", "yandex_key")),
                "last": app.locator.last,
            }

        def api_location_detect(self, body, **_):
            mode = body.get("mode") or app.config.get("ui", "location_mode")
            if mode not in ("ip", "yandex"):
                raise ApiError(tr("Неизвестный способ определения места"))
            try:
                found = app.locator.locate(mode)
            except LocationError as e:
                raise ApiError(str(e), HTTPStatus.BAD_GATEWAY)
            return dict(found, revision=app.config.revision)

        def api_location_key(self, body, **_):
            key = str(body.get("key", "")).strip()
            if key and not re.fullmatch(r"[A-Za-z0-9-]{8,64}", key):
                raise ApiError(tr("Это не похоже на ключ API Яндекса"))
            app.config.update({"location": {"yandex_key": key}})
            if app.config.get("ui", "location_mode") == "yandex":
                app.locator.wake()

        # Модуль «Погода» (web/modules/weather)

        def api_weather_forecast(self, query, **_):
            lat, lon = (query.get(k, [None])[0] for k in ("lat", "lon"))
            name = None
            if lat is None and lon is None:
                ui = app.config.get("ui")
                lat, lon, name = ui["latitude"], ui["longitude"], ui.get("location_name", "")
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                raise ApiError(tr("Нужны координаты lat и lon"))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ApiError(tr("Координаты вне допустимого диапазона"))
            try:
                data = app.weather.forecast(lat, lon)
            except WeatherError as e:
                raise ApiError(str(e), HTTPStatus.BAD_GATEWAY)
            if name is not None:
                data["name"] = name
            return data

        def api_weather_search(self, query, **_):
            try:
                return {"results": app.weather.search((query.get("q") or [""])[0], app.config.get("ui", "language"))}
            except WeatherError as e:
                raise ApiError(str(e), HTTPStatus.BAD_GATEWAY)

        def api_status(self, **_):
            return {"items": app.collector.snapshot, "time": time.time()}

        def api_info(self, **_):
            return {
                "version": __version__,
                "build": BUILD,
                "hostname": app.system.hostname(),
                "admin_url": app.admin_url(),
                "uptime": time.time() - app.started,
                "dry_run": app.system.dry_run,
            }

        # Сеть

        def api_network(self, **_):
            local = app.network.local()
            return dict(local, admin=app.network.admin(local, app.admin_url()))

        def api_network_external(self, query, **_):
            return app.network.external(refresh=query.get("refresh") == ["1"])

        def api_speedtest(self, **_):
            return app.network.speed_status()

        def api_speedtest_run(self, body, **_):
            provider = body.get("provider")
            if provider not in SPEED_PROVIDERS:
                raise ApiError(tr("Неизвестный тест скорости"))
            if not app.network.start_speedtest(provider):
                raise ApiError(tr("Тест скорости уже идёт"), HTTPStatus.CONFLICT)
            return app.network.speed_status()

        def api_catalog(self, **_):
            found = app.modules.scan()
            loaded = {k: m for k, m in found.items() if not m.error}
            return {
                "widgets": list(desktops.load_catalog(loaded).values()),
                "providers": status.catalog(),
                "i18n": {code: i18n.module_catalog(loaded, code) for code in i18n.languages()},
                "modules": [m.info() for _, m in sorted(found.items())],
            }

        # Wi‑Fi

        def api_wifi(self, query, **_):
            state = app.system.wifi_state(rescan=query.get("rescan") == ["1"])
            # Нужно экрану, чтобы предупредить перед выключением Wi‑Fi.
            return dict(state, other_connection=app.system.other_connection())

        def api_wifi_connect(self, body, **_):
            app.system.wifi_connect(
                str(body.get("ssid", "")),
                str(body.get("password", "")),
                bool(body.get("hidden", False)),
            )
            if not app.config.get("setup", "wifi_done"):
                app.config.update({"setup": {"wifi_done": True}})

        def api_setup_wifi_done(self, **_):
            app.config.update({"setup": {"wifi_done": True}})

        def api_wifi_disconnect(self, **_):
            app.system.wifi_disconnect()

        def api_wifi_forget(self, body, **_):
            app.system.wifi_forget(str(body.get("ssid", "")))

        def api_wifi_radio(self, body, **_):
            enabled = bool(body.get("enabled"))
            app.system.wifi_radio(enabled)
            app.wifi_watchdog.offline_since = None if enabled else time.monotonic()

        # Питание

        def api_power(self, body, **_):
            app.system.power(body.get("action"))

        # Только админка (на киоске тоже работают, он доверенный)

        def api_admin_password(self, body, **_):
            new = check_new_password(body)
            if not trusted:
                stored = app.config.get("admin", "password_hash")
                if not auth.check_password(str(body.get("current", "")), stored):
                    raise ApiError(tr("Текущий пароль указан неверно"), HTTPStatus.FORBIDDEN)
            app.config.update({"admin": {"password_hash": auth.hash_password(new)}})
            app.sessions.clear()
            return self.start_session()

        def api_time(self, **_):
            return {"timezone": app.system.timezone(), "timezones": app.system.timezones()}

        def api_admin_timezone(self, body, **_):
            app.system.set_timezone(str(body.get("timezone", "")))

        def api_admin_desktops(self, body, **_):
            layout = app.config.get("layout")
            try:
                clean = desktops.validate(
                    body.get("desktops"), app.widget_catalog(), layout["cols"], layout["rows"],
                    providers=status.PROVIDERS.keys(),
                )
            except desktops.LayoutError as e:
                raise ApiError(str(e))
            minutes = body.get("home_after_min", layout["home_after_min"])
            if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 <= minutes <= 240:
                raise ApiError(tr("Возврат на первый стол: от 0 до 240 минут"))
            app.config.update({"desktops": clean, "layout": {"home_after_min": minutes}})
            return {"revision": app.config.revision, "desktops": clean}

        # Фон

        def api_backgrounds(self, **_):
            return {"items": app.backgrounds.list()}

        def api_admin_background(self, body, **_):
            try:
                image, blur = (decode_image(body.get(key)) for key in ("image", "blur"))
                bg_id = app.backgrounds.add(image, blur)
            except BackgroundError as e:
                raise ApiError(str(e))
            return {"id": bg_id, "items": app.backgrounds.list()}

        def api_admin_background_delete(self, body, **_):
            bg_id = body.get("id")
            try:
                app.backgrounds.delete(bg_id)
            except BackgroundError as e:
                raise ApiError(str(e))
            # Удалённый фон больше не выбран ни в одной теме.
            ui = app.config.get("ui")
            patch = {key: value for key, value in (("background", ""), ("background_dark", "same"))
                     if ui.get(key) == bg_id}
            if patch:
                app.config.update({"ui": patch})
            return {"items": app.backgrounds.list(), "revision": app.config.revision}

        # Обновления

        def api_update(self, **_):
            return {
                "settings": normalize_settings(app.config.get("update")),
                "available": app.updates.available(BUILD.get("commit", "")),
                "token_set": app.updates.token_set(),
                "status": app.updates.status(),
                "build": BUILD,
            }

        def api_update_settings(self, body, **_):
            patch = {}
            if "channel" in body:
                if body["channel"] not in CHANNELS:
                    raise ApiError(tr("Неизвестный канал обновлений"))
                patch["channel"] = body["channel"]
            if "auto" in body:
                patch["auto"] = bool(body["auto"])
            if "schedule" in body:
                if body["schedule"] not in SCHEDULES:
                    raise ApiError(tr("Неизвестное расписание"))
                patch["schedule"] = body["schedule"]
            if "time" in body:
                if not isinstance(body["time"], str) or not TIME_RE.fullmatch(body["time"]):
                    raise ApiError(tr("Время в формате ЧЧ:ММ"))
                patch["time"] = body["time"]
            channel_changed = "channel" in patch and patch["channel"] != normalize_settings(app.config.get("update"))["channel"]
            app.config.update({"update": patch})
            # Сменили канал — сразу узнаём, что в нём есть.
            if channel_changed:
                try:
                    app.updates.start("check")
                except (subprocess.SubprocessError, OSError) as e:
                    log.warning("update check after channel change failed: %s", e)

        def api_update_token(self, body, **_):
            token = str(body.get("token", "")).strip()
            if token and not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
                raise ApiError(tr("Это не похоже на токен GitHub"))
            app.updates.set_token(token)

        def api_update_run(self, body, **_):
            mode = body.get("mode")
            if mode not in ("check", "install", "rollback"):
                raise ApiError(tr("Неизвестное действие"))
            try:
                app.updates.start(mode)
            except subprocess.SubprocessError as e:
                raise ApiError(tr("Не удалось запустить обновление: {error}", error=e), HTTPStatus.BAD_GATEWAY)

    H = Handler
    # (метод, путь) -> (функция, доступна ли без входа)
    ROUTES = {
        ("GET", "/session"): (H.api_session, True),
        ("POST", "/login"): (H.api_login, True),
        ("POST", "/setup/password"): (H.api_setup_password, True),
        ("POST", "/logout"): (H.api_logout, True),
        ("GET", "/config"): (H.api_config, False),
        ("GET", "/revision"): (H.api_revision, False),
        ("GET", "/theme"): (H.api_theme, True),
        ("POST", "/settings"): (H.api_settings, False),
        ("GET", "/status"): (H.api_status, False),
        ("GET", "/info"): (H.api_info, False),
        ("GET", "/catalog"): (H.api_catalog, False),
        ("GET", "/network"): (H.api_network, False),
        ("GET", "/network/external"): (H.api_network_external, False),
        ("GET", "/speedtest"): (H.api_speedtest, False),
        ("POST", "/speedtest"): (H.api_speedtest_run, False),
        ("GET", "/wifi"): (H.api_wifi, False),
        ("POST", "/wifi/connect"): (H.api_wifi_connect, False),
        ("POST", "/wifi/disconnect"): (H.api_wifi_disconnect, False),
        ("POST", "/wifi/forget"): (H.api_wifi_forget, False),
        ("POST", "/wifi/radio"): (H.api_wifi_radio, False),
        ("POST", "/setup/wifi-done"): (H.api_setup_wifi_done, False),
        ("POST", "/power"): (H.api_power, False),
        ("POST", "/admin/password"): (H.api_admin_password, False),
        ("GET", "/location"): (H.api_location, False),
        ("POST", "/location/detect"): (H.api_location_detect, False),
        ("POST", "/location/key"): (H.api_location_key, False),
        ("GET", "/m/weather/forecast"): (H.api_weather_forecast, False),
        ("GET", "/m/weather/search"): (H.api_weather_search, False),
        ("GET", "/time"): (H.api_time, False),
        ("POST", "/admin/timezone"): (H.api_admin_timezone, False),
        ("POST", "/admin/desktops"): (H.api_admin_desktops, False),
        ("GET", "/backgrounds"): (H.api_backgrounds, False),
        ("POST", "/admin/background"): (H.api_admin_background, False),
        ("POST", "/admin/background/delete"): (H.api_admin_background_delete, False),
        ("GET", "/update"): (H.api_update, False),
        ("POST", "/update/settings"): (H.api_update_settings, False),
        ("POST", "/update/token"): (H.api_update_token, False),
        ("POST", "/update/run"): (H.api_update_run, False),
    }
    return Handler


def serve(app, bind, port, trusted):
    server = ThreadingHTTPServer((bind, port), make_handler(app, trusted, port))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name=f"http-{port}")
    thread.start()
    log.info("%s listening on %s:%d", "kiosk" if trusted else "admin", bind, port)
    return server
