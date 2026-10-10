"""Registro de tareas (documento maestro §8.2).

Interfaz ``TaskStore`` independiente del almacenamiento. La implementación
inicial es en memoria: válida para una sola instancia; el estado se pierde al
reiniciar. Si el contrato exige durabilidad, se añade otro backend sin tocar
API ni supervisor.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from web_agent.core.deadline import Deadline
from web_agent.core.result import PUBLIC_STATUS, TERMINAL_STATES, TaskState


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    state: TaskState
    deadline: Deadline
    created_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: Optional[str] = None
    steps_executed: int = 0
    summary: str = "Tarea aceptada."
    reason_code: Optional[str] = None
    verification_status: Optional[str] = None
    evidence_kind: Optional[str] = None
    last_action: Optional[str] = None
    possible_side_effects: bool = False
    finished_elapsed_seconds: Optional[float] = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def public_status(self) -> str:
        return PUBLIC_STATUS[self.state]

    def elapsed_seconds(self) -> float:
        if self.finished_elapsed_seconds is not None:
            return self.finished_elapsed_seconds
        return round(self.deadline.elapsed(), 2)


class TaskStore(ABC):
    @abstractmethod
    def create(self, record: TaskRecord) -> None: ...

    @abstractmethod
    def get(self, task_id: str) -> Optional[TaskRecord]: ...

    @abstractmethod
    def update(self, task_id: str, **fields) -> Optional[TaskRecord]: ...

    @abstractmethod
    def count_active(self) -> int: ...


class InMemoryTaskStore(TaskStore):
    def __init__(self, max_records: int = 5_000):
        self._records: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._max = max_records

    def create(self, record: TaskRecord) -> None:
        with self._lock:
            if record.task_id in self._records:
                raise KeyError("task_id duplicado")
            if len(self._records) >= self._max:
                # Purga las tareas terminadas más antiguas.
                finished = sorted((r for r in self._records.values() if r.terminal), key=lambda r: r.created_at_utc)
                for old in finished[: max(1, len(finished) // 4)]:
                    self._records.pop(old.task_id, None)
            self._records[record.task_id] = record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._records.get(task_id)

    def update(self, task_id: str, **fields) -> Optional[TaskRecord]:
        with self._lock:
            current = self._records.get(task_id)
            if current is None:
                return None
            # Un estado terminal publicado no se reescribe (p. ej. timeout del supervisor).
            if current.terminal and "state" in fields:
                return current
            if current.terminal:
                return current
            if "state" in fields and fields["state"] in TERMINAL_STATES and "finished_elapsed_seconds" not in fields:
                fields["finished_elapsed_seconds"] = round(current.deadline.elapsed(), 2)
            updated = replace(current, **fields)
            self._records[task_id] = updated
            return updated

    def count_active(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if not r.terminal)
