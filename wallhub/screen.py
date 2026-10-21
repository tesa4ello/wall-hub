"""Трансляция экрана панели в админку.

x11vnc (юнит wallhub-vnc.service) слушает только 127.0.0.1:5900. Браузер
админки подключается к /api/screen/ws по WebSocket, а сервис после проверки
входа гоняет байты между WebSocket и VNC. Юнит запускается, когда появляется
первый зритель, и останавливается через минуту после ухода последнего.
"""

import base64
import hashlib
import logging
import os
import socket
import struct
import subprocess
import threading
import time

log = logging.getLogger(__name__)

UNIT = "wallhub-vnc.service"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
IDLE_STOP = 60


class Screen:
    def __init__(self, dry_run=False, port=None):
        self.dry_run = dry_run
        self.port = int(port or os.environ.get("WALLHUB_VNC_PORT", 5900))
        self.viewers = 0
        self.lock = threading.Lock()
        self.stop_timer = None

    def _systemctl(self, verb):
        if self.dry_run:
            return
        subprocess.run(["systemctl", verb, "--no-block", UNIT], check=False, capture_output=True, timeout=15)

    def acquire(self):
        with self.lock:
            self.viewers += 1
            if self.stop_timer:
                self.stop_timer.cancel()
                self.stop_timer = None
        self._systemctl("start")

    def release(self):
        with self.lock:
            self.viewers -= 1
            if self.viewers == 0:
                self.stop_timer = threading.Timer(IDLE_STOP, self._idle_stop)
                self.stop_timer.daemon = True
                self.stop_timer.start()

    def _idle_stop(self):
        with self.lock:
            if self.viewers:
                return
            self.stop_timer = None
        log.info("трансляция экрана остановлена: зрителей нет")
        self._systemctl("stop")

    def connect_vnc(self, timeout=8):
        """Подключается к x11vnc, подождав, пока он запустится."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return socket.create_connection(("127.0.0.1", self.port), timeout=3)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def _read_exact(f, n):
    data = f.read(n)
    if data is None or len(data) < n:
        raise EOFError
    return data


def read_frame(f):
    """Один кадр WebSocket от браузера: (opcode, payload)."""
    b1, b2 = _read_exact(f, 2)
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(f, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(f, 8))[0]
    if length > 1 << 20:
        raise EOFError
    mask = _read_exact(f, 4) if b2 & 0x80 else None
    payload = _read_exact(f, length) if length else b""
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def frame(payload, opcode=2):
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 1 << 16:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def pump(rfile, wfile, vnc):
    """Гоняет данные, пока одна из сторон не закроется."""
    send_lock = threading.Lock()

    def send(data, opcode=2):
        with send_lock:
            wfile.write(frame(data, opcode))
            wfile.flush()

    def from_vnc():
        try:
            while True:
                data = vnc.recv(65536)
                if not data:
                    break
                send(data)
        except OSError:
            pass
        try:
            send(b"", 8)
        except OSError:
            pass

    reader = threading.Thread(target=from_vnc, daemon=True)
    reader.start()
    try:
        while True:
            opcode, payload = read_frame(rfile)
            if opcode == 8:
                break
            if opcode == 9:
                send(payload, 10)
            elif opcode in (0, 1, 2):
                vnc.sendall(payload)
    except (EOFError, OSError, ValueError):
        pass
    finally:
        try:
            vnc.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        vnc.close()
        reader.join(timeout=2)
