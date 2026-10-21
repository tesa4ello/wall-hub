import json
import os
import tempfile
import time
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


class SessionsTest(unittest.TestCase):
    def test_sessions_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sessions.json")
            token = auth.Sessions(path).create()
            self.assertTrue(auth.Sessions(path).valid(token))
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(token, f.read())
            auth.Sessions(path).clear()
            self.assertFalse(auth.Sessions(path).valid(token))


class ValidateTest(unittest.TestCase):
    def test_valid(self):
        validate_ui({"theme": "light", "theme_mode": "sun", "day_start": "06:30", "dim_after_min": 0,
                     "latitude": 59.94, "longitude": 30.3, "night_dim": 40, "blank_after_min": 60,
                     "background": "0123456789ab", "background_dark": "same", "bg_dim": 30, "tiles": "blur",
                     "tile_transparency": 70, "sound_enabled": False, "sound_volume": 40, "sound_name": "bell",
                     "sound_quiet": True, "sound_quiet_from": "23:00", "sound_quiet_to": "07:30", "sound_update": False})

    def test_invalid(self):
        for patch in ({"theme": "pink"}, {"day_start": "25:00"}, {"dim_level": 100},
                      {"dim_after_min": True}, {"unknown": 1}, {"theme_mode": "auto"},
                      {"latitude": 91}, {"night_dim": 95}, {"auto_theme": True},
                      {"background": "../config"}, {"background_dark": 1}, {"bg_dim": 90}, {"tiles": "glass"}, {"tile_transparency": 95},
                      {"sound_enabled": 1}, {"sound_volume": 101}, {"sound_name": "horn"}, {"sound_quiet_to": "7:3"}):
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


class ModulesTest(unittest.TestCase):
    def make(self, root, name, **meta):
        os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, name, "module.json"), "w", encoding="utf-8") as f:
            json.dump(dict({"id": name, "core": "1", "widgets": []}, **meta), f)
        with open(os.path.join(root, name, "module.js"), "w") as f:
            f.write("export const widgets = {};")

    def test_sources_and_compatibility(self):
        from wallhub.modules import Registry
        with tempfile.TemporaryDirectory() as dev, tempfile.TemporaryDirectory() as inst:
            self.make(dev, "quotes")
            self.make(dev, "clock")  # штатный id перекрыть нельзя
            self.make(dev, "_template")
            self.make(inst, "old", core="0")
            self.make(inst, "quotes", version="9.9.9")
            reg = Registry(os.path.join(WEB, "modules"), [dev], inst)
            found = reg.scan()
            self.assertEqual(found["quotes"].source, "dev")
            self.assertEqual(found["clock"].source, "builtin")
            self.assertNotIn("_template", found)
            self.assertIsNotNone(found["old"].error)
            self.assertNotIn("old", reg.loaded())
            self.assertTrue(reg.static_file("quotes", "module.js"))
            self.assertIsNone(reg.static_file("quotes", "../quotes/module.js/.."))
            self.assertIsNone(reg.static_file("quotes", "backend/__init__.py"))


