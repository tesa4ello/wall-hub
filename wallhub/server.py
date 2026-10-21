"""HTTP-сервер: статика интерфейса и JSON API.

Один и тот же обработчик обслуживает два слушателя:
  * киоск — 127.0.0.1:8080, доверенный, без пароля;
  * админка — 0.0.0.0:8088, всё API только после входа по паролю.
"""

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

from . import BUILD, __version__, auth, status
from .network import SPEED_PROVIDERS, Network
from .update import CHANNELS, SCHEDULES, Updates, normalize_settings
from .system import CommandError

log = logging.getLogger(__name__)

COOKIE = "wallhub_session"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_BODY = 64 * 1024


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
        raise ApiError("Ожидается объект")
    rules = {
        "theme": lambda v: v in ("light", "dark"),
        "theme_mode": lambda v: v in ("manual", "schedule", "sun"),
        "latitude": lambda v: _number(v) and -90 <= v <= 90,
        "longitude": lambda v: _number(v) and -180 <= v <= 180,
        "night_dim": lambda v: _int(v) and 0 <= v <= 90,
        "blank_after_min": lambda v: _int(v) and 0 <= v <= 240,
        "day_start": lambda v: isinstance(v, str) and TIME_RE.match(v),
        "night_start": lambda v: isinstance(v, str) and TIME_RE.match(v),
        "dim_after_min": lambda v: _int(v) and 0 <= v <= 240,
        "dim_level": lambda v: _int(v) and 0 <= v <= 95,
        "language": lambda v: v in ("ru", "en"),
    }
    for key, value in patch.items():
        check = rules.get(key)
        if check is None:
            raise ApiError(f"Неизвестная настройка: {key}")
        if not check(value):
            raise ApiError(f"Недопустимое значение: {key}")
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
            self.snapshot = status.collect(self.config.get("statusbar"))
            time.sleep(self.interval)


