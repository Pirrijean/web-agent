"""Deadline monotónico compartido (documento maestro §8.3, §15.1).

El deadline se fija al admitir la tarea. Todos los componentes reciben el mismo
objeto y calculan su tiempo restante; ningún componente reinicia el plazo.
``time.monotonic`` usa un reloj de sistema compartido entre procesos del mismo
host (CLOCK_MONOTONIC en Linux), por lo que ``expires_at`` puede transmitirse
al worker en subproceso.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from web_agent.core.errors import DeadlineExceededError


@dataclass(frozen=True)
class Deadline:
    started_at: float
    expires_at: float

    @classmethod
    def start(cls, total_seconds: float, *, now: float | None = None) -> "Deadline":
        if total_seconds <= 0:
            raise ValueError("El plazo debe ser positivo.")
        t0 = time.monotonic() if now is None else now
        return cls(started_at=t0, expires_at=t0 + float(total_seconds))

    @property
    def total_seconds(self) -> float:
        return self.expires_at - self.started_at

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def check(self, margin: float = 0.0) -> None:
        """Lanza ``DeadlineExceededError`` si no queda más que ``margin`` segundos."""
        if self.remaining() <= margin:
            raise DeadlineExceededError("Deadline global agotado.")

    def timeout_for(self, local_timeout: float | None, margin: float = 0.0) -> float:
        """Timeout efectivo: ``min(local, restante - margen)``. Nunca amplía el plazo."""
        available = self.remaining() - margin
        if available <= 0:
            raise DeadlineExceededError("Deadline global agotado.")
        if local_timeout is None:
            return available
        return max(0.001, min(float(local_timeout), available))
