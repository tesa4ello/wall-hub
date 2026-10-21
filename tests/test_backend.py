import json
import os
import tempfile
import unittest

from wallhub import auth
from wallhub.config import Config
from wallhub.server import ApiError, validate_ui
from wallhub.system import split_terse


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
                     "latitude": 59.94, "longitude": 30.3, "night_dim": 40, "blank_after_min": 60})

    def test_invalid(self):
        for patch in ({"theme": "pink"}, {"day_start": "25:00"}, {"dim_level": 100},
                      {"dim_after_min": True}, {"unknown": 1}, {"theme_mode": "auto"},
                      {"latitude": 91}, {"night_dim": 95}, {"auto_theme": True}):
            with self.assertRaises(ApiError):
                validate_ui(patch)


class NmcliTest(unittest.TestCase):
    def test_split_terse_escapes(self):
        self.assertEqual(split_terse(r"*:My\:Net:80:WPA2"), ["*", "My:Net", "80", "WPA2"])
        self.assertEqual(split_terse(r"a\\b:"), ["a\\b", ""])


if __name__ == "__main__":
    unittest.main()
