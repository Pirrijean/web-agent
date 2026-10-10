"""Contexto de tarea y estado de ejecución (documento maestro §8.4).

``TaskContext`` es inmutable: objetivo autorizado, identificadores, deadline,
modo de percepción y nombres de secretos disponibles. ``RunState`` es el estado
mutable del bucle: observación vigente, historial acotado y contadores.
Los valores secretos viven solo en ``SecretStore`` y se resuelven en el
ejecutor (§11.3).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from web_agent.core.deadline import Deadline
from web_agent.core.observation import DOMObservation, VisualObservation
from web_agent.security.redaction import DataRedactor


class SecretStore:
    """Resolvedor de secretos de la tarea. Nunca se serializa ni se muestra."""

    def __init__(self, secrets: Optional[dict[str, str]] = None):
        self._secrets: dict[str, str] = {}
        for name, value in (secrets or {}).items():
            self.add(name, value)

    def add(self, name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            return
        self._secrets[name] = value
        DataRedactor.register_secret(value)

    def names(self) -> frozenset[str]:
        return frozenset(self._secrets)

    def resolve(self, name: str) -> str:
        try:
            return self._secrets[name]
        except KeyError:
            raise KeyError("Referencia de secreto no disponible.") from None

    def clear(self) -> None:
        for value in self._secrets.values():
            DataRedactor.unregister_secret(value)
        self._secrets.clear()

    def __repr__(self) -> str:  # pragma: no cover - nunca mostrar valores
        return f"SecretStore(names={sorted(self._secrets)})"

    __str__ = __repr__

    def __reduce__(self):  # impedir serialización accidental
        raise TypeError("SecretStore no es serializable.")


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    run_id: str
    objective: str
    perception_mode: str
    deadline: Deadline
    max_steps: int
    desktop_url: Optional[str] = None
    secret_names: frozenset[str] = frozenset()


@dataclass
class StepRecord:
    index: int
    action: str
    summary: str
    outcome: str
    risk: str = "R0"
    screen_changed: Optional[bool] = None
    model_assessment: Optional[str] = None
    coords: Optional[tuple[int, int]] = None

    def to_prompt_line(self) -> str:
        changed = (
            "" if self.screen_changed is None
            else (" | pantalla cambió" if self.screen_changed else " | pantalla SIN cambios")
        )
        return f"#{self.index} {self.action}: {self.summary} -> {self.outcome}{changed}"


@dataclass
class RunState:
    current_visual: Optional[VisualObservation] = None
    current_image: Optional[bytes] = field(default=None, repr=False)
    current_dom: Optional[DOMObservation] = None
    observation_stale: bool = True
    geometry_revision: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=12))
    navigation_memory: deque = field(default_factory=lambda: deque(maxlen=10))
    steps_executed: int = 0
    model_calls: int = 0
    invalid_proposals: int = 0
    recovery_attempts: int = 0
    done_attempts: int = 0
    feedback: Optional[str] = None
    pending_uncertain_effect: bool = False
    persistent_actions_executed: int = 0
    extracted: list = field(default_factory=list)
    last_screen_hash: Optional[str] = None

    @property
    def current_observation_id(self) -> Optional[str]:
        if self.current_visual is not None:
            return self.current_visual.observation_id
        if self.current_dom is not None:
            return self.current_dom.observation_id
        return None

    def mark_stale(self) -> None:
        self.observation_stale = True

    def add_feedback(self, text: str) -> None:
        self.feedback = text if not self.feedback else f"{self.feedback}\n{text}"
        if len(self.feedback) > 1500:
            self.feedback = self.feedback[-1500:]

    def pop_feedback(self) -> Optional[str]:
        fb, self.feedback = self.feedback, None
        return fb
