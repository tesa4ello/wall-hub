"""Фоновые картинки экрана.

Картинку готовит админка: обрезает и уменьшает её под экран и заодно рисует
размытую копию для «экономного» стекла (плитки показывают свой кусок этой
копии вместо backdrop-filter). Сюда приходят уже готовые JPEG/PNG/WebP;
сервер только проверяет их и хранит парой файлов <id>.<ext> и <id>-blur.<ext>
в каталоге backgrounds рядом с настройками.
"""

import os
import re
import secrets
import threading

# Готовая картинка 1024×600 весит сотни килобайт; запас на PNG.
MAX_IMAGE = 6 * 1024 * 1024
MAX_COUNT = 30
ID_RE = re.compile(r"^[0-9a-f]{12}$")
FILE_RE = re.compile(r"^([0-9a-f]{12})(-blur)?\.(jpg|png|webp)$")
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
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _files(self):
        try:
            return os.listdir(self.path)
        except FileNotFoundError:
            return []

    def list(self):
        """Загруженные фоны, новые первыми."""
        items = {}
        for name in self._files():
            m = FILE_RE.match(name)
            if not m:
                continue
            item = items.setdefault(m.group(1), {"id": m.group(1)})
            full = os.path.join(self.path, name)
            if m.group(2):
                item["blur"] = f"/bg/{name}"
            else:
                item["url"] = f"/bg/{name}"
                item["size"] = os.path.getsize(full)
                item["mtime"] = os.path.getmtime(full)
        # Без основного файла фон не показать (например, оборвалась запись).
        result = [i for i in items.values() if "url" in i]
        result.sort(key=lambda i: i["mtime"], reverse=True)
        return result

    def exists(self, bg_id):
        return any(i["id"] == bg_id for i in self.list())

    def add(self, image, blur):
        kinds = sniff(image), sniff(blur)
        if None in kinds:
            raise BackgroundError("Это не картинка JPEG, PNG или WebP")
        if len(image) > MAX_IMAGE or len(blur) > MAX_IMAGE:
            raise BackgroundError("Слишком большая картинка")
        with self._lock:
            if len(self.list()) >= MAX_COUNT:
                raise BackgroundError(f"Можно хранить не больше {MAX_COUNT} фонов, удалите лишние")
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
            raise BackgroundError("Нет такого фона")
        with self._lock:
            for name in self._files():
                m = FILE_RE.match(name)
                if m and m.group(1) == bg_id:
                    os.remove(os.path.join(self.path, name))

    def file(self, name):
        """Путь и тип файла для раздачи по /bg/<name> или None."""
        m = FILE_RE.match(name)
        full = os.path.join(self.path, name)
        if not m or not os.path.isfile(full):
            return None
        return full, TYPES[m.group(3)]
