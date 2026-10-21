import json
import os
import tempfile
import unittest

from wallhub import auth
from wallhub import desktops
from wallhub.config import Config
from wallhub.network import group_external, parse_ip
from wallhub.server import ApiError, validate_ui
from wallhub.system import split_terse
from wallhub.update import Updates, normalize_settings


class ConfigTest(unittest.TestCase):
    def test_defaults_merge_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = Config(path)
            self.assertEqual(config.get("ui", "theme"), "dark")
            config.update({"ui": {"theme": "light"}})
            reloaded = Config(path)
            self.assertEqual(reloaded.get("ui", "theme"), "light")
            # Остальные значения по умолчанию не потерялись.
            self.assertEqual(reloaded.get("ui", "dim_level"), 80)
            self.assertEqual(reloaded.revision, 1)
            with open(path) as f:
                self.assertIn("_revision", json.load(f))

    def test_migrates_auto_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(path, "w") as f:
                json.dump({"ui": {"auto_theme": True}}, f)
            ui = Config(path).get("ui")
            self.assertEqual(ui["theme_mode"], "schedule")
            self.assertNotIn("auto_theme", ui)

    def test_public_hides_password_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(os.path.join(tmp, "c.json"))
            config.update({"admin": {"password_hash": "secret"}})
            self.assertNotIn("password_hash", config.public()["admin"])


class AuthTest(unittest.TestCase):
    def test_password_roundtrip(self):
        stored = auth.hash_password("hunter22")
        self.assertTrue(auth.check_password("hunter22", stored))
        self.assertFalse(auth.check_password("hunter23", stored))
        self.assertFalse(auth.check_password("x", ""))


class ValidateTest(unittest.TestCase):
    def test_valid(self):
        validate_ui({"theme": "light", "theme_mode": "sun", "day_start": "06:30", "dim_after_min": 0,
                     "latitude": 59.94, "longitude": 30.3, "night_dim": 40, "blank_after_min": 60,
                     "background": "0123456789ab", "background_dark": "same", "bg_dim": 30, "tiles": "blur"})

    def test_invalid(self):
        for patch in ({"theme": "pink"}, {"day_start": "25:00"}, {"dim_level": 100},
                      {"dim_after_min": True}, {"unknown": 1}, {"theme_mode": "auto"},
                      {"latitude": 91}, {"night_dim": 95}, {"auto_theme": True},
                      {"background": "../config"}, {"background_dark": 1}, {"bg_dim": 90}, {"tiles": "glass"}):
            with self.assertRaises(ApiError):
                validate_ui(patch)


class NmcliTest(unittest.TestCase):
    def test_split_terse_escapes(self):
        self.assertEqual(split_terse(r"*:My\:Net:80:WPA2"), ["*", "My:Net", "80", "WPA2"])
        self.assertEqual(split_terse(r"a\\b:"), ["a\\b", ""])


class ExternalIpTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_ip("json", '"203.0.113.5"'), "203.0.113.5")
        self.assertEqual(parse_ip("text", "203.0.113.5\n"), "203.0.113.5")
        self.assertEqual(parse_ip("trace", "fl=1\nip=203.0.113.5\nts=1\n"), "203.0.113.5")
        with self.assertRaises(ValueError):
            parse_ip("text", "<html>blocked</html>")

    def test_group(self):
        result = group_external([("A", "1.1.1.1"), ("B", "2.2.2.2"), ("C", "1.1.1.1"), ("D", None)])
        self.assertEqual(result["addresses"], [
            {"ip": "1.1.1.1", "services": ["A", "C"]},
            {"ip": "2.2.2.2", "services": ["B"]},
        ])
        self.assertEqual(result["failed"], ["D"])


class UpdateSettingsTest(unittest.TestCase):
    def test_old_channels_become_stable(self):
        for old in ("main", "releases", None):
            self.assertEqual(normalize_settings({"channel": old})["channel"], "stable")
        self.assertEqual(normalize_settings({"channel": "dev"})["channel"], "dev")


class UpdateProgressTest(unittest.TestCase):
    def progress(self, **status):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "update.json"), "w", encoding="utf-8") as f:
                json.dump(status, f)
            return Updates(d).progress()

    def ago(self, seconds):
        import datetime
        return (datetime.datetime.now().astimezone() - datetime.timedelta(seconds=seconds)).isoformat()

    def test_running_install_is_shown(self):
        p = self.progress(state="installing", job="install", phase="install", message="Устанавливаю…")
        self.assertEqual(p["phase"], "install")

    def test_result_is_shown_for_a_while(self):
        self.assertIsNotNone(self.progress(state="idle", job="install", result="ok", finished_at=self.ago(10)))
        self.assertIsNone(self.progress(state="idle", job="install", result="ok", finished_at=self.ago(120)))
        self.assertIsNotNone(self.progress(state="idle", job="install", result="failed", finished_at=self.ago(120)))

    def test_check_result_is_not_shown(self):
        self.assertIsNone(self.progress(state="idle", job="check", result="failed", finished_at=self.ago(5)))
        self.assertIsNone(self.progress(state="idle"))


WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class DesktopsTest(unittest.TestCase):
    def setUp(self):
        self.catalog = desktops.load_catalog(WEB)
        self.providers = ["wifi", "load"]

    def check(self, widgets):
        desk = [{"id": "d1", "name": "Стол", "widgets": widgets}]
        return desktops.validate(desk, self.catalog, 16, 10, self.providers)

    def test_defaults_are_valid(self):
        from wallhub.config import DEFAULTS
        desktops.validate(DEFAULTS["desktops"], self.catalog, 16, 10, ["wifi", "load", "memory", "temp", "traffic"])

    def test_options_cleaned(self):
        result = self.check([{"id": "a", "type": "system/statusbar", "x": 0, "y": 0, "w": 16, "h": 1,
                              "options": {"items": ["load", "bogus", "load"], "junk": 1}}])
        self.assertEqual(result[0]["widgets"][0]["options"], {"items": ["load"], "align": "left"})

    def test_rejects_overlap_bounds_and_size(self):
        clock = {"type": "clock/clock", "w": 4, "h": 3, "options": {}}
        with self.assertRaises(desktops.LayoutError):
            self.check([dict(clock, id="a", x=0, y=0), dict(clock, id="b", x=3, y=2)])
        with self.assertRaises(desktops.LayoutError):
            self.check([dict(clock, id="a", x=14, y=0)])
        with self.assertRaises(desktops.LayoutError):
            self.check([dict(clock, id="a", x=0, y=0, w=1)])
        with self.assertRaises(desktops.LayoutError):
            self.check([dict(clock, id="a", x=0, y=0, type="nope")])

    def test_migration_from_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as f:
                json.dump({"modules": [{"id": "clock", "enabled": True, "options": {"seconds": False}}],
                           "statusbar": ["load"]}, f)
            cfg = Config(path)
            widgets = cfg.get("desktops")[0]["widgets"]
            self.assertEqual(widgets[0]["options"]["items"], ["load"])
            self.assertFalse(widgets[1]["options"]["seconds"])
            self.assertNotIn("modules", cfg.public())


if __name__ == "__main__":
    unittest.main()