class App:
    """Общее состояние, которое нужно обработчикам запросов."""

    def __init__(self, config, system, web_root, admin_port):
        self.config = config
        self.system = system
        self.web_root = os.path.realpath(web_root)
        self.admin_port = admin_port
        self.sessions = auth.Sessions()
        state_dir = os.path.dirname(os.path.abspath(config.path))
        self.updates = Updates(state_dir, system.dry_run)
        self.network = Network(state_dir, admin_port, system.dry_run)
        self.login_lock = threading.Lock()
        self.started = time.time()
        status.load_all()
        self.collector = StatusCollector(config)
        self.collector.start()

    def module_catalog(self):
        modules = []
        root = os.path.join(self.web_root, "modules")
        for name in sorted(os.listdir(root)):
            manifest = os.path.join(root, name, "module.json")
            if os.path.isfile(manifest):
                with open(manifest, encoding="utf-8") as f:
                    modules.append(dict(json.load(f), id=name))
        return modules

    def admin_url(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 53))
            ip = sock.getsockname()[0]
        except OSError:
            ip = socket.gethostname()
        finally:
            sock.close()
        return f"http://{ip}:{self.admin_port}/"


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

        def send_file(self, path):
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Интерфейс обновляется вместе с пакетом, кешировать его незачем.
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # --- запрос ----------------------------------------------------

        def read_json(self):
            if self.headers.get_content_type() != "application/json":
                # Заодно защищает от CSRF: обычная HTML-форма так не отправит.
                raise ApiError("Ожидается application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError("Слишком большой запрос", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                raise ApiError("Некорректный JSON")

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
            elif method in ("GET", "HEAD"):
                self.handle_static(url.path)
            else:
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)

        def handle_static(self, path):
            if path == "/":
                path = "/index.html" if trusted else "/admin/index.html"
            elif path.endswith("/"):
                path += "index.html"
            full = os.path.realpath(os.path.join(app.web_root, path.lstrip("/")))
            if not full.startswith(app.web_root + os.sep) or not os.path.isfile(full):
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                return
            self.send_file(full)

        def handle_api(self, method, path, query):
            route = ROUTES.get((method, path))
            if route is None:
                self.send_error_json("Not found", HTTPStatus.NOT_FOUND)
                return
            func, public = route
            if not public and not self.authorized():
                self.send_error_json("Нужно войти", HTTPStatus.UNAUTHORIZED)
                return
            try:
                body = self.read_json() if method == "POST" else None
                result = func(self, body=body, query=query)
            except ApiError as e:
                self.send_error_json(str(e), e.code)
                return
            except CommandError as e:
                self.send_error_json(str(e), HTTPStatus.BAD_GATEWAY)
                return
            except Exception:
                log.exception("API %s %s failed", method, path)
                self.send_error_json("Внутренняя ошибка", HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if isinstance(result, tuple):
                self.send_json(*result)
            else:
                self.send_json(result if result is not None else {"ok": True})

        # --- маршруты API ------------------------------------------------

        def api_session(self, **_):
            return {"authorized": self.authorized(), "trusted": trusted}

        def api_login(self, body, **_):
            if trusted:
                return {"ok": True}
            stored = app.config.get("admin", "password_hash")
            # Попытки входа выполняются строго по одной, с паузой после неудачи.
            with app.login_lock:
                if not stored or not auth.check_password(str(body.get("password", "")), stored):
                    time.sleep(auth.FAIL_DELAY)
                    raise ApiError("Неверный пароль", HTTPStatus.UNAUTHORIZED)
            token = app.sessions.create()
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={auth.SESSION_TTL}"
            return {"ok": True}, HTTPStatus.OK, {"Set-Cookie": cookie}

        def api_logout(self, **_):
            app.sessions.drop(self.session_token())
            return {"ok": True}, HTTPStatus.OK, {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"}

        def api_config(self, **_):
            data = app.config.public()
            if not trusted:
                # Начальный пароль виден только на самом экране.
                data["admin"].pop("initial_password", None)
            return data

        def api_revision(self, **_):
            # build: киоск перезагружает страницу, когда версия сменилась.
            # update: шестерёнка на экране показывает значок новой версии.
            return {
                "revision": app.config.revision,
                "build": BUILD.get("commit", ""),
                "update": app.updates.available(BUILD.get("commit", "")),
            }

        def api_settings(self, body, **_):
            patch = validate_ui(body.get("ui", {}))
            return {"revision": app.config.update({"ui": patch})}

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
                raise ApiError("Неизвестный тест скорости")
            if not app.network.start_speedtest(provider):
                raise ApiError("Тест скорости уже идёт", HTTPStatus.CONFLICT)
            return app.network.speed_status()

        def api_modules(self, **_):
            return {"modules": app.module_catalog(), "providers": status.catalog()}

        # Wi‑Fi

        def api_wifi(self, query, **_):
            return app.system.wifi_state(rescan=query.get("rescan") == ["1"])

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
            app.system.wifi_radio(bool(body.get("enabled")))

        # Питание

        def api_power(self, body, **_):
            app.system.power(body.get("action"))

        # Только админка (на киоске тоже работают, он доверенный)

        def api_admin_password(self, body, **_):
            new = str(body.get("new", ""))
            if len(new) < 6:
                raise ApiError("Пароль должен быть не короче 6 символов")
            if not trusted:
                stored = app.config.get("admin", "password_hash")
                if not auth.check_password(str(body.get("current", "")), stored):
                    raise ApiError("Текущий пароль указан неверно", HTTPStatus.FORBIDDEN)
            app.config.update({"admin": {"password_hash": auth.hash_password(new), "initial_password": ""}})
            app.sessions.clear()
            token = app.sessions.create()
            cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={auth.SESSION_TTL}"
            return {"ok": True}, HTTPStatus.OK, {"Set-Cookie": cookie}

        def api_time(self, **_):
            return {"timezone": app.system.timezone(), "timezones": app.system.timezones()}

        def api_admin_timezone(self, body, **_):
            app.system.set_timezone(str(body.get("timezone", "")))

        def api_admin_modules(self, body, **_):
            known = {m["id"] for m in app.module_catalog()}
            modules = body.get("modules")
            if not isinstance(modules, list):
                raise ApiError("Ожидается список модулей")
            clean = []
            for item in modules:
                if not isinstance(item, dict) or item.get("id") not in known:
                    raise ApiError("Неизвестный модуль")
                options = item.get("options", {})
                if not isinstance(options, dict):
                    raise ApiError("Некорректные параметры модуля")
                clean.append({"id": item["id"], "enabled": bool(item.get("enabled", True)), "options": options})
            app.config.set(("modules",), clean)

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
                    raise ApiError("Неизвестный канал обновлений")
                patch["channel"] = body["channel"]
            if "auto" in body:
                patch["auto"] = bool(body["auto"])
            if "schedule" in body:
                if body["schedule"] not in SCHEDULES:
                    raise ApiError("Неизвестное расписание")
                patch["schedule"] = body["schedule"]
            if "time" in body:
                if not isinstance(body["time"], str) or not TIME_RE.fullmatch(body["time"]):
                    raise ApiError("Время в формате ЧЧ:ММ")
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
                raise ApiError("Это не похоже на токен GitHub")
            app.updates.set_token(token)

        def api_update_run(self, body, **_):
            mode = body.get("mode")
            if mode not in ("check", "install", "rollback"):
                raise ApiError("Неизвестное действие")
            try:
                app.updates.start(mode)
            except subprocess.SubprocessError as e:
                raise ApiError(f"Не удалось запустить обновление: {e}", HTTPStatus.BAD_GATEWAY)

        def api_admin_statusbar(self, body, **_):
            items = body.get("items")
            if not isinstance(items, list) or any(i not in status.PROVIDERS for i in items):
                raise ApiError("Неизвестный пункт статусной строки")
            app.config.set(("statusbar",), items)

    H = Handler
    # (метод, путь) -> (функция, доступна ли без входа)
    ROUTES = {
        ("GET", "/session"): (H.api_session, True),
        ("POST", "/login"): (H.api_login, True),
        ("POST", "/logout"): (H.api_logout, True),
        ("GET", "/config"): (H.api_config, False),
        ("GET", "/revision"): (H.api_revision, False),
        ("POST", "/settings"): (H.api_settings, False),
        ("GET", "/status"): (H.api_status, False),
        ("GET", "/info"): (H.api_info, False),
        ("GET", "/modules"): (H.api_modules, False),
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
        ("GET", "/time"): (H.api_time, False),
        ("POST", "/admin/timezone"): (H.api_admin_timezone, False),
        ("POST", "/admin/modules"): (H.api_admin_modules, False),
        ("POST", "/admin/statusbar"): (H.api_admin_statusbar, False),
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
