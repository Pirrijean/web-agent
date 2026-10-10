"""Dobles de prueba: página/navegador falsos con capturas PNG reales y auditoría en memoria."""

from __future__ import annotations

import struct
import zlib
from types import SimpleNamespace


def make_png(width: int, height: int, shade: int = 0) -> bytes:
    raw = b"".join(b"\x00" + bytes([shade % 256]) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeMouse:
    def __init__(self, page):
        self.page = page
        self.calls = []

    async def move(self, x, y):
        self.calls.append(("move", x, y))

    async def click(self, x, y, button="left", click_count=1):
        self.calls.append(("click", x, y, button, click_count))
        if not self.page.inert:
            self.page.version += 1

    async def wheel(self, dx, dy):
        self.calls.append(("wheel", dx, dy))
        self.page.version += 1


class FakeKeyboard:
    def __init__(self, page):
        self.page = page
        self.calls = []

    async def type(self, text, delay=0):
        self.calls.append(("type", text))
        self.page.version += 1

    async def press(self, key):
        self.calls.append(("press", key))
        self.page.version += 1

    async def up(self, key):
        self.calls.append(("up", key))


class FakePage:
    def __init__(self, url="https://desktop.example.com/vnc.html", width=1280, height=800):
        self.url = url
        self.viewport_size = {"width": width, "height": height}
        self.version = 0
        self.inert = False
        self.closed = False
        self.navigations = []
        self.screenshots = 0
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)
        self.main_frame = object()

    def is_closed(self):
        return self.closed

    async def screenshot(self, **kwargs):
        self.screenshots += 1
        w, h = self.viewport_size["width"], self.viewport_size["height"]
        return make_png(w, h, self.version)

    async def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url = url
        self.version += 1
        return SimpleNamespace(status=200)

    async def title(self):
        return "Escritorio"


class FakeBrowser:
    def __init__(self, page=None, fail_start=False):
        self.page = page or FakePage()
        self.fail_start = fail_start
        self.started = False
        self.stopped = False
        self.session_id = "ses_test"
        self.page_id = "pag_test"
        self.navigation_epoch = 0
        self.blocked_requests = 0
        self.network_validator = None
        self.headless = True
        self.chromium_sandbox = True

    async def start(self):
        self.started = True
        if self.fail_start:
            raise RuntimeError("Fallo parcial")

    async def stop(self):
        self.stopped = True


class MemoryAudit:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def log_event(self, event):
        if self.fail:
            raise OSError("No disponible")
        self.events.append(event)

    def types(self):
        return [e.event_type for e in self.events]

    def dump(self) -> str:
        return "\n".join(e.model_dump_json() for e in self.events)
