"""Interfaz neutral de proveedor de inferencia (documento maestro §8.7; ADR-006).

El adaptador recibe un objetivo, una observación y contexto acotado, y devuelve
una propuesta estructurada NO CONFIABLE. No tiene acceso al navegador, la red
de la sesión, los archivos ni los secretos. Nada de lo que devuelve se ejecuta
sin pasar por validación, política y ejecutor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional


@dataclass(frozen=True)
class ModelRequest:
    objective: str
    perception_mode: str
    step_index: int
    max_steps: int
    remaining_seconds: float
    observation_meta: dict
    image_bytes: Optional[bytes] = field(default=None, repr=False)
    image_mime: Optional[str] = None
    dom_text: Optional[str] = None
    history: tuple[str, ...] = ()
    navigation_memory: tuple[str, ...] = ()
    feedback: Optional[str] = None
    secret_names: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()
    max_output_tokens: int = 1024
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class VerificationRequest:
    objective: str
    observation_meta: dict
    image_bytes: Optional[bytes] = field(default=None, repr=False)
    image_mime: Optional[str] = None
    dom_text: Optional[str] = None
    history: tuple[str, ...] = ()
    claimed_evidence: Optional[str] = None
    max_output_tokens: int = 512
    timeout_seconds: float = 60.0


@dataclass
class ModelUsage:
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    provider_reported_cost_usd: Optional[Decimal] = None
    latency_seconds: float = 0.0


class BaseModelAdapter(ABC):
    """Interfaz aislada. No puede acceder al navegador ni ejecutar acciones."""

    supports_vision: bool = False
    model_id: str = "unknown"

    def __init__(self) -> None:
        self.last_usage: Optional[ModelUsage] = None

    @abstractmethod
    async def propose_action(self, request: ModelRequest) -> Any:
        """Devuelve un ``dict`` o ``ProposedAction``; el núcleo lo revalida siempre."""

    async def verify_outcome(self, request: VerificationRequest) -> Any:
        """Evaluación visual independiente de la postcondición final.

        Devuelve ``{"status": "verificado"|"no_verificado"|"incierto", "evidence": str}``.
        Por defecto no hay evidencia: ``incierto``.
        """
        return {"status": "incierto", "evidence": "El adaptador no implementa verificación."}

    async def aclose(self) -> None:
        return None