class StoreTest(unittest.TestCase):
    """Установка и удаление модуля из каталога (GitHub подменён)."""

    def setUp(self):
        import io
        import tarfile
        from wallhub.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(os.path.join(self.tmp.name, "config.json"))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            files = {
                "modules/hello/module.json": json.dumps({"id": "hello", "version": "1.2.0", "core": "1",
                                                         "title": "Привет", "widgets": []}),
                "modules/hello/module.js": "export const widgets = {};",
                "modules/other/module.json": "{}",
            }
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo("repo-abc/" + name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        archive = buf.getvalue()
        catalog = {"modules": [{"id": "hello", "version": "1.2.0", "core": "1", "title": "Привет"},
                               {"id": "clock", "version": "9.0.0", "core": "1", "title": "Часы"}]}

        def fetch(url, accept=None, limit=None):
            if "/commits/" in url:
                return json.dumps({"sha": "abcdef1234567"}).encode()
            if "catalog.json" in url:
                return json.dumps(catalog).encode()
            if "/tarball/" in url:
                return archive
            raise AssertionError(url)

        self.changed = 0
        self.store = Store(self.config, os.path.join(self.tmp.name, "modules"), "/nonexistent", ["clock"],
                           on_change=self.bump, fetch=fetch)

    def bump(self):
        self.changed += 1

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_and_remove(self):
        from wallhub.modules import Registry
        rows = {m["id"]: m for m in self.store.listing()["modules"]}
        self.assertTrue(rows["clock"]["builtin"])
        self.store.install("hello")
        reg = Registry(os.path.join(WEB, "modules"), installed_dir=os.path.join(self.tmp.name, "modules"))
        self.assertEqual(reg.loaded()["hello"].meta["version"], "1.2.0")
        self.assertTrue(reg.static_file("hello", "module.js"))
        self.assertEqual(self.store.installed()["hello"]["commit"], "abcdef1234567")
        desks = self.config.get("desktops")
        desks[0]["widgets"].append({"id": "h1", "type": "hello/x", "x": 0, "y": 0, "w": 1, "h": 1, "options": {}})
        self.config.update({"desktops": desks})
        self.store.remove("hello")
        self.assertNotIn("hello", reg.loaded())
        self.assertFalse(any(w["type"] == "hello/x" for w in self.config.get("desktops")[0]["widgets"]))
        self.assertEqual(self.changed, 2)

    def test_builtin_is_not_installed(self):
        from wallhub.store import StoreError
        with self.assertRaises(StoreError):
            self.store.install("clock")

    def test_parse_repo(self):
        from wallhub.store import StoreError, parse_repo
        self.assertEqual(parse_repo("https://github.com/tesa4ello/wallhub-modules.git"), "tesa4ello/wallhub-modules")
        self.assertEqual(parse_repo("tesa4ello/wallhub-modules"), "tesa4ello/wallhub-modules")
        with self.assertRaises(StoreError):
            parse_repo("https://example.com/x")


class DesktopsTest(unittest.TestCase):
    def setUp(self):
        from wallhub.modules import Registry
        self.catalog = desktops.load_catalog(Registry(os.path.join(WEB, "modules")).loaded())
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

    def test_unknown_module_widget_is_kept(self):
        w = {"id": "a", "type": "gone/widget", "x": 0, "y": 0, "w": 3, "h": 2, "options": {"k": 1}}
        self.assertEqual(self.check([w])[0]["widgets"][0], w)

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
        from wallhub.modules import Registry
        self.i18n = i18n
        i18n.load(WEB, Registry(os.path.join(WEB, "modules")).loaded())

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


class BuiltinBackgroundsTest(unittest.TestCase):
    def test_builtins_listed_and_protected(self):
        from wallhub.background import BackgroundError, Backgrounds
        web = os.path.join(os.path.dirname(__file__), "..", "web", "backgrounds")
        with tempfile.TemporaryDirectory() as tmp:
            bgs = Backgrounds(tmp, web)
            ids = [i["id"] for i in bgs.list()]
            # Значения по умолчанию указывают на настоящие встроенные фоны.
            from wallhub.config import DEFAULTS
            self.assertIn(DEFAULTS["ui"]["background"], ids)
            self.assertIn(DEFAULTS["ui"]["background_dark"], ids)
            for item in bgs.list():
                self.assertIsNotNone(bgs.file(item["url"][len("/bg/"):]))
                self.assertIsNotNone(bgs.file(item["blur"][len("/bg/"):]))
            with self.assertRaises(BackgroundError):
                bgs.delete("default-aurora-dark")
            validate_ui({"background": "default-waves-light", "background_dark": "default-orbs-dark"})


class WeatherTest(unittest.TestCase):
    def setUp(self):
        from wallhub import weather
        self.weather = weather
        # fake_fetch строит прогноз от текущего времени.
        self.now = time.time()
        self.calls = []

        def fetch(url, params):
            self.calls.append((url, params))
            if self.fail:
                raise OSError("offline")
            return weather.fake_fetch(url, params)

        self.fail = False
        self.clock = [self.now]
        self.w = weather.Weather(fetch=fetch, clock=lambda: self.clock[0])

    def test_normalize_shape(self):
        data = self.w.forecast(55.7512, 37.6184)
        self.assertEqual(data["place"]["timezone"], "Europe/Moscow")
        self.assertEqual(len(data["hourly"]), self.weather.HOURS)
        self.assertEqual(len(data["daily"]), self.weather.DAYS)
        self.assertIn("temp", data["current"])
        self.assertIsInstance(data["hourly"][0]["day"], bool)
        self.assertFalse(data["stale"])

    def test_cache_shared_by_nearby_points(self):
        self.w.forecast(55.7512, 37.6184)
        self.w.forecast(55.7498, 37.6201)
        self.assertEqual(len(self.calls), 1)
        self.clock[0] += self.weather.FORECAST_TTL + 1
        self.w.forecast(55.75, 37.62)
        self.assertEqual(len(self.calls), 2)

    def test_stale_on_failure(self):
        self.w.forecast(55.75, 37.62)
        self.fail = True
        self.clock[0] += self.weather.FORECAST_TTL + self.weather.RETRY_AFTER + 1
        data = self.w.forecast(55.75, 37.62)
        self.assertTrue(data["stale"])
        with self.assertRaises(self.weather.WeatherError):
            self.w.forecast(10, 10)

    def test_search(self):
        results = self.w.search("Мос")
        self.assertEqual(results[0]["name"], "Москва")
        self.assertIn("Россия", results[0]["region"])
        self.assertEqual(self.w.search("М"), [])


class LocationTest(unittest.TestCase):
    def test_clean_location(self):
        self.assertEqual(desktops.clean_location(None), {"mode": "panel"})
        self.assertEqual(desktops.clean_location({"mode": "custom", "lat": "x"}), {"mode": "panel"})
        self.assertEqual(desktops.clean_location({"mode": "custom", "lat": 91, "lon": 0}), {"mode": "panel"})
        self.assertEqual(desktops.clean_location({"mode": "custom", "name": "Сочи", "lat": 43.585472, "lon": 39.723098}),
                         {"mode": "custom", "name": "Сочи", "lat": 43.5855, "lon": 39.7231})

    def test_parsers(self):
        from wallhub import location
        self.assertEqual(location.parse_ip_service("ipwho.is", {"success": False}), None)
        found = location.parse_ip_service("ipwho.is", {"success": True, "latitude": 59.93, "longitude": 30.31, "city": "СПб"})
        self.assertEqual((found["lat"], found["name"]), (59.93, "СПб"))
        self.assertEqual(location.parse_yandex({"location": {"point": {"lat": 55.1, "lon": 37.2}, "accuracy": 40}})["accuracy"], 40)
        self.assertIsNone(location.parse_yandex({}))
        self.assertEqual(location.signal_to_dbm(100), -50)

    def test_locate_by_ip_updates_config(self):
        from wallhub import location
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(os.path.join(tmp, "c.json"))
            loc = location.Locator(config, None, request=lambda url, body=None: {
                "success": True, "latitude": 59.9386, "longitude": 30.3141, "city": "Санкт-Петербург"})
            found = loc.locate("ip")
            self.assertTrue(found["changed"])
            ui = config.get("ui")
            self.assertEqual((ui["latitude"], ui["location_name"]), (59.9386, "Санкт-Петербург"))
            self.assertFalse(loc.locate("ip")["changed"])

    def test_manual_coordinates_kept_on_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            with open(path, "w") as f:
                json.dump({"ui": {"latitude": 59.9, "longitude": 30.3}}, f)
            self.assertEqual(Config(path).get("ui", "location_mode"), "manual")
            self.assertEqual(Config(os.path.join(tmp, "new.json")).get("ui", "location_mode"), "ip")

    def test_yandex_key_is_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(os.path.join(tmp, "c.json"))
            config.update({"location": {"yandex_key": "abc-123-def"}})
            public = config.public()
            self.assertNotIn("yandex_key", public["location"])
            self.assertTrue(public["location"]["yandex_key_set"])


class MetnoTest(unittest.TestCase):
    def test_fallback_to_metno(self):
        from wallhub import weather
        calls = []

        def fetch(url, params):
            calls.append(url)
            if url == weather.FORECAST_URL:
                raise OSError("timed out")
            return weather.fake_metno(params["lat"], params["lon"])

        w = weather.Weather(fetch=fetch, local_tz=lambda: "Europe/Moscow")
        data = w.forecast(59.79, 30.16)
        self.assertEqual(data["source"], "met.no")
        self.assertFalse(w.status["open-meteo"]["ok"])
        self.assertTrue(w.status["met.no"]["ok"])
        self.assertEqual(data["place"]["timezone"], "Europe/Moscow")
        self.assertEqual(data["place"]["utc_offset"], 3 * 3600)
        self.assertGreaterEqual(len(data["hourly"]), 47)
        self.assertGreaterEqual(len(data["daily"]), 9)
        self.assertEqual(data["minutely"], [])
        self.assertEqual(data["hourly"][3]["code"], 63)
        self.assertEqual(data["hourly"][3]["precip"], 0.6)
        day = data["daily"][0]
        self.assertLess(day["sunrise"], day["sunset"])
        self.assertLessEqual(day["tmin"], day["tmax"])
        self.assertIsNotNone(data["current"]["feels"])
        # Дальше сразу MET Norway, без ожидания Open-Meteo (он «остывает»).
        calls.clear()
        w.forecast(10, 10)
        self.assertEqual(calls, [__import__("wallhub").metno.URL])

    def test_symbols(self):
        from wallhub import metno
        self.assertEqual(metno.symbol_code("clearsky_night"), (0, False))
        self.assertEqual(metno.symbol_code("heavyrainandthunder"), (95, None))
        self.assertEqual(metno.symbol_code("lightsnowshowers_day"), (85, True))

    def test_sun_times(self):
        from datetime import datetime, timezone as tz
        from wallhub import metno
        noon = datetime(2026, 6, 21, 9, tzinfo=tz.utc).timestamp()  # полдень в Москве
        rise, sett = metno.sun_times(noon, 55.75, 37.62)
        self.assertEqual(datetime.fromtimestamp(rise, tz.utc).strftime("%H:%M")[:4], "00:4")
        self.assertIsNone(metno.sun_times(noon, 78, 15)[0])

    def test_search_falls_back_to_nominatim(self):
        from wallhub import weather

        def fetch(url, params):
            if url == weather.GEOCODING_URL:
                raise OSError("timed out")
            return weather.fake_fetch(url, params)

        results = weather.Weather(fetch=fetch).search("Пушкин")
        self.assertEqual(results[0]["name"], "Пушкин")
        self.assertIn("Санкт-Петербург", results[0]["region"])
class ConfigWaitTest(unittest.TestCase):
    def test_wait_wakes_on_update(self):
        import threading
        import time
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(os.path.join(tmp, "c.json"))
            start = config.revision
            threading.Timer(0.1, lambda: config.update({"ui": {"theme": "light"}})).start()
            t0 = time.monotonic()
            self.assertTrue(config.wait(lambda: config.revision != start, 5))
            self.assertLess(time.monotonic() - t0, 2)
            self.assertFalse(config.wait(lambda: False, 0.05))


class ScreenTest(unittest.TestCase):
    def test_accept_key(self):
        from wallhub import screen
        # Пример из RFC 6455.
        self.assertEqual(screen.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_frames(self):
        import io
        import struct
        from wallhub import screen
        for size in (0, 5, 200, 70000):
            payload = bytes(range(256)) * (size // 256) + bytes(size % 256)
            self.assertEqual(screen.read_frame(io.BytesIO(screen.frame(payload))), (2, payload))
        # Браузер маскирует кадры.
        mask = b"\x01\x02\x03\x04"
        data = b"hello"
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        raw = bytes([0x82, 0x80 | len(data)]) + mask + masked
        self.assertEqual(screen.read_frame(io.BytesIO(raw)), (2, data))
        # Слишком большой кадр и обрыв — конец соединения.
        with self.assertRaises(EOFError):
            screen.read_frame(io.BytesIO(bytes([0x82, 127]) + struct.pack(">Q", 1 << 30)))
        with self.assertRaises(EOFError):
            screen.read_frame(io.BytesIO(b"\x82"))

    def test_pump(self):
        import io
        import socket
        from wallhub import screen
        ours, vnc = socket.socketpair()
        vnc.sendall(b"RFB 003.008\n")
        vnc.shutdown(socket.SHUT_WR)
        rfile = io.BytesIO(screen.frame(b"client") + screen.frame(b"", 8))
        wfile = io.BytesIO()
        screen.pump(rfile, wfile, ours)
        self.assertEqual(vnc.recv(100), b"client")
        out = io.BytesIO(wfile.getvalue())
        self.assertEqual(screen.read_frame(out), (2, b"RFB 003.008\n"))
        self.assertEqual(screen.read_frame(out), (8, b""))
        vnc.close()

    def test_idle_stop(self):
        from unittest import mock
        from wallhub import screen
        s = screen.Screen(dry_run=True)
        calls = []
        s._systemctl = calls.append
        with mock.patch.object(screen, "IDLE_STOP", 0.05):
            s.acquire()
            s.acquire()
            s.release()
            time.sleep(0.1)
            self.assertEqual(calls, ["start", "start"])
            s.release()
            time.sleep(0.15)
            self.assertEqual(calls, ["start", "start", "stop"])
            # Вернулся до таймера — юнит не останавливается.
            s.acquire()
            s.release()
            s.acquire()
            time.sleep(0.1)
            self.assertEqual(calls[-1], "start")


class ProvidersTest(unittest.TestCase):
    NOW = 1_800_000_000  # 2027-01-15 08:00 UTC

    def test_settings_normalized(self):
        from wallhub import weather
        s = weather.normalize_settings({"providers": [{"id": "met.no", "enabled": True}, {"id": "bogus"}],
                                        "refresh_min": 999, "keys": {"yandex": ""}})
        ids = [p["id"] for p in s["providers"]]
        self.assertEqual(ids[0], "met.no")
        self.assertNotIn("bogus", ids)
        self.assertEqual(len(ids), 5)
        self.assertEqual(s["refresh_min"], 60)
        self.assertEqual(s["keys"], {})

    def test_order_and_keys(self):
        from wallhub import weather, weather_providers as wp
        calls = []

        def fetch(url, params, headers=None):
            calls.append(url)
            if url == wp.WEATHERAPI_URL:
                return self.weatherapi_sample()
            raise OSError("down")

        settings = {"providers": [{"id": "weatherapi", "enabled": True}, {"id": "yandex", "enabled": True},
                                  {"id": "open-meteo", "enabled": True}], "keys": {"weatherapi": "k" * 20},
                    "refresh_min": 15}
        w = weather.Weather(fetch=fetch, clock=lambda: self.NOW, settings=lambda: settings)
        data = w.forecast(55.75, 37.62)
        # Яндекс без ключа пропущен, WeatherAPI ответил первым.
        self.assertEqual(calls, [wp.WEATHERAPI_URL])
        self.assertEqual(data["source"], "weatherapi")
        self.assertEqual(data["refresh"], 15 * 60)
        self.assertEqual(data["units"]["pressure"], "mmhg")

    def weatherapi_sample(self):
        hours = [{"time_epoch": self.NOW - 3600 + i * 3600, "temp_c": -3.0 + i * 0.1, "is_day": 1,
                  "condition": {"code": 1213}, "chance_of_rain": 0, "chance_of_snow": 70, "precip_mm": 0.2}
                 for i in range(24)]
        return {
            "location": {"tz_id": "Europe/Moscow"},
            "current": {"last_updated_epoch": self.NOW, "temp_c": -2.5, "feelslike_c": -7.0, "humidity": 90,
                        "precip_mm": 0.1, "condition": {"code": 1213}, "is_day": 1, "pressure_mb": 1015.0,
                        "wind_kph": 18.0, "wind_degree": 200, "gust_kph": 30.0, "cloud": 100, "uv": 0.5},
            "forecast": {"forecastday": [{"date": "2027-01-15", "day": {"maxtemp_c": -1.0, "mintemp_c": -6.0,
                         "totalprecip_mm": 2.0, "daily_chance_of_rain": 0, "daily_chance_of_snow": 80, "uv": 1.0,
                         "maxwind_kph": 25.0, "condition": {"code": 1213}},
                         "astro": {"sunrise": "08:55 AM", "sunset": "04:20 PM"}, "hour": hours}]},
        }

    def test_weatherapi(self):
        from wallhub import weather_providers as wp
        data = wp.weatherapi(lambda url, params: self.weatherapi_sample(), 55.75, 37.62, None, "k", self.NOW, 48, 10)
        self.assertEqual(data["current"]["code"], 71)
        self.assertEqual(data["current"]["wind"], 5.0)
        self.assertEqual(data["daily"][0]["sunrise"], 1_799_992_500)  # 08:55 MSK = 05:55 UTC
        self.assertEqual(data["place"]["timezone"], "Europe/Moscow")
        self.assertEqual(data["hourly"][0]["pop"], 70)

    def test_openweathermap(self):
        from wallhub import weather_providers as wp
        cur = {"dt": self.NOW, "timezone": 10800, "weather": [{"id": 500, "icon": "10n"}],
               "main": {"temp": 1.5, "feels_like": -2.0, "humidity": 80, "pressure": 1010},
               "wind": {"speed": 4.0, "deg": 90}, "clouds": {"all": 75}, "rain": {"1h": 0.3},
               "sys": {"sunrise": self.NOW + 3000, "sunset": self.NOW + 30000}}
        fc = {"city": {"timezone": 10800}, "list": [
            {"dt": self.NOW + i * 10800, "main": {"temp": 1.0 + i}, "weather": [{"id": 803}], "pop": 0.2,
             "rain": {"3h": 0.9}, "sys": {"pod": "d"}, "wind": {"speed": 3}} for i in range(16)]}
        fetch = lambda url, params: cur if url == wp.OWM_WEATHER else fc
        data = wp.openweathermap(fetch, 55.75, 37.62, None, "k", self.NOW, 48, 10)
        self.assertEqual(data["place"]["timezone"], "Etc/GMT-3")
        self.assertEqual((data["current"]["code"], data["current"]["day"]), (61, False))
        self.assertEqual(data["hourly"][0]["precip"], 0.3)
        self.assertEqual(data["daily"][0]["sunrise"], self.NOW + 3000)
        self.assertAlmostEqual(data["daily"][0]["precip"], 0.9 * 5, places=1)  # 11, 14, 17, 20, 23 по Москве

    def test_yandex(self):
        from wallhub import weather_providers as wp
        raw = {"now": self.NOW, "info": {"tzinfo": {"name": "Europe/Moscow", "offset": 10800}},
               "fact": {"temp": -4, "feels_like": -9, "condition": "light-snow", "wind_speed": 3.2, "wind_dir": "sw",
                        "pressure_mm": 750, "humidity": 85, "daytime": "d", "cloudness": 0.75, "uv_index": 0},
               "forecasts": [{"date_ts": self.NOW - 11 * 3600, "sunrise": "08:55", "sunset": "16:20",
                              "parts": {"night": {"temp_min": -8, "temp_max": -6, "prec_prob": 10, "prec_mm": 0},
                                        "day": {"temp_min": -5, "temp_max": -2, "condition": "snow", "prec_prob": 60,
                                                "prec_mm": 1.5, "uv_index": 1}},
                              "hours": [{"hour_ts": self.NOW + i * 3600, "temp": -4, "condition": "snow",
                                         "prec_prob": 50, "prec_mm": 0.2} for i in range(10)]}]}
        seen = {}

        def fetch(url, params, headers=None):
            seen.update(headers or {})
            return raw
        data = wp.yandex(fetch, 55.75, 37.62, None, "secret-key", self.NOW, 48, 7)
        self.assertEqual(seen["X-Yandex-Weather-Key"], "secret-key")
        self.assertEqual((data["current"]["code"], data["current"]["wind_dir"]), (71, 225))
        self.assertAlmostEqual(data["current"]["pressure"], 999.9, places=0)
        day = data["daily"][0]
        self.assertEqual((day["tmin"], day["tmax"], day["code"], day["pop"]), (-8, -2, 73, 60))
        self.assertEqual(day["sunrise"], 1_799_992_500)

    def test_weather_keys_are_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config(os.path.join(tmp, "c.json"))
            config.update({"weather": {"keys": {"yandex": "x" * 20}}})
            self.assertNotIn("keys", config.public()["weather"])
