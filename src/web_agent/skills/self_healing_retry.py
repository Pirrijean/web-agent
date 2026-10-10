"""SKILL-06 SelfHealingRetry (documento maestro §8.12, §13.3).

Protocolo: reobservar → clasificar → comprobar posibles efectos → elegir una
alternativa autorizada → validar → ejecutar → verificar.

Prohibido: cadena genérica clic → doble clic → Escape; repetir operaciones
persistentes inciertas; reintentar violaciones de política.
"""

from __future__ import annotations

from web_agent.core.context import RunState, TaskContext
from web_agent.core.schemas import RiskLevel
from web_agent.recovery.strategies import RecoveryAction, RecoveryDecision, RecoveryStrategy
from web_agent.skills.base import Skill, SkillSpec


class SelfHealingRetry(Skill):
    spec = SkillSpec(
        skill_id="self_healing_retry",
        version="1.0",
        purpose="Diagnosticar y recuperar fallos sin bucles ciegos.",
        preconditions=("Fallo recuperable clasificado",),
        postcondition="Nueva propuesta basada en una observación fresca.",
        risks=("Duplicar operaciones persistentes",),
        repetition_rule="Máximo de intentos de recuperación acotado por configuración.",
        evidence="Eventos recovery_started / recovery_result en auditoría.",
    )

    def __init__(self, max_attempts: int = 6):
        self.max_attempts = max_attempts

    def on_failure(
        self, state: RunState, error: BaseException, *, risk: RiskLevel, emitted: bool
    ) -> RecoveryDecision:
        decision = RecoveryStrategy.decide(error, risk=risk, emitted=emitted)
        if decision.action == RecoveryAction.ABORT:
            return decision
        state.recovery_attempts += 1
        if decision.possible_side_effect:
            state.pending_uncertain_effect = True
        if state.recovery_attempts > self.max_attempts:
            return RecoveryDecision(decision.failure, RecoveryAction.ABORT, decision.possible_side_effect,
                                    "Intentos de recuperación agotados.")
        state.mark_stale()
        state.add_feedback(decision.guidance)
        return decision

    def on_rejection(self, state: RunState, reason_code: str, message: str) -> bool:
        """Propuesta rechazada por validación contextual. Devuelve False si se agotan los intentos."""
        state.recovery_attempts += 1
        state.mark_stale()
        state.add_feedback(f"Propuesta rechazada ({reason_code}): {message} Vuelve a analizar la captura nueva.")
        return state.recovery_attempts <= self.max_attempts

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        lines = [
            "RECUPERACIÓN: si una acción no produjo efecto, no repitas exactamente la misma; analiza la "
            "captura nueva (¿diálogo de error?, ¿control deshabilitado?, ¿foco en otro sitio?) y elige "
            "una alternativa. Usa doble clic solo si el control lo requiere (p. ej. abrir un registro de "
            "una grilla) y justifícalo.",
        ]
        if state.pending_uncertain_effect:
            lines.append(
                "ATENCIÓN: una operación persistente anterior tiene resultado incierto. Comprueba primero "
                "si ya se aplicó antes de repetirla."
            )
        return lines