class FirstLoginTest(unittest.TestCase):
    """Пароль админки задаётся при первом входе."""

    def setUp(self):
        import http.client
        from wallhub.server import App, serve
        from wallhub.system import System

        self.http = http.client
        self.tmp = tempfile.TemporaryDirectory()
        config = Config(os.path.join(self.tmp.name, "c.json"))
        web = os.path.join(os.path.dirname(__file__), "..", "web")
        self.app = App(config, System(dry_run=True), web, 80)
        self.admin = serve(self.app, "127.0.0.1", 0, trusted=False)
        self.port = self.admin.server_address[1]

    def tearDown(self):
        self.admin.shutdown()
        self.admin.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, cookie=None):
        conn = self.http.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, "/api" + path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"{}")
        cookie = resp.getheader("Set-Cookie", "").split(";")[0]
        conn.close()
        return resp.status, data, cookie

    def test_setup_then_login(self):
        status, session, _ = self.call("GET", "/session")
        self.assertTrue(session["setup"])
        self.assertFalse(session["authorized"])
        self.assertEqual(self.call("POST", "/setup/password", {"new": "123"})[0], 400)
        status, _, cookie = self.call("POST", "/setup/password", {"new": "secret1"})
        self.assertEqual(status, 200)
        status, config, _ = self.call("GET", "/config", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(config["admin"]["password_set"])
        # Второй раз задать пароль без входа нельзя.
        self.assertEqual(self.call("POST", "/setup/password", {"new": "hacker1"})[0], 409)
        self.assertFalse(self.call("GET", "/session")[1]["setup"])
        self.assertEqual(self.call("POST", "/login", {"password": "secret1"})[0], 200)

    def test_admin_url_without_default_port(self):
        self.assertNotIn(":80/", self.app.admin_url())


class I18nTest(unittest.TestCase):
    def setUp(self):
        from wallhub import i18n
        self.i18n = i18n
        i18n.load(os.path.join(os.path.dirname(__file__), "..", "web"))

    def tearDown(self):
        self.i18n.set_language("ru")

    def test_translate_with_params(self):
        self.i18n.set_language("en")
        self.assertEqual(self.i18n.tr("Не больше {n} столов", n=3), "At most 3 desktops")
        # Строки модулей берутся из их module.json.
        self.assertEqual(self.i18n.tr("Часы"), "Clock")
        self.i18n.set_language("ru")
        self.assertEqual(self.i18n.tr("Не больше {n} столов", n=3), "Не больше 3 столов")

    def test_unknown_language_falls_back_to_source(self):
        self.i18n.set_language("xx")
        self.assertEqual(self.i18n.current(), "ru")
        self.assertIn("en", self.i18n.languages())

    def test_catalogs_cover_all_languages_keys(self):
        """У каждого непервичного языка переводы не пустые и плейсхолдеры совпадают."""
        import re
        web = os.path.join(os.path.dirname(__file__), "..", "web", "i18n")
        with open(os.path.join(web, "languages.json"), encoding="utf-8") as f:
            meta = json.load(f)
        for lang in meta["languages"]:
            if lang["code"] == meta["source"]:
                continue
            with open(os.path.join(web, f"{lang['code']}.json"), encoding="utf-8") as f:
                catalog = json.load(f)
            for src, dst in catalog.items():
                self.assertTrue(dst, src)
                ph = lambda s: sorted(re.findall(r"\{\w+\}|%s", s))
                self.assertEqual(ph(src), ph(dst), src)


class WifiWatchdogTest(unittest.TestCase):
    class FakeSystem:
        def __init__(self, radio, other):
            self.radio, self.other, self.turned_on = radio, other, False

        def radio_enabled(self):
            return self.radio

        def other_connection(self):
            return self.other

        def wifi_radio(self, enabled):
            self.radio = enabled
            self.turned_on = enabled

    def test_turns_wifi_back_on_after_timeout(self):
        from wallhub.server import WifiWatchdog
        system = self.FakeSystem(radio=False, other=False)
        dog = WifiWatchdog(system)
        dog.check(0)
        dog.check(WifiWatchdog.TIMEOUT - 1)
        self.assertFalse(system.turned_on)
        dog.check(WifiWatchdog.TIMEOUT)
        self.assertTrue(system.turned_on)

    def test_leaves_wifi_off_with_cable(self):
        from wallhub.server import WifiWatchdog
        system = self.FakeSystem(radio=False, other=True)
        dog = WifiWatchdog(system)
        dog.check(0)
        dog.check(WifiWatchdog.TIMEOUT * 2)
        self.assertFalse(system.turned_on)


class BackgroundsTest(unittest.TestCase):
    JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    def test_add_list_delete(self):
        from wallhub.background import Backgrounds
        with tempfile.TemporaryDirectory() as tmp:
            bgs = Backgrounds(os.path.join(tmp, "backgrounds"))
            self.assertEqual(bgs.list(), [])
            bg_id = bgs.add(self.JPEG, self.PNG)
            [item] = bgs.list()
            self.assertEqual(item["url"], f"/bg/{bg_id}.jpg")
            self.assertEqual(item["blur"], f"/bg/{bg_id}-blur.png")
            self.assertTrue(bgs.exists(bg_id))
            self.assertEqual(bgs.file(f"{bg_id}.jpg")[1], "image/jpeg")
            self.assertIsNone(bgs.file("../config.json"))
            bgs.delete(bg_id)
            self.assertEqual(bgs.list(), [])

    def test_rejects_non_images(self):
        from wallhub.background import BackgroundError, Backgrounds
        with tempfile.TemporaryDirectory() as tmp:
            bgs = Backgrounds(tmp)
            with self.assertRaises(BackgroundError):
                bgs.add(b"<svg onload=alert(1)>", self.JPEG)
            with self.assertRaises(BackgroundError):
                bgs.delete("../x")

    def test_decode_image(self):
        import base64
        from wallhub.background import BackgroundError
        from wallhub.server import decode_image
        url = "data:image/jpeg;base64," + base64.b64encode(self.JPEG).decode()
        self.assertEqual(decode_image(url), self.JPEG)
        for bad in (None, "", "data:image/jpeg;base64,***"):
            with self.assertRaises(BackgroundError):
                decode_image(bad)


class WidgetCardTest(unittest.TestCase):
    def test_card_override_kept_only_when_differs(self):
        catalog = {"clock/clock": {"id": "clock/clock", "title": "Часы", "card": False, "options": []}}
        desk = lambda card: [{"id": "d1", "widgets": [dict({"id": "w1", "type": "clock/clock", "x": 0, "y": 0, "w": 4, "h": 4}, **card)]}]
        self.assertTrue(desktops.validate(desk({"card": True}), catalog, 16, 10)[0]["widgets"][0]["card"])
        self.assertNotIn("card", desktops.validate(desk({"card": False}), catalog, 16, 10)[0]["widgets"][0])
        self.assertNotIn("card", desktops.validate(desk({"card": "yes"}), catalog, 16, 10)[0]["widgets"][0])
