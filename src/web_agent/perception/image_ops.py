"""Operaciones de imagen deterministas y baratas (Pillow, sin numpy).

- ``change_map``: compara dos capturas en una cuadrícula reducida y describe
  cuánto y dónde cambió la pantalla (guardián de cambios globales, Mitigación 4,
  y control de ráfagas de Nivel 2).
- ``thumbnail`` / ``crop_roi``: imágenes para la visión dual (Mitigación 4).

Las coordenadas de salida están en píxeles de la imagen original, que en este
proyecto coinciden con píxeles CSS del viewport (captura ``scale="css"``).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops

GRID_COLS = 40
GRID_ROWS = 25
CELL_DIFF_THRESHOLD = 12  # diferencia media (0..255) para considerar una celda cambiada


@dataclass(frozen=True)
class ChangeMap:
    changed_fraction: float
    clusters: int
    bbox: tuple[int, int, int, int] | None  # (x0, y0, x1, y1) en píxeles de imagen
    cells: frozenset[tuple[int, int]]

    @property
    def unchanged(self) -> bool:
        return not self.cells


def decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _grid(img: Image.Image) -> Image.Image:
    return img.convert("L").resize((GRID_COLS, GRID_ROWS), Image.Resampling.BOX)


def _clusters(cells: set[tuple[int, int]], gap: int = 2) -> int:
    """Componentes conexas con tolerancia ``gap`` celdas (cambios cercanos = un único cambio)."""
    remaining = set(cells)
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            cx, cy = stack.pop()
            near = [c for c in remaining if abs(c[0] - cx) <= gap and abs(c[1] - cy) <= gap]
            for c in near:
                remaining.discard(c)
                stack.append(c)
    return count


def change_map(before: bytes, after: bytes) -> ChangeMap:
    a, b = decode(before), decode(after)
    if a.size != b.size:
        w, h = b.size
        return ChangeMap(1.0, 1, (0, 0, w, h), frozenset((x, y) for x in range(GRID_COLS) for y in range(GRID_ROWS)))
    w, h = a.size
    diff = ImageChops.difference(_grid(a), _grid(b))
    data = diff.tobytes()
    cells = {
        (i % GRID_COLS, i // GRID_COLS) for i, v in enumerate(data) if v >= CELL_DIFF_THRESHOLD
    }
    if not cells:
        return ChangeMap(0.0, 0, None, frozenset())
    cw, ch = w / GRID_COLS, h / GRID_ROWS
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    bbox = (int(min(xs) * cw), int(min(ys) * ch), int((max(xs) + 1) * cw), int((max(ys) + 1) * ch))
    return ChangeMap(len(cells) / (GRID_COLS * GRID_ROWS), _clusters(cells), bbox, frozenset(cells))


def screens_equivalent(before: bytes, after: bytes, *, max_fraction: float = 0.0) -> bool:
    """Igualdad tolerante (ignora ruido por debajo del umbral de celda)."""
    return change_map(before, after).changed_fraction <= max_fraction


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def thumbnail(data: bytes, size: tuple[int, int] = (400, 250), quality: int = 70) -> bytes:
    img = decode(data)
    return _encode_jpeg(img.resize(size, Image.Resampling.LANCZOS), quality)


@dataclass(frozen=True)
class RoiCrop:
    image: bytes
    box: tuple[int, int, int, int]  # (x0, y0, x1, y1) en píxeles de la imagen original

    def box_norm(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = self.box
        return (round(x0 * 1000 / width), round(y0 * 1000 / height),
                round(x1 * 1000 / width), round(y1 * 1000 / height))


def crop_roi(data: bytes, center: tuple[int, int], size: tuple[int, int] = (500, 350),
             quality: int = 85) -> RoiCrop:
    """Recorte a resolución nativa centrado en ``center`` y ajustado a los bordes."""
    img = decode(data)
    w, h = img.size
    rw, rh = min(size[0], w), min(size[1], h)
    x0 = min(max(0, center[0] - rw // 2), w - rw)
    y0 = min(max(0, center[1] - rh // 2), h - rh)
    box = (x0, y0, x0 + rw, y0 + rh)
    return RoiCrop(_encode_jpeg(img.crop(box), quality), box)
