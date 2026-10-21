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
