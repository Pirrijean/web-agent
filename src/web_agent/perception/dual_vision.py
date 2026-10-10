"""Visión dual: miniatura global + recorte ROI (Mitigación 4).

Se usa solo en Nivel 2 y solo si está activada. El guardián de cambios decide:
si la pantalla cambió en varias zonas dispersas o en gran parte, NO se recorta
y el modelo recibe la captura completa (no se pierde visión periférica).

Coordenadas: la miniatura conserva la proporción del viewport, así que las
coordenadas normalizadas 0..1000 sobre ella son las mismas que sobre la
captura completa. Para el ROI el modelo puede responder con
``"coord_space": "roi"`` y el núcleo transforma de forma determinista
(``roi_to_global``); el modelo nunca hace la aritmética.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from web_agent.perception.image_ops import change_map, crop_roi, thumbnail

THUMB_SIZE = (400, 250)
ROI_SIZE = (500, 350)
MAX_DISPERSED_CLUSTERS = 2
MAX_CHANGED_FRACTION = 0.35


@dataclass(frozen=True)
class DualView:
    thumbnail: bytes
    roi: bytes
    roi_box_norm: tuple[int, int, int, int]  # (x0, y0, x1, y1) en 0..1000 globales
    thumbnail_mime: str = "image/jpeg"
    roi_mime: str = "image/jpeg"

    def meta(self) -> dict:
        return {
            "vista": "dual",
            "imagen_1": f"miniatura de TODA la pantalla ({THUMB_SIZE[0]}x{THUMB_SIZE[1]}); coordenadas 0..1000 globales",
            "imagen_2": "recorte nítido de la zona activa",
            "roi_norm": list(self.roi_box_norm),
            "roi_coordenadas": 'para apuntar con el recorte usa "coord_space": "roi" y x,y 0..1000 relativos al recorte',
        }


def build_dual_view(
    current: bytes,
    previous: Optional[bytes],
    focus: Optional[tuple[int, int]],
    *,
    width: int,
    height: int,
) -> Optional[DualView]:
    """Devuelve la vista dual o ``None`` si conviene enviar la captura completa."""
    if previous is None or focus is None:
        return None
    changes = change_map(previous, current)
    if changes.clusters > MAX_DISPERSED_CLUSTERS or changes.changed_fraction > MAX_CHANGED_FRACTION:
        return None  # cambios dispersos o grandes: guardián activa la vista completa
    center = focus
    if changes.bbox is not None:
        x0, y0, x1, y1 = changes.bbox
        center = ((x0 + x1) // 2, (y0 + y1) // 2)
    roi = crop_roi(current, center, ROI_SIZE)
    if not roi_contains(roi.box, focus) and changes.bbox is None:
        return None
    return DualView(thumbnail(current, THUMB_SIZE), roi.image, roi.box_norm(width, height))


def roi_contains(box: tuple[int, int, int, int], point: tuple[int, int]) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def roi_to_global(x: int, y: int, roi_box_norm: tuple[int, int, int, int]) -> tuple[int, int]:
    x0, y0, x1, y1 = roi_box_norm
    gx = x0 + round(x * (x1 - x0) / 1000)
    gy = y0 + round(y * (y1 - y0) / 1000)
    return max(0, min(1000, gx)), max(0, min(1000, gy))
