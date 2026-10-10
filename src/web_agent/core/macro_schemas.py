"""Contrato de macro-acciones del Nivel 2 (``ProposedMacroAction``).

Una macro es una propuesta NO CONFIABLE de 1..5 pasos sobre la MISMA observación.
Reglas estructurales (las de riesgo las impone la política en el runner):

- el primer paso tiene la misma semántica que una acción de Nivel 1 (puede ser
  cualquier acción del catálogo cerrado y cualquier riesgo que la política admita);
- los pasos siguientes forman la ráfaga y solo pueden ser ``click_xy``,
  ``double_click``, ``type_text`` o ``press_key``; el runner exige además que
  su riesgo sea ≤ R1 y corta la ráfaga ante cualquier cambio inesperado;
- si el modelo devuelve una macro cuando el Nivel 2 no está permitido, solo se
  toma el primer paso (escalamiento a Nivel 1);
- un paso inválido trunca la ráfaga en ese punto; si el inválido es el primero,
  la respuesta entera se rechaza.

Cada paso se valida con ``ProposedAction`` (``extra="forbid"``): una macro no
amplía el catálogo de acciones ni sus parámetros.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from web_agent.core.errors import ModelOutputError
from web_agent.core.schemas import RISK_ORDER, ActionType, ProposedAction, RiskLevel
from web_agent.perception.dual_vision import roi_to_global

MAX_MACRO_STEPS = 5
BURST_ACTIONS = frozenset({ActionType.CLICK_XY, ActionType.DOUBLE_CLICK, ActionType.TYPE_TEXT, ActionType.PRESS_KEY})
BURST_MAX_RISK = RiskLevel.R1
_INHERITED = ("screen_summary", "previous_step_assessment")


def risk_allowed_in_burst(risk: RiskLevel) -> bool:
    return RISK_ORDER[risk] <= RISK_ORDER[BURST_MAX_RISK]


class ProposedMacroAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    macro_action: str = Field(..., min_length=1, max_length=80)
    reasoning: str = Field("", max_length=1000)
    steps: list[dict[str, Any]] = Field(..., min_length=1, max_length=MAX_MACRO_STEPS)
    expected_final_text: Optional[str] = Field(None, max_length=120)
    screen_summary: Optional[str] = Field(None, max_length=500)
    previous_step_assessment: Optional[str] = Field(None, max_length=40)


@dataclass(frozen=True)
class ParsedProposal:
    actions: tuple[ProposedAction, ...]
    macro: Optional[ProposedMacroAction] = None
    truncated_reason: Optional[str] = None

    @property
    def is_burst(self) -> bool:
        return len(self.actions) > 1


def is_macro(raw: Any) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("steps"), list)


def _prepare_step(step: Any, macro: ProposedMacroAction, observation_id: Optional[str],
                  roi_box: Optional[tuple[int, int, int, int]], first: bool) -> dict:
    if not isinstance(step, dict):
        raise ValueError("Paso de macro no es un objeto.")
    step = {k: v for k, v in step.items() if v is not None}
    space = step.pop("coord_space", "global")
    if space == "roi":
        if roi_box is None or "x" not in step or "y" not in step:
            raise ValueError("coord_space=roi sin recorte vigente o sin coordenadas.")
        if not all(isinstance(step[k], int) and not isinstance(step[k], bool) for k in ("x", "y")):
            raise ValueError("Coordenadas ROI no enteras.")
        step["x"], step["y"] = roi_to_global(step["x"], step["y"], roi_box)
    elif space != "global":
        raise ValueError("coord_space desconocido.")
    step.setdefault("reasoning", macro.reasoning or f"Paso de la macro {macro.macro_action}")
    if observation_id is not None and step.get("observation_id") in (None, "$OBS"):
        step["observation_id"] = observation_id
    if first:
        for key in _INHERITED:
            value = getattr(macro, key)
            if value is not None:
                step.setdefault(key, value)
    return step


def parse_model_output(
    raw: Any,
    *,
    validate: Callable[[Any], ProposedAction],
    observation_id: Optional[str],
    allow_macro: bool,
    roi_box: Optional[tuple[int, int, int, int]] = None,
) -> ParsedProposal:
    """Convierte la salida del modelo en una o varias ``ProposedAction`` validadas."""
    if not is_macro(raw):
        if isinstance(raw, dict) and raw.get("coord_space") is not None:
            raw = dict(raw)
            space = raw.pop("coord_space")
            if space == "roi":
                if roi_box is None or not all(isinstance(raw.get(k), int) for k in ("x", "y")):
                    raise ModelOutputError("coord_space=roi sin recorte vigente o sin coordenadas.")
                raw["x"], raw["y"] = roi_to_global(raw["x"], raw["y"], roi_box)
            elif space != "global":
                raise ModelOutputError("coord_space desconocido.")
        return ParsedProposal((validate(raw),))

    try:
        macro = ProposedMacroAction.model_validate(raw)
    except ValidationError as exc:
        detail = "; ".join(str(e.get("msg", ""))[:120] for e in exc.errors()[:3])
        raise ModelOutputError(f"La macro no cumple el contrato. {detail}".strip()) from None

    try:
        first = validate(_prepare_step(macro.steps[0], macro, observation_id, roi_box, True))
    except ValueError as exc:
        raise ModelOutputError(f"Primer paso de la macro inválido: {exc}") from None
    if not allow_macro:
        return ParsedProposal((first,), macro, "nivel2_no_permitido")

    actions = [first]
    reason = None
    for idx, step in enumerate(macro.steps[1:], start=2):
        try:
            action = validate(_prepare_step(step, macro, observation_id, roi_box, False))
        except (ValueError, ModelOutputError):
            reason = f"paso_{idx}_invalido"
            break
        if action.action not in BURST_ACTIONS:
            reason = f"paso_{idx}_no_permitido_en_rafaga"
            break
        actions.append(action)
    return ParsedProposal(tuple(actions), macro, reason)
