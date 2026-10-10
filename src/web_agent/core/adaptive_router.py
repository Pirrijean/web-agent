"""Motor de enrutamiento adaptativo tri-nivel (propuesta «Motor Jerárquico Tri-Nivel»).

Niveles (numeración de la propuesta):

- **Nivel 3 – vía local** (sin inferencia, coste 0): clic sobre un texto literal
  del objetivo localizado por OCR dirigido o por la caché semántica. Solo R0.
- **Nivel 2 – macro-acciones**: una llamada al modelo puede devolver hasta 5 pasos
  sobre la misma observación; la ráfaga posterior al primer paso solo admite
  R0/R1 y se corta ante cambios inesperados de pantalla.
- **Nivel 1 – bucle clásico**: una acción por llamada, validación y política en
  cada turno, verificación independiente. Es el fallback de todo.

El router NO ejecuta nada: decide el nivel máximo permitido para cada paso y
genera candidatos de Nivel 3. Toda acción pasa por el mismo validador,
política, ejecutor y auditoría que el resto del agente.

Escalamiento ascendente y sin ciclos:
1. un fallo de Nivel 3 (sin efecto, rechazo, riesgo > R0, ambigüedad, peligro
   cercano) pone en lista negra ese objetivo y limita el paso siguiente a Nivel 2;
2. una ráfaga interrumpida o una acción fallida limita el paso siguiente a Nivel 1;
3. tras resolver un obstáculo en Nivel 1, el paso siguiente empieza como máximo en
   Nivel 2; un éxito en Nivel 2 restaura el máximo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from web_agent.core.context import RunState, TaskContext
from web_agent.core.policy import DESTRUCTIVE_PATTERN, fold_text
from web_agent.core.schemas import ActionType, ProposedAction
from web_agent.core.semantic_cache import CacheKey, SemanticCache, screen_signature
from web_agent.perception.ocr_engine import (
    FindResult,
    NullOcrEngine,
    OcrEngine,
    OcrUnavailable,
    TextBox,
    text_matches,
)

LEVEL_LOCAL = 3
LEVEL_MACRO = 2
LEVEL_CLASSIC = 1

MIN_OCR_CONFIDENCE = 0.90
MAX_ATTEMPTS_PER_TARGET = 3
DANGER_RADIUS_PX = 80
_DANGER_EXTRA = re.compile(r"\b(advertencia|warning|peligro|permanente|permanentemente|irreversible|cuidado)\b")

# Verbos de clic y de escritura para extraer objetivos literales del enunciado.
_QUOTED = re.compile(r"[\"'«“‘]([^\"'«»“”‘’\n]{1,40})[\"'»”’]")
_CLICK_VERB = re.compile(
    r"\b(haz(?:\s+un)?\s+clic|haga\s+clic|clic|click|pulsa|pulse|presiona|presione|oprime|oprima|"
    r"selecciona|seleccione|elige|elija|abre|abra|entra|entre|accede|acceda|ve|ir|navega|navegue)\b"
)
_TYPE_VERB = re.compile(
    r"\b(escribe|escriba|ingresa|ingrese|introduce|introduzca|teclea|teclee|rellena|rellene|"
    r"completa|complete|pon|ponga|busca|busque|buscar|texto|valor|nombre|usuario|contrasena|clave)\b"
)
_CHAIN = re.compile(
    r"^\s*,?\s*(?:y|e)?\s*(?:luego|despues|a continuacion)?\s*,?\s*"
    r"(?:en|a|al|sobre)?\s*(?:el|la|los|las)?\s*"
    r"(?:boton|menu|pestana|opcion|enlace|icono|elemento|apartado|seccion)?\s*$"
)


@dataclass(frozen=True)
class LiteralTarget:
    index: int
    text: str


def extract_click_targets(objective: str) -> tuple[LiteralTarget, ...]:
    """Textos entrecomillados que el objetivo pide pulsar, en orden.

    Solo se aceptan literales precedidos por un verbo de clic (o encadenados a un
    literal de clic anterior) y sin verbo de escritura entre ambos: «escribe
    'Empresa S.A.'» no es un objetivo de clic.
    """
    targets: list[LiteralTarget] = []
    prev_end = 0
    prev_was_click = False
    for m in _QUOTED.finditer(objective):
        window = fold_text(objective[max(prev_end, m.start() - 60): m.start()])
        verbs = list(_CLICK_VERB.finditer(window))
        is_click = False
        if verbs:
            after_verb = window[verbs[-1].end():]
            is_click = not _TYPE_VERB.search(after_verb) and len(after_verb.split()) <= 6
        elif prev_was_click and _CHAIN.match(fold_text(objective[prev_end: m.start()])):
            is_click = True
        text = m.group(1).strip()
        if is_click and text:
            targets.append(LiteralTarget(len(targets), text))
        prev_end = m.end()
        prev_was_click = is_click
    return tuple(targets)


@dataclass(frozen=True)
class Level3Candidate:
    action: ProposedAction
    target: LiteralTarget
    box: TextBox
    cache_key: Optional[CacheKey]
    from_cache: bool
    elapsed_seconds: float


@dataclass
class Level3Attempt:
    candidate: Optional[Level3Candidate]
    reason: str  # código de auditoría cuando no hay candidato


@dataclass
class _Pending:
    candidate: Level3Candidate


@dataclass
class RouterStats:
    level3_actions: int = 0
    level3_escalations: int = 0
    macro_bursts: int = 0
    burst_steps: int = 0
    burst_aborts: int = 0
    level1_steps: int = 0
    cache_hits: int = 0
    extra: dict = field(default_factory=dict)


class AdaptiveRouter:
    def __init__(
        self,
        *,
        ocr: Optional[OcrEngine] = None,
        level3_enabled: bool = True,
        level2_enabled: bool = True,
        dual_vision_enabled: bool = False,
        level3_timeout_seconds: float = 2.5,
        cache: Optional[SemanticCache] = None,
    ) -> None:
        self.ocr = ocr or NullOcrEngine()
        self.level3_enabled = level3_enabled and self.ocr.available
        self.level2_enabled = level2_enabled
        self.dual_vision_enabled = dual_vision_enabled
        self.level3_timeout_seconds = level3_timeout_seconds
        self.cache = cache or SemanticCache()
        self.stats = RouterStats()
        self._max = LEVEL_LOCAL if self.level3_enabled else (LEVEL_MACRO if level2_enabled else LEVEL_CLASSIC)
        self._next_cap = self._max
        self.cap = self._max
        self._obstacle = False
        self._targets: tuple[LiteralTarget, ...] = ()
        self._done: set[int] = set()
        self._blacklist: set[int] = set()
        self._pending: Optional[_Pending] = None
        self._attempts: dict[int, list[str]] = {}  # objetivo -> hashes de pantallas ya analizadas
        self.previous_image: Optional[bytes] = None
        self.expected_text: Optional[str] = None

    def attach_ocr(self, ocr: OcrEngine, ctx: TaskContext) -> None:
        """Activa el Nivel 3 cuando el OCR termina de cargar en segundo plano."""
        if not ocr.available or ctx.perception_mode == "dom":
            return
        self.ocr = ocr
        self.level3_enabled = True
        self._targets = extract_click_targets(ctx.objective)
        self._max = LEVEL_LOCAL

    # ------------------------------------------------------------ ciclo
    def start(self, ctx: TaskContext) -> None:
        self._targets = extract_click_targets(ctx.objective) if self.level3_enabled else ()
        if ctx.perception_mode == "dom":
            # Los niveles 3 y 2 trabajan sobre coordenadas visuales.
            self.level3_enabled = False
            self.level2_enabled = False
            self._max = self._next_cap = self.cap = LEVEL_CLASSIC

    def describe(self) -> dict:
        return {
            "ocr": self.ocr.name if self.ocr.available else f"no ({self.ocr.unavailable_reason})",
            "nivel3": self.level3_enabled,
            "nivel2": self.level2_enabled,
            "vision_dual": self.dual_vision_enabled,
            "objetivos_literales": [t.text for t in self._targets],
        }

    def begin_step(self, state: RunState) -> Optional[str]:
        """Tras observar: resuelve el efecto del Nivel 3 pendiente y fija el nivel del paso.

        Devuelve un código de evento para auditoría o ``None``.
        """
        event = None
        if self._pending is not None:
            cand = self._pending.candidate
            self._pending = None
            changed = bool(state.history and state.history[-1].screen_changed)
            if changed:
                self._done.add(cand.target.index)
                if cand.cache_key is not None:
                    self.cache.put(cand.cache_key, cand.box)
                event = "level3_effect_observed"
            else:
                self._blacklist.add(cand.target.index)
                if cand.cache_key is not None:
                    self.cache.invalidate(cand.cache_key)
                self._next_cap = min(self._next_cap, LEVEL_MACRO)
                self.stats.level3_escalations += 1
                event = "level3_no_effect"
        self.cap, self._next_cap = self._next_cap, self._max
        return event

    def remember_image(self, image: Optional[bytes]) -> None:
        """Guarda la captura vigente como «anterior» para la visión dual del paso siguiente."""
        self.previous_image = image

    # ------------------------------------------------------------ eventos
    def on_level3_escalated(self, candidate: Optional[Level3Candidate] = None) -> None:
        """Escala al Nivel 2 en el paso siguiente; si hay candidato, ese objetivo no se reintenta en local."""
        self.stats.level3_escalations += 1
        self._next_cap = min(self._next_cap, LEVEL_MACRO)
        if candidate is not None:
            self._blacklist.add(candidate.target.index)

    def on_level3_executed(self, cand: Level3Candidate) -> None:
        self.stats.level3_actions += 1
        if cand.from_cache:
            self.stats.cache_hits += 1
        self._pending = _Pending(cand)

    def on_model_step(self, level: int, ok: bool) -> None:
        if level == LEVEL_CLASSIC:
            self.stats.level1_steps += 1
        if not ok:
            self._obstacle = True
            self._next_cap = LEVEL_CLASSIC
            return
        if level == LEVEL_CLASSIC and self._obstacle:
            self._obstacle = False
            self._next_cap = min(self._next_cap, LEVEL_MACRO)
        elif level == LEVEL_MACRO:
            self._obstacle = False

    def on_burst(self, executed: int, aborted: bool) -> None:
        self.stats.macro_bursts += 1
        self.stats.burst_steps += executed
        if aborted:
            self.stats.burst_aborts += 1
            self._obstacle = True
            self._next_cap = LEVEL_CLASSIC

    def force_classic(self) -> None:
        """Limita a Nivel 1 el paso en curso y el siguiente."""
        self.cap = LEVEL_CLASSIC
        self._next_cap = LEVEL_CLASSIC

    # ------------------------------------------------------------ consultas
    def macro_allowed(self, ctx: TaskContext, state: RunState) -> bool:
        return (
            self.level2_enabled
            and self.cap >= LEVEL_MACRO
            and ctx.perception_mode in ("visual_only", "auto")
            and ctx.deadline.remaining() > 20.0
        )

    def dual_vision_allowed(self, ctx: TaskContext, state: RunState) -> bool:
        return self.dual_vision_enabled and self.macro_allowed(ctx, state)

    def next_target(self) -> Optional[LiteralTarget]:
        for t in self._targets:
            if t.index not in self._done and t.index not in self._blacklist:
                return t
        return None

    # ------------------------------------------------------------ Nivel 3
    async def try_level_3(self, ctx: TaskContext, state: RunState) -> Level3Attempt:
        if not self.level3_enabled or self.cap < LEVEL_LOCAL:
            return Level3Attempt(None, "level3_not_allowed")
        target = self.next_target()
        if target is None:
            return Level3Attempt(None, "level3_no_literal_target")
        obs, image = state.current_visual, state.current_image
        if obs is None or image is None or state.observation_stale or obs.sensitive:
            return Level3Attempt(None, "level3_no_observation")
        if state.feedback:
            return Level3Attempt(None, "level3_pending_feedback")
        seen = self._attempts.setdefault(target.index, [])
        if obs.image_sha256 in seen:
            return Level3Attempt(None, "level3_screen_already_analyzed")
        if len(seen) >= MAX_ATTEMPTS_PER_TARGET:
            self._blacklist.add(target.index)
            return Level3Attempt(None, "level3_attempts_exhausted")
        seen.append(obs.image_sha256)
        budget = min(self.level3_timeout_seconds, ctx.deadline.remaining() - 5.0)
        if budget <= 0.2:
            return Level3Attempt(None, "level3_no_time")

        w, h = obs.image_width_px, obs.image_height_px
        last = state.history[-1] if state.history else None
        last_sig = f"{last.action}@{last.coords}" if last and last.coords else (last.action if last else None)
        try:
            layout = await self.ocr.detect(image, timeout=budget)
            signature = screen_signature(layout, w, h)
            key = (CacheKey.build(signature, target.text, len(self._done), last_sig)
                   if signature is not None else None)
            cached = self.cache.get(key) if key is not None else None
            from_cache = False
            if cached is not None:
                read = await self.ocr.read(image, [cached], timeout=budget)
                matches = tuple(b for b in read if text_matches(b.text, target.text))
                result = FindResult(matches, tuple(layout), tuple(read))
                from_cache = bool(matches)
            if not from_cache:
                result = await self.ocr.find_text(image, [target.text], timeout=budget, layout=layout)
        except (OcrUnavailable, TimeoutError):
            # Fallo transitorio (CPU saturada, motor ocupado): la pantalla no cuenta como analizada.
            seen.remove(obs.image_sha256)
            self.on_level3_escalated()
            return Level3Attempt(None, "level3_ocr_unavailable")

        if len(result.matches) != 1:
            if len(result.matches) > 1:
                self._blacklist.add(target.index)  # ambiguo: no se reintenta en local
            self.on_level3_escalated()
            return Level3Attempt(None, "level3_ambiguous" if result.matches else "level3_text_not_found")
        box = result.matches[0]
        if box.confidence < MIN_OCR_CONFIDENCE:
            self.on_level3_escalated()
            return Level3Attempt(None, "level3_low_confidence")
        if await self._danger_nearby(image, box, layout, budget):
            self._blacklist.add(target.index)
            self.on_level3_escalated()
            return Level3Attempt(None, "level3_danger_nearby")

        cx, cy = box.center
        action = ProposedAction(
            action=ActionType.CLICK_XY,
            x=max(0, min(1000, round(cx * 1000 / w))),
            y=max(0, min(1000, round(cy * 1000 / h))),
            observation_id=obs.observation_id,
            reasoning=f"Nivel 3: el objetivo pide pulsar '{target.text}' y el OCR local lo localiza sin ambigüedad.",
            target_description=f"texto '{target.text}' localizado por OCR",
            expected_effect=f"se abre o activa '{target.text}'",
            effect_class="navigate",
            reason_code="level3_literal_click",
        )
        return Level3Attempt(Level3Candidate(action, target, box, key, from_cache, result.elapsed_seconds), "ok")

    async def _danger_nearby(self, image: bytes, box: TextBox, layout, budget: float) -> bool:
        """Mitigación 3: términos destructivos o de advertencia a ≤ 80 px del objetivo."""
        center = box.center
        near = [b for b in layout
                if b.distance_to(center) <= DANGER_RADIUS_PX
                and (b.x0, b.y0, b.x1, b.y1) != (box.x0, box.y0, box.x1, box.y1)]
        if not near:
            return False
        try:
            read = await self.ocr.read(image, near[:12], timeout=budget)
        except (OcrUnavailable, TimeoutError):
            return True  # sin poder comprobar el entorno, no se actúa localmente
        for b in read:
            folded = fold_text(b.text)
            if DESTRUCTIVE_PATTERN.search(folded) or _DANGER_EXTRA.search(folded):
                return True
        return False
