"""Фоновые картинки экрана.

Картинку готовит админка: обрезает и уменьшает её под экран и заодно рисует
размытую копию для «экономного» стекла (плитки показывают свой кусок этой
копии вместо backdrop-filter). Сюда приходят уже готовые JPEG/PNG/WebP;
сервер только проверяет их и хранит парой файлов <id>.<ext> и <id>-blur.<ext>
в каталоге backgrounds рядом с настройками.

Встроенные фоны (default-<имя>-light|dark) лежат в web/backgrounds и приходят
с версией панели; их нельзя удалить.
"""

import os
import re
import secrets
import threading

from .i18n import tr

# Готовая картинка 1024×600 весит сотни килобайт; запас на PNG.
MAX_IMAGE = 6 * 1024 * 1024
MAX_COUNT = 30
ID_RE = re.compile(r"^([0-9a-f]{12}|default-[a-z]+-(light|dark))$")
FILE_RE = re.compile(r"^([0-9a-f]{12}|default-[a-z]+-(?:light|dark))(-blur)?\.(jpg|png|webp)$")
TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class BackgroundError(Exception):
    pass


def sniff(data):
    """Расширение по сигнатуре файла или None, если это не картинка."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class Backgrounds:
    def __init__(self, path, builtin_path=None):
        self.path = path
        self.builtin_path = builtin_path
        self._lock = threading.Lock()

    @staticmethod
    def _scan(path):
        """{id: {имя суффикса: имя файла}} для файлов каталога."""
        found = {}
        try:
            names = os.listdir(path) if path else []
        except FileNotFoundError:
            names = []
        for name in names:
            m = FILE_RE.match(name)
            if m:
                found.setdefault(m.group(1), {})["blur" if m.group(2) else "url"] = name
        return found

    def _user(self):
        return {k: v for k, v in self._scan(self.path).items() if not k.startswith("default-")}

    def _builtin(self):
        return {k: v for k, v in self._scan(self.builtin_path).items() if k.startswith("default-")}

    def list(self):
        """Загруженные фоны (новые первыми), затем встроенные."""
        user = []
        for bg_id, files in self._user().items():
            # Без основного файла фон не показать (например, оборвалась запись).
            if "url" not in files:
                continue
            full = os.path.join(self.path, files["url"])
            item = {"id": bg_id, "url": f"/bg/{files['url']}", "size": os.path.getsize(full),
                    "mtime": os.path.getmtime(full)}
            if "blur" in files:
                item["blur"] = f"/bg/{files['blur']}"
            user.append(item)
        user.sort(key=lambda i: i["mtime"], reverse=True)
        builtin = []
        for bg_id, files in sorted(self._builtin().items()):
            if "url" in files:
                builtin.append({"id": bg_id, "url": f"/bg/{files['url']}", "blur": f"/bg/{files.get('blur', files['url'])}",
                                "builtin": True, "theme": bg_id.rsplit("-", 1)[1]})
        # Тёмные встроенные после светлых, чтобы галереи шли по темам.
        builtin.sort(key=lambda i: i["theme"] == "dark")
        return user + builtin

    def exists(self, bg_id):
        return any(i["id"] == bg_id for i in self.list())

    def add(self, image, blur):
        kinds = sniff(image), sniff(blur)
        if None in kinds:
            raise BackgroundError(tr("Это не картинка JPEG, PNG или WebP"))
        if len(image) > MAX_IMAGE or len(blur) > MAX_IMAGE:
            raise BackgroundError(tr("Слишком большая картинка"))
        with self._lock:
            if len(self._user()) >= MAX_COUNT:
                raise BackgroundError(tr("Можно хранить не больше {n} фонов, удалите лишние", n=MAX_COUNT))
            os.makedirs(self.path, exist_ok=True)
            bg_id = secrets.token_hex(6)
            # Сначала размытая копия: фон появляется в списке по основному файлу.
            for suffix, data, ext in (("-blur", blur, kinds[1]), ("", image, kinds[0])):
                full = os.path.join(self.path, f"{bg_id}{suffix}.{ext}")
                with open(full + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(full + ".tmp", full)
        return bg_id

    def delete(self, bg_id):
        if not ID_RE.match(str(bg_id)):
            raise BackgroundError(tr("Нет такого фона"))
        if bg_id.startswith("default-"):
            raise BackgroundError(tr("Встроенный фон нельзя удалить"))
        with self._lock:
            for name in self._user().get(bg_id, {}).values():
                os.remove(os.path.join(self.path, name))

    def file(self, name):
        """Путь и тип файла для раздачи по /bg/<name> или None."""
        m = FILE_RE.match(name)
        if not m:
            return None
        base = self.builtin_path if m.group(1).startswith("default-") else self.path
        full = os.path.join(base or "", name)
        if not base or not os.path.isfile(full):
            return None
        return full, TYPES[m.group(3)]
