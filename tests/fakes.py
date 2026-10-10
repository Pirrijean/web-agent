"""Dobles de prueba: página/navegador falsos con capturas PNG reales y auditoría en memoria."""

from __future__ import annotations

import struct
import zlib
from types import SimpleNamespace

from web_agent.perception.ocr_engine import OcrEngine, TextBox


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

    async def click(self, x, y, button="left", click_count=1, delay=0):
        self.calls.append(("click", x, y, button, click_count))
        self.delays = getattr(self, "delays", []) + [delay]
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


# --------------------------------------------------------------------------- motor tri-nivel
def make_png_regions(width: int, height: int, background: int, rects) -> bytes:
    """PNG en escala de grises con rectángulos ``(x0, y0, x1, y1, shade)`` sobre un fondo."""
    base = bytes([background % 256]) * width
    rows = []
    for y in range(height):
        hits = [r for r in rects if r[1] <= y < r[3]]
        if not hits:
            rows.append(b"\x00" + base)
            continue
        row = bytearray(base)
        for x0, _, x1, _, shade in hits:
            x0, x1 = max(0, x0), min(width, x1)
            row[x0:x1] = bytes([shade % 256]) * (x1 - x0)
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class RegionPage(FakePage):
    """Página cuyas capturas cambian solo en la zona tocada (formularios), salvo que
    ``modal_on_click`` simule que un clic abre una ventana (cambio global)."""

    def __init__(self, *args, modal_on_click: bool = False, animate_frames: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.modal_on_click = modal_on_click
        self.modal = False
        self.focus = (640, 400)
        self.field_shade = 90
        self.animate_frames = animate_frames
        self.mouse = _RegionMouse(self)
        self.keyboard = _RegionKeyboard(self)

    async def screenshot(self, **kwargs):
        self.screenshots += 1
        if self.animate_frames > 0:
            self.animate_frames -= 1
            self.field_shade += 23
        w, h = self.viewport_size["width"], self.viewport_size["height"]
        bg = 40 if self.modal else 200
        fx, fy = self.focus
        return make_png_regions(w, h, bg, [(fx - 60, fy - 15, fx + 60, fy + 15, self.field_shade)])

    async def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url = url
        self.field_shade += 5
        return SimpleNamespace(status=200)


class _RegionMouse(FakeMouse):
    async def click(self, x, y, button="left", click_count=1, delay=0):
        self.calls.append(("click", x, y, button, click_count))
        self.delays = getattr(self, "delays", []) + [delay]
        self.page.focus = (int(x), int(y))
        self.page.field_shade += 31
        if self.page.modal_on_click:
            self.page.modal = True


class _RegionKeyboard(FakeKeyboard):
    async def type(self, text, delay=0):
        self.calls.append(("type", text))
        self.page.field_shade += 17

    async def press(self, key):
        self.calls.append(("press", key))
        self.page.field_shade += 11


class FakeOcr(OcrEngine):
    """OCR controlable: ``screen()`` devuelve las cajas con texto visibles en este momento."""

    name = "fake"
    available = True

    def __init__(self, screen):
        super().__init__()
        self.screen = screen
        self.detect_calls = 0
        self.read_calls = 0

    def _boxes(self):
        return [b if isinstance(b, TextBox) else TextBox(*b) for b in self.screen()]

    def _detect_sync(self, image):
        self.detect_calls += 1
        return [TextBox(b.x0, b.y0, b.x1, b.y1) for b in self._boxes()]

    def _read_sync(self, image, boxes):
        self.read_calls += 1
        by_pos = {(b.x0, b.y0, b.x1, b.y1): b for b in self._boxes()}
        return [by_pos[(b.x0, b.y0, b.x1, b.y1)] for b in boxes if (b.x0, b.y0, b.x1, b.y1) in by_pos]
