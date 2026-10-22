"""Смоук-тест: ядро в dry-run поднимается, штатные модули и их API отвечают.

Ловит поломки вроде «маршруты погоды отдают 404» после изменений в server.py.
"""

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request

from wallhub.config import Config
from wallhub.server import App, serve
from wallhub.system import System

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        config = Config(os.path.join(cls.tmp.name, "config.json"))
        cls.app = App(config, System(dry_run=True), WEB, 0, [])
        # Порт нужен заранее: киоск проверяет заголовок Host (localhost:порт).
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.server = serve(cls.app, "127.0.0.1", port, trusted=True)
        cls.base = f"http://localhost:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.app.backends.stop_all()
        cls.app.module_updates.stop()
        cls.app.alarms.stop()
        cls.app.reminders.stop()
        cls.tmp.cleanup()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_pages(self):
        for path in ("/", "/admin/", "/app.js", "/admin/admin.js", "/i18n/en.json"):
            self.assertEqual(self.request(path)[0], 200, path)

    def test_builtin_modules(self):
        status, body = self.request("/api/catalog")
        self.assertEqual(status, 200)
        text = body.decode()
        for module in ("clock", "system", "weather"):
            self.assertIn(f'"{module}/', text, module)
            self.assertEqual(self.request(f"/modules/{module}/module.js")[0], 200, module)

    def test_core_api(self):
        for path in ("/api/config", "/api/info", "/api/display", "/api/revision", "/api/status", "/api/network/devices",
                     "/api/store/updates", "/api/m/weather/settings"):
            self.assertEqual(self.request(path)[0], 200, path)

    def test_weather_routes_exist(self):
        # Без координат прогноз отвечает ошибкой запроса, но не «нет такого маршрута».
        for path in ("/api/m/weather/forecast", "/api/m/weather/search?q="):
            self.assertNotEqual(self.request(path)[0], 404, path)

    def test_notifications(self):
        status, body = self.request("/api/notifications", {"module": "weather", "title": "Проверка", "level": "warn"})
        self.assertEqual(status, 200, body)
        note_id = json.loads(body)["id"]
        shade = json.loads(self.request("/api/shade")[1])
        self.assertIn(note_id, [n["id"] for n in shade["notifications"]])
        self.assertIn("status", shade)
        self.assertEqual(self.request("/api/notifications", {"module": "nope", "title": "x"})[0], 400)
        self.assertEqual(self.request("/api/notifications/dismiss", {"id": note_id})[0], 200)
        shade = json.loads(self.request("/api/shade", {"dnd": True, "brightness": 70})[1])
        self.assertTrue(shade["toggles"]["dnd"])
        self.assertEqual(shade["brightness"], 70)
        self.assertNotIn(note_id, [n["id"] for n in shade["notifications"]])
        self.assertEqual(self.request("/api/shade", {"brightness": 5})[0], 400)

    def test_reminder_in_shade(self):
        # Подстановка «text» в уведомлении напоминания не должна ломать шторку.
        self.app.reminders._notify({"id": "r99", "date": "2030-01-01", "time": "09:00", "text": "Позвонить {маме}"})
        status, body = self.request("/api/shade")
        self.assertEqual(status, 200, body)
        texts = [n["text"] for n in json.loads(body)["notifications"]]
        self.assertIn("Позвонить {маме}", texts)

    def test_timer_bad_seconds(self):
        status, body = self.request("/api/timers", {"action": "start", "seconds": 60})
        timer = json.loads(body)["timers"][-1]["id"]
        self.assertEqual(self.request("/api/timers", {"action": "add", "id": timer, "seconds": "abc"})[0], 400)
        self.assertEqual(self.request("/api/timers", {"action": "cancel", "id": timer})[0], 200)


class TransferTest(unittest.TestCase):
    """Перенос на новую панель: новая забирает копию у старой по сети."""

    def start(self, name, password=None):
        from wallhub import auth
        config = Config(os.path.join(self.tmp.name, name, "config.json"))
        if password:
            config.update({"admin": {"password_hash": auth.hash_password(password)}, "ui": {"panel_name": name}})
        app = App(config, System(dry_run=True), WEB, 0, [])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = serve(app, "127.0.0.1", port, trusted=False)
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), app.backends.stop_all(), app.module_updates.stop(),
                                 app.alarms.stop(), app.reminders.stop(), app.backups.stop()))
        return app, f"http://127.0.0.1:{port}"

    def post(self, url, body, cookie=None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), None

    def test_pull_from_old_panel(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old, old_url = self.start("old", password="secret12")
        old.alarms.save_alarm({"time": "06:30", "days": [0, 1, 2, 3, 4], "label": "Работа"})
        new, new_url = self.start("new")
        address = old_url.removeprefix("http://")
        status, body, _ = self.post(new_url + "/api/setup/transfer/pull", {"address": address, "password": "wrong"})
        self.assertEqual(status, 400, body)
        status, info, _ = self.post(new_url + "/api/setup/transfer/pull", {"address": address, "password": "secret12"})
        self.assertEqual(status, 200, info)
        self.assertEqual(info["panel"], "old")
        self.assertIn("secrets", info["parts"])
        status, body, cookie = self.post(new_url + "/api/setup/transfer/restore", {"parts": info["parts"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(cookie and "wallhub_session=" in cookie)
        self.assertEqual([a["label"] for a in new.config.get("alarms")], ["Работа"])
        self.assertEqual(new.config.get("admin", "password_hash"), old.config.get("admin", "password_hash"))
        # Пароль задан: перенос в режиме первого запуска больше недоступен.
        status, _, _ = self.post(new_url + "/api/setup/transfer/restore", {"parts": ["settings"]})
        self.assertEqual(status, 409)
