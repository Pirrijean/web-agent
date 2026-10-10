"""OCR local en CPU para el Nivel 3 (RapidOCR sobre ONNX Runtime, opcional).

Medido en este proyecto (CPU x86, 1280x800): OCR completo 3–4 s por captura,
detección sola ~0,65 s y reconocimiento ~30–85 ms por caja. Por eso el motor
no lee toda la pantalla: ``find_text`` detecta cajas, descarta por forma las que
no pueden contener el texto buscado y reconoce solo las candidatas (~1,2 s).

El OCR es una señal NO CONFIABLE (igual que la captura): nunca autoriza nada por
sí mismo. Sus candidatos pasan por validador, política y ejecutor.

Degradación (Mitigación 1): si el paquete no está instalado, falla la carga o la
memoria libre es insuficiente, se usa ``NullOcrEngine`` y el Nivel 3 queda
desactivado sin afectar al resto del agente.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- tipos
@dataclass(frozen=True)
class TextBox:
    x0: int
    y0: int
    x1: int
    y1: int
    text: str = ""
    confidence: float = 0.0

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)

    def distance_to(self, point: tuple[int, int]) -> float:
        """Distancia del punto al rectángulo (0 si está dentro)."""
        px, py = point
        dx = max(self.x0 - px, 0, px - self.x1)
        dy = max(self.y0 - py, 0, py - self.y1)
        return (dx * dx + dy * dy) ** 0.5

    def with_text(self, text: str, confidence: float) -> "TextBox":
        return TextBox(self.x0, self.y0, self.x1, self.y1, text, confidence)


@dataclass(frozen=True)
class FindResult:
    matches: tuple[TextBox, ...]
    layout: tuple[TextBox, ...] = ()  # cajas detectadas (sin texto) para la firma de pantalla
    read: tuple[TextBox, ...] = field(default=())  # cajas reconocidas en esta búsqueda
    elapsed_seconds: float = 0.0


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[\"'«»“”‘’:;.,!?¡¿()\[\]{}<>|*_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_matches(candidate: str, target: str) -> bool:
    """Coincidencia exacta tras normalizar (sin parecidos aproximados: no se adivina)."""
    return bool(target) and normalize_text(candidate) == normalize_text(target)


def text_contains(candidate: str, term: str) -> bool:
    """El término aparece como palabra(s) completa(s) dentro del texto leído."""
    c, t = normalize_text(candidate), normalize_text(term)
    return bool(t) and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", c) is not None


def plausible_box_for(box: TextBox, target: str, *, contains: bool = False) -> bool:
    """¿Puede la caja contener ``target`` en una sola línea? (filtro por proporción)."""
    if box.height <= 0:
        return False
    chars = max(1, len(target))
    ratio = box.width / box.height
    expected = chars * 0.55
    if contains:
        return ratio >= 0.45 * expected
    return 0.45 * expected <= ratio <= 1.9 * expected + 0.6


# --------------------------------------------------------------------------- memoria
def available_memory_mb() -> Optional[int]:
    """Memoria disponible considerando el límite del cgroup del contenedor."""
    candidates: list[int] = []
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                candidates.append(int(line.split()[1]) // 1024)
                break
    except (OSError, ValueError):
        pass
    for limit_path, usage_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        try:
            limit_raw = Path(limit_path).read_text().strip()
            if limit_raw == "max":
                continue
            limit, usage = int(limit_raw), int(Path(usage_path).read_text().strip())
            if limit < 1 << 60:
                candidates.append(max(0, limit - usage) // (1024 * 1024))
        except (OSError, ValueError):
            continue
    return min(candidates) if candidates else None


# --------------------------------------------------------------------------- motores
class OcrEngine:
    """Interfaz común. Las subclases implementan ``_detect_sync`` y ``_read_sync``."""

    name = "base"
    available = False
    unavailable_reason = ""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _detect_sync(self, image: bytes) -> list[TextBox]:  # pragma: no cover - interfaz
        raise NotImplementedError

    def _read_sync(self, image: bytes, boxes: Sequence[TextBox]) -> list[TextBox]:  # pragma: no cover
        raise NotImplementedError

    async def _run(self, fn, *args, timeout: float):
        if not self.available:
            raise OcrUnavailable(self.unavailable_reason or "OCR no disponible.")

        def guarded():
            # Un único análisis a la vez: si el anterior sigue en curso (tras un timeout),
            # no se acumulan hilos ni memoria.
            if not self._lock.acquire(blocking=False):
                raise OcrUnavailable("OCR ocupado.")
            try:
                return fn(*args)
            finally:
                self._lock.release()

        return await asyncio.wait_for(asyncio.to_thread(guarded), timeout=timeout)

    async def detect(self, image: bytes, *, timeout: float) -> list[TextBox]:
        return await self._run(self._detect_sync, image, timeout=timeout)

    async def read(self, image: bytes, boxes: Sequence[TextBox], *, timeout: float) -> list[TextBox]:
        if not boxes:
            return []
        return await self._run(self._read_sync, image, list(boxes), timeout=timeout)

    async def find_text(self, image: bytes, targets: Iterable[str], *, timeout: float,
                        layout: Optional[Sequence[TextBox]] = None, contains: bool = False) -> FindResult:
        """Busca cada ``target`` reconociendo solo las cajas con forma compatible.

        ``contains=False`` (por defecto, Nivel 3): coincidencia exacta tras normalizar.
        ``contains=True`` (solo señales de verificación): el término aparece dentro del texto.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        targets = [t for t in targets if normalize_text(t)]
        boxes = list(layout) if layout is not None else await self.detect(image, timeout=timeout)
        candidates = [b for b in boxes if any(plausible_box_for(b, t, contains=contains) for t in targets)]
        if contains:
            candidates = candidates[:24]  # cota de coste: señales, no exhaustividad
        remaining = max(0.05, timeout - (loop.time() - started))
        read = await self.read(image, candidates, timeout=remaining) if candidates else []
        match = text_contains if contains else text_matches
        matches = tuple(b for b in read if any(match(b.text, t) for t in targets))
        return FindResult(matches, tuple(boxes), tuple(read), loop.time() - started)


