"""Wall-HUB: бэкенд настенной панели."""

import os

__version__ = "1.1.0"
# Версия API ядра для модулей (поле core в module.json). Мажорная часть
# меняется, когда старые модули перестают работать.
CORE_API = "1.1"


def _read_build():
    """Сборка из файла VERSION рядом с пакетом (его пишут install.sh и обновлятор)."""
    info = {}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key:
                    info[key] = value
    except OSError:
        pass
    return info


BUILD = _read_build()
