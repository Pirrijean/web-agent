"""Verificación de postcondiciones (documento maestro §8.12, §13).

- ``DONE`` solo solicita verificación; nunca es éxito por sí mismo.
- La diferencia de píxeles es una señal de diagnóstico, nunca un veredicto.
- Modo DOM con ``success_condition``: comprobación determinista.
- Modo visual: evaluación VLM independiente de la propuesta, registrada como
  ``visual_interpreted`` (no es certeza absoluta).
- Sin criterio verificable el resultado es ``incierto``; no se inventa.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from web_agent.core.context import RunState, TaskContext
from web_agent.core.result import EvidenceKind, VerificationResult, VerificationStatus
from web_agent.core.schemas import TaskProfile
from web_agent.models.base import BaseModelAdapter, VerificationRequest
from web_agent.security.network_policy import NetworkPolicyValidator


class _ModelVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    status: VerificationStatus
    evidence: str = Field("", max_length=1000)
    criteria: tuple[str, ...] = ()
    unmet_criteria: tuple[str, ...] = ()


class Verifier:
    def __init__(self, profile: TaskProfile, network_validator: NetworkPolicyValidator):
        self.profile = profile
        self.network_validator = network_validator

    async def verify_dom_condition(self, page) -> Optional[VerificationResult]:
        condition = self.profile.verification.success_condition
        if condition is None:
            return None
        if page is None or page.is_closed() or not self.network_validator.is_url_allowed(page.url):
            return VerificationResult(status=VerificationStatus.NO_VERIFICADO, evidence_kind=EvidenceKind.DOM_CONDITION,
                                      evidence="Página no disponible o fuera de ámbito.")
        checked = []
        if condition.url_equals is not None:
            checked.append("url_equals")
            if page.url != condition.url_equals:
                return VerificationResult(status=VerificationStatus.NO_VERIFICADO,
                                          evidence_kind=EvidenceKind.DOM_CONDITION,
                                          evidence="La URL final no coincide.", criteria=tuple(checked))
        if condition.visible_text is not None:
            checked.append("visible_text")
            locator = page.get_by_text(condition.visible_text, exact=True)
            if await locator.count() != 1 or not await locator.is_visible():
                return VerificationResult(status=VerificationStatus.NO_VERIFICADO,
                                          evidence_kind=EvidenceKind.DOM_CONDITION,
                                          evidence="El texto esperado no está visible.", criteria=tuple(checked))
        if condition.element_role is not None:
            checked.append("element")
            locator = page.get_by_role(condition.element_role, name=condition.element_name, exact=True)
            if await locator.count() != 1 or not await locator.is_visible():
                return VerificationResult(status=VerificationStatus.NO_VERIFICADO,
                                          evidence_kind=EvidenceKind.DOM_CONDITION,
                                          evidence="El elemento esperado no está visible.", criteria=tuple(checked))
        return VerificationResult(status=VerificationStatus.VERIFICADO, evidence_kind=EvidenceKind.DOM_CONDITION,
                                  evidence="Condición final del perfil comprobada en la página.",
                                  criteria=tuple(checked))

    async def verify_visual(
        self,
        model: BaseModelAdapter,
        ctx: TaskContext,
        state: RunState,
        *,
        claimed_evidence: Optional[str],
        timeout: float,
        max_output_tokens: int,
        local_hints: tuple[str, ...] = (),
    ) -> VerificationResult:
        obs = state.current_visual
        if obs is None or state.current_image is None or state.observation_stale:
            return VerificationResult(status=VerificationStatus.INCIERTO, evidence="Sin observación vigente.")
        if not getattr(model, "supports_vision", False):
            return VerificationResult(status=VerificationStatus.INCIERTO,
                                      evidence="El modelo configurado no admite imágenes.")
        request = VerificationRequest(
            objective=ctx.objective,
            observation_meta=obs.model_metadata(),
            image_bytes=state.current_image,
            image_mime=f"image/{obs.image_format}",
            history=tuple(r.to_prompt_line() for r in state.history),
            claimed_evidence=claimed_evidence,
            local_hints=tuple(h[:200] for h in local_hints[:6]),
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout,
        )
        raw = await asyncio.wait_for(model.verify_outcome(request), timeout=timeout)
        try:
            verdict = _ModelVerdict.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            return VerificationResult(status=VerificationStatus.INCIERTO,
                                      evidence_kind=EvidenceKind.VISUAL_INTERPRETED,
                                      evidence="La verificación devolvió una respuesta inválida.")
        evidence = verdict.evidence
        if verdict.unmet_criteria:
            evidence = (evidence + " | Pendiente: " + "; ".join(verdict.unmet_criteria))[:1000]
        return VerificationResult(
            status=verdict.status,
            evidence_kind=EvidenceKind.VISUAL_INTERPRETED,
            evidence=evidence,
            criteria=tuple(c[:120] for c in verdict.criteria[:10]),
        )
