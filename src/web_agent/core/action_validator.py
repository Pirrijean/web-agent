"""Validación contextual de propuestas (documento maestro §8.8, §9.3, §10).

Pydantic valida la forma; este módulo valida el significado en el contexto
vigente, inmediatamente antes de ejecutar:

- compatibilidad con el modo de percepción;
- ``observation_id`` igual a la observación vigente y no caducada;
- misma página, navegación y geometría que en la captura;
- coordenadas dentro del viewport y de la superficie autorizada;
- tiempo restante suficiente; secretos disponibles;
- repetición de acciones sin efecto (anti-bucle).

Un rechazo aquí (``ActionRejectedError``) no es una violación de política: el
runner reobserva y replantea. Nunca se corrigen coordenadas especulativamente.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from web_agent.core.context import RunState, TaskContext
from web_agent.core.errors import ActionRejectedError
from web_agent.core.geometry import GeometryError, norm_to_css
from web_agent.core.observation import ElementRef
from web_agent.core.schemas import (
    DOM_ACTIONS,
    OBSERVATION_BOUND_ACTIONS,
    VISUAL_ACTIONS,
    ActionType,
    ProposedAction,
    TaskProfile,
)

MIN_ACTION_MARGIN_SECONDS = 1.0
REPEAT_RADIUS_CSS = 12


@dataclass(frozen=True)
class LiveGeometry:
    page_url: str
    page_id: str
    navigation_epoch: int
    viewport_width_css: int
    viewport_height_css: int


@dataclass(frozen=True)
class ValidatedAction:
    action: ProposedAction
    x_css: Optional[int] = None
    y_css: Optional[int] = None
    element: Optional[ElementRef] = None
    page_url: str = ""


class ContextualValidator:
    def __init__(self, profile: TaskProfile, *, clock=time.monotonic):
        self.profile = profile
        self.clock = clock

    def validate(
        self,
        action: ProposedAction,
        ctx: TaskContext,
        state: RunState,
        live: Optional[LiveGeometry],
    ) -> ValidatedAction:
        t = action.action
        mode = ctx.perception_mode

        # 1. Modo de percepción explícito (§9.1).
        if mode == "visual_only" and t in DOM_ACTIONS:
            raise ActionRejectedError("mode_incompatible", f"{t.value} no está disponible en modo visual_only.")
        if mode == "dom" and t in VISUAL_ACTIONS:
            raise ActionRejectedError("mode_incompatible", f"{t.value} no está disponible en modo dom.")

        # 2. Deadline y espera acotada.
        remaining = ctx.deadline.remaining()
        if remaining <= MIN_ACTION_MARGIN_SECONDS:
            raise ActionRejectedError("deadline", "Tiempo insuficiente para una nueva acción.")
        if t == ActionType.WAIT and action.duration_seconds > remaining - MIN_ACTION_MARGIN_SECONDS:
            raise ActionRejectedError("wait_exceeds_deadline", "La espera supera el tiempo restante.")

        # 3. Secretos: solo referencias autorizadas y existentes.
        if action.secret_ref is not None:
            allowed = self.profile.security.allowed_secret_names & ctx.secret_names
            if action.secret_ref not in allowed:
                raise ActionRejectedError("secret_unavailable", "Referencia de secreto no autorizada.")

        if t not in OBSERVATION_BOUND_ACTIONS:
            return ValidatedAction(action=action, page_url=live.page_url if live else "")

        # 4. Vigencia de la observación (§4.3, §9.3).
        if live is None:
            raise ActionRejectedError("no_live_page", "No hay página activa para validar la acción.")
        obs = state.current_visual if t in VISUAL_ACTIONS else state.current_dom
        if obs is None:
            raise ActionRejectedError("no_observation", "No existe una observación vigente del tipo requerido.")
        if action.observation_id != obs.observation_id:
            raise ActionRejectedError("stale_observation", "La propuesta no corresponde a la observación vigente.")
        if state.observation_stale:
            raise ActionRejectedError("stale_observation", "La observación vigente quedó invalidada por una acción.")
        if self.clock() - obs.captured_at_monotonic > self.profile.perception.observation_max_age_seconds:
            raise ActionRejectedError("observation_expired", "La observación superó la antigüedad máxima.")
        if obs.geometry_revision != state.geometry_revision:
            raise ActionRejectedError("geometry_changed", "La geometría cambió desde la captura.")
        if obs.page_id != live.page_id or obs.navigation_epoch != live.navigation_epoch or obs.page_url != live.page_url:
            raise ActionRejectedError("page_changed", "La página cambió desde la captura.")

        # 5. DOM: referencia vigente, única y habilitada.
        if t in DOM_ACTIONS:
            el = state.current_dom.get(action.target_element_id) if state.current_dom else None
            if el is None:
                raise ActionRejectedError("element_not_found", "El elemento no existe en la observación vigente.")
            if not el.unique:
                raise ActionRejectedError("ambiguous_target", "El elemento es ambiguo; no se actúa a ciegas.")
            if not el.enabled and t != ActionType.EXTRACT:
                raise ActionRejectedError("element_disabled", "El elemento está deshabilitado.")
            return ValidatedAction(action=action, element=el, page_url=live.page_url)

        # 6. Visual: geometría y superficie autorizada (§9.2).
        vis = state.current_visual
        if (live.viewport_width_css, live.viewport_height_css) != (vis.viewport_width_css, vis.viewport_height_css):
            raise ActionRejectedError("geometry_changed", "El viewport cambió desde la captura.")
        x_css = y_css = None
        if action.x is not None:
            try:
                x_css, y_css = norm_to_css(
                    action.x,
                    action.y,
                    viewport_width_css=vis.viewport_width_css,
                    viewport_height_css=vis.viewport_height_css,
                    image_width_px=vis.image_width_px,
                    image_height_px=vis.image_height_px,
                    offset_x_css=vis.transform.offset_x_css,
                    offset_y_css=vis.transform.offset_y_css,
                    scale=vis.transform.scale,
                )
            except GeometryError as exc:
                raise ActionRejectedError("out_of_bounds", str(exc)) from None
            surface = self.profile.perception.authorized_surface
            if surface is not None and not surface.contains(x_css, y_css):
                raise ActionRejectedError("outside_surface", "El punto está fuera de la superficie autorizada.")

        # 7. Anti-bucle: misma acción en el mismo punto sin efecto observado (§13.3).
        self._check_repetition(action, state, x_css, y_css)

        return ValidatedAction(action=action, x_css=x_css, y_css=y_css, page_url=live.page_url)

    @staticmethod
    def _check_repetition(action: ProposedAction, state: RunState, x_css, y_css) -> None:
        recent = list(state.history)[-2:]
        if len(recent) < 2:
            return
        same = 0
        for rec in recent:
            if rec.action != action.action.value or rec.screen_changed is not False:
                continue
            if x_css is None or rec.coords is None:
                if x_css is None and rec.coords is None:
                    same += 1
                continue
            if abs(rec.coords[0] - x_css) <= REPEAT_RADIUS_CSS and abs(rec.coords[1] - y_css) <= REPEAT_RADIUS_CSS:
                same += 1
        if same >= 2:
            raise ActionRejectedError(
                "repeated_without_effect",
                "La misma acción ya se repitió sin efecto observable; elige una alternativa.",
            )
