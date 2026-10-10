"""Proveedor determinista para pruebas (documento maestro §16, §20.14).

No es un sustituto silencioso del VLM en evaluación (ADR-003 §12): solo se usa
si se configura explícitamente. Admite pasos como dict/ProposedAction o como
funciones que reciben la ``ModelRequest`` (para calcular coordenadas o citar
el ``observation_id`` vigente). El marcador ``"$OBS"`` se sustituye por el
``observation_id`` actual.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from web_agent.core.schemas import ProposedAction
from web_agent.models.base import BaseModelAdapter, ModelRequest, VerificationRequest

OBS_PLACEHOLDER = "$OBS"


class ScriptedModelAdapter(BaseModelAdapter):
    supports_vision = True
    model_id = "scripted"

    def __init__(
        self,
        actions: Optional[Iterable[Any]] = None,
        verifications: Optional[Iterable[Any]] = None,
    ):
        super().__init__()
        self.actions = list(actions or [])
        self.verifications = list(verifications or [])
        self.call_count = 0
        self.verify_count = 0
        self.requests: list[ModelRequest] = []
        self.verify_requests: list[VerificationRequest] = []

    async def propose_action(self, request: ModelRequest) -> Any:
        self.requests.append(request)
        if self.call_count < len(self.actions):
            step = self.actions[self.call_count]
            self.call_count += 1
        else:
            self.call_count += 1
            return {"action": "done", "reasoning": "Guion agotado."}
        if callable(step):
            step = step(request)
        if isinstance(step, ProposedAction):
            step = step.model_dump(mode="json", exclude_none=True)
        if isinstance(step, dict):
            step = dict(step)
            if step.get("observation_id") == OBS_PLACEHOLDER:
                step["observation_id"] = request.observation_meta.get("observation_id")
        return step

    async def verify_outcome(self, request: VerificationRequest) -> Any:
        self.verify_requests.append(request)
        if self.verify_count < len(self.verifications):
            v = self.verifications[self.verify_count]
            self.verify_count += 1
            return v(request) if callable(v) else v
        self.verify_count += 1
        return {"status": "incierto", "evidence": "Verificación guionizada no definida."}


StepFn = Callable[[ModelRequest], dict]