class OcrUnavailable(RuntimeError):
    pass


class NullOcrEngine(OcrEngine):
    name = "none"
    available = False

    def __init__(self, reason: str = "OCR desactivado.") -> None:
        super().__init__()
        self.unavailable_reason = reason


class RapidOcrEngine(OcrEngine):
    """RapidOCR (PP-OCRv4 móvil, sin clasificador de ángulo) en CPU."""

    name = "rapidocr"

    def __init__(self, *, threads: int = 2) -> None:
        super().__init__()
        from rapidocr import RapidOCR  # import diferido: dependencia opcional
        from rapidocr.utils.typings import ModelType, OCRVersion

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        params = {
            "Global.use_cls": False,
            "Global.log_level": "warning",
            "EngineConfig.onnxruntime.intra_op_num_threads": max(1, threads),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.ocr_version": OCRVersion.PPOCRV4,
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Rec.model_type": ModelType.MOBILE,
        }
        self._engine = RapidOCR(params=params)
        self._warm_up()
        self.available = True

    def _warm_up(self) -> None:
        """La primera inferencia de cada sesión ONNX es varias veces más lenta (~5 s medidos):
        se paga aquí, durante la carga acotada, y no dentro del plazo de un paso."""
        import numpy as np

        # Mismo tamaño que una captura real (1280x800): una imagen pequeña se reescalaría hasta
        # 736 px de lado menor y el calentamiento costaría varias veces más que un análisis real.
        canvas = np.full((800, 1280, 3), 255, dtype=np.uint8)
        canvas[380:404, 500:780] = 0
        self._engine(canvas, use_det=True, use_cls=False, use_rec=False)
        self._engine(canvas[376:408, 490:790], use_det=False, use_cls=False, use_rec=True)

    @staticmethod
    def _array(image: bytes):
        import numpy as np
        from PIL import Image

        return np.array(Image.open(io.BytesIO(image)).convert("RGB"))

    def _detect_sync(self, image: bytes) -> list[TextBox]:
        arr = self._array(image)
        result = self._engine(arr, use_det=True, use_cls=False, use_rec=False)
        boxes = []
        for b in result.boxes if result.boxes is not None else []:
            xs, ys = b[:, 0], b[:, 1]
            boxes.append(TextBox(int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
        return boxes

    def _read_sync(self, image: bytes, boxes: Sequence[TextBox]) -> list[TextBox]:
        arr = self._array(image)
        h, w = arr.shape[:2]
        out = []
        for b in boxes:
            crop = arr[max(0, b.y0 - 2): min(h, b.y1 + 2), max(0, b.x0 - 2): min(w, b.x1 + 2)]
            if crop.size == 0:
                continue
            r = self._engine(crop, use_det=False, use_cls=False, use_rec=True)
            txts, scores = list(r.txts or []), list(r.scores or [])
            if txts:
                out.append(b.with_text(str(txts[0])[:200], float(scores[0]) if scores else 0.0))
        return out


def create_ocr_engine(*, enabled: bool, min_free_memory_mb: int, threads: int = 2) -> OcrEngine:
    if not enabled:
        return NullOcrEngine("Nivel 3 desactivado por configuración.")
    free = available_memory_mb()
    if free is not None and free < min_free_memory_mb:
        return NullOcrEngine(f"Memoria libre insuficiente para OCR ({free} MB < {min_free_memory_mb} MB).")
    try:
        return RapidOcrEngine(threads=threads)
    except ImportError:
        return NullOcrEngine("OCR no instalado (instala el extra 'ocr').")
    except Exception as exc:  # modelos no descargables, ONNX incompatible…
        logger.warning("No se pudo iniciar el OCR: %s", type(exc).__name__)
        return NullOcrEngine("No se pudo iniciar el OCR.")
