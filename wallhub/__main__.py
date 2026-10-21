"""Запуск: python3 -m wallhub [--dry-run] [--config PATH] ..."""

import argparse
import logging
import os
import signal
import threading

from . import auth
from .config import Config
from .server import App, serve
from .system import System

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(prog="wallhub", description="Wall-HUB: бэкенд настенной панели")
    parser.add_argument("--config", default=os.environ.get("WALLHUB_CONFIG", "/var/lib/wallhub/config.json"))
    parser.add_argument("--web", default=os.environ.get("WALLHUB_WEB", os.path.join(HERE, "..", "web")))
    parser.add_argument("--kiosk-port", type=int, default=8080)
    parser.add_argument("--admin-port", type=int, default=80)
    parser.add_argument("--admin-bind", default="0.0.0.0")
    parser.add_argument("--dry-run", action="store_true", help="не выполнять системные команды (для разработки)")
    parser.add_argument("--reset-password", action="store_true",
                        help="сбросить пароль админки (новый задаётся при следующем входе) и выйти")
    parser.add_argument("--modules-dir", action="append", default=[], metavar="DIR",
                        help="папка с модулями в разработке (например, wallhub-modules/modules); можно несколько")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config(args.config)

    if args.reset_password:
        config.update({"admin": {"password_hash": ""}})
        # Сессии теперь переживают перезапуск, поэтому сбрасываем и их.
        auth.Sessions(os.path.join(os.path.dirname(os.path.abspath(config.path)), "sessions.json")).clear()
        return

    app = App(config, System(dry_run=args.dry_run), args.web, args.admin_port,
              [os.path.realpath(d) for d in args.modules_dir])
    servers = [
        serve(app, "127.0.0.1", args.kiosk_port, trusted=True),
        serve(app, args.admin_bind, args.admin_port, trusted=False),
    ]

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    for server in servers:
        server.shutdown()


if __name__ == "__main__":
    main()
