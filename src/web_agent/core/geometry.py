"""Geometría de observaciones y coordenadas (documento maestro §9.2).

El modelo propone coordenadas enteras normalizadas en [0, 1000] relativas a la
imagen que recibió. La conversión a píxeles CSS del viewport es explícita:

    img_x = x_norm / 1000 * (W_img - 1)
    x_css = offset_x_css + img_x / scale

Con captura completa del viewport en escala CSS (offset 0, scale 1,
W_img == W_css) se reduce a ``round(x_norm / 1000 * (W_css - 1))``.
No se aplica nunca el device pixel ratio de forma implícita.
"""

from __future__ import annotations

import math
import struct

NORM_MAX = 1000


class GeometryError(ValueError):
    """Coordenadas o geometría incompatibles."""


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def norm_to_css(
    x_norm: int,
    y_norm: int,
    *,
    viewport_width_css: int,
    viewport_height_css: int,
    image_width_px: int | None = None,
    image_height_px: int | None = None,
    offset_x_css: int = 0,
    offset_y_css: int = 0,
    scale: float = 1.0,
) -> tuple[int, int]:
    """Convierte coordenadas normalizadas a píxeles CSS del viewport con comprobación de bordes."""
    for name, value in (("x", x_norm), ("y", y_norm)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise GeometryError(f"La coordenada {name} debe ser entera.")
        if not 0 <= value <= NORM_MAX:
            raise GeometryError(f"La coordenada {name} está fuera de [0, {NORM_MAX}].")
    if viewport_width_css < 1 or viewport_height_css < 1:
        raise GeometryError("Viewport inválido.")
    if not (scale > 0 and math.isfinite(scale)):
        raise GeometryError("Escala inválida.")

    img_w = image_width_px if image_width_px is not None else viewport_width_css
    img_h = image_height_px if image_height_px is not None else viewport_height_css
    if img_w < 1 or img_h < 1:
        raise GeometryError("Dimensiones de imagen inválidas.")

    img_x = (x_norm / NORM_MAX) * (img_w - 1)
    img_y = (y_norm / NORM_MAX) * (img_h - 1)
    x_css = _round_half_up(offset_x_css + img_x / scale)
    y_css = _round_half_up(offset_y_css + img_y / scale)

    if not (0 <= x_css < viewport_width_css and 0 <= y_css < viewport_height_css):
        raise GeometryError("La coordenada cae fuera del viewport.")
    return x_css, y_css


def image_dimensions(data: bytes) -> tuple[int, int, str]:
    """Devuelve (ancho, alto, formato) de un PNG o JPEG sin dependencias externas."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height), "png"
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > n:
                break
            seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return int(width), int(height), "jpeg"
            i += 2 + seg_len
    raise GeometryError("Formato de imagen no reconocido.")
