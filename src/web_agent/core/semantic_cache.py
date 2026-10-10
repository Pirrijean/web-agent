"""Caché semántica de pantallas para el Nivel 3 (Mitigación 5).

Firma de pantalla: SHA-256 de la DISPOSICIÓN de las cajas de texto detectadas
(no de los píxeles ni del texto completo):

- leer todos los textos cuesta 3–4 s en CPU; la detección de cajas ya se calcula
  en la búsqueda dirigida, así que la firma sale gratis;
- se enmascaran las zonas dinámicas habituales (35 px inferiores de la barra de
  tareas y esquina superior derecha) y las cajas se cuantizan a una rejilla de
  16 px, de modo que relojes, contadores y el cursor no alteran la firma.

Clave compuesta (evita confundir formularios con la misma disposición, p.ej.
«Crear» y «Editar»): firma de pantalla + objetivo literal + paso del flujo +
última acción con éxito.

Una entrada NUNCA se ejecuta a ciegas: el Nivel 3 vuelve a reconocer el texto en
la caja guardada, repite el análisis de proximidad de peligro y pasa por la
política (solo R0). La caché vive en memoria durante una tarea.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, Optional

from web_agent.perception.ocr_engine import TextBox, normalize_text

GRID_PX = 16
MASK_BOTTOM_PX = 35
MASK_TOP_RIGHT = (220, 40)  # ancho, alto de la esquina superior derecha
MAX_ENTRIES = 64


def _masked(box: TextBox, width: int, height: int) -> bool:
    if box.y0 >= height - MASK_BOTTOM_PX:
        return True
    return box.x0 >= width - MASK_TOP_RIGHT[0] and box.y1 <= MASK_TOP_RIGHT[1]


def screen_signature(layout: Iterable[TextBox], width: int, height: int) -> Optional[str]:
    cells = sorted(
        {(b.x0 // GRID_PX, b.y0 // GRID_PX, max(1, b.width // GRID_PX)) for b in layout
         if not _masked(b, width, height)}
    )
    if len(cells) < 3:  # pantalla casi vacía: firma poco informativa
        return None
    digest = hashlib.sha256(f"{width}x{height}|".encode() + repr(cells).encode()).hexdigest()
    return digest[:32]


@dataclass(frozen=True)
class CacheKey:
    signature: str
    target: str
    flow_step: int
    last_action: str

    @classmethod
    def build(cls, signature: str, target: str, flow_step: int, last_action: Optional[str]) -> "CacheKey":
        return cls(signature, normalize_text(target), flow_step, last_action or "-")


class SemanticCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: "OrderedDict[CacheKey, TextBox]" = OrderedDict()
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> Optional[TextBox]:
        box = self._entries.get(key)
        if box is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return box

    def put(self, key: CacheKey, box: TextBox) -> None:
        self._entries[key] = box
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, key: CacheKey) -> None:
        self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)
