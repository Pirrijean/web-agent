"""Auditoría JSONL redactada y obligatoria (documento maestro §8.14, §14).

Garantía real: registro secuencial append-only con redacción y política
fail-stop (si falla la escritura, el runner no inicia nuevas acciones).
No es inmutable ni criptográficamente verificable.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from web_agent.security.redaction import DataRedactor

SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

EVENT_TYPES = frozenset(
    {
        "task_accepted",
        "task_started",
        "session_initialized",
        "observation_created",
        "model_request_started",
        "model_response_received",
        "proposal_validated",
        "proposal_rejected",
        "policy_decision",
        "action_started",
        "action_completed",
        "action_failed",
        "verification_result",
        "recovery_started",
        "recovery_result",
        "budget_warning",
        "budget_exhausted",
        "deadline_warning",
        "task_timed_out",
        "skill_started",
        "skill_result",
        "task_finished",
    }
)


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: Optional[str] = Field(None, max_length=64)
    run_id: str = Field(..., pattern=SAFE_ID_PATTERN)
    seq: int = Field(0, ge=0)
    event_type: str = Field(..., max_length=64)
    outcome: str = Field("info", max_length=32)
    reason_code: Optional[str] = Field(None, max_length=64)
    observation_id: Optional[str] = Field(None, max_length=64)
    details: str = Field("", max_length=4_000)
    data: dict[str, Any] = Field(default_factory=dict)
    is_success: bool = False
    blocked_by_policy: bool = False


class AuditLogger:
    def __init__(self, log_dir: str = "./data/audit"):
        self.log_dir = Path(log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log_event(self, event: AuditEvent) -> None:
        """Escribe el evento redactado. Cualquier excepción se trata como fallo de auditoría."""
        if not re.fullmatch(SAFE_ID_PATTERN, event.run_id):
            raise ValueError("Identificador de ejecución inseguro.")
        safe_dict = DataRedactor.redact_obj(event.model_dump(mode="json"))
        log_path = (self.log_dir / f"{event.run_id}_audit.jsonl").resolve()
        try:
            log_path.relative_to(self.log_dir)
        except ValueError as exc:
            raise ValueError("Ruta de auditoría fuera del directorio permitido.") from exc
        line = json.dumps(safe_dict, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
