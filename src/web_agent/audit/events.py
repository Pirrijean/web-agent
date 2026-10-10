import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from web_agent.security.redaction import DataRedactor

SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class AuditEvent(BaseModel):
    """Registro inmutable de un evento auditable (Fase 5)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = Field(..., pattern=SAFE_ID_PATTERN)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action_type: str = Field(..., max_length=64)
    details: str = Field(..., max_length=10_000)
    is_success: bool
    blocked_by_policy: bool = False


class AuditLogger:
    def __init__(self, log_dir: str = "./data/audit"):
        self.log_dir = Path(log_dir).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_event(self, event: AuditEvent):
        """Escribe el evento de auditoría garantizando la redacción previa y evitando path traversal."""
        if not re.fullmatch(SAFE_ID_PATTERN, event.run_id):
            raise ValueError(f"Identificador de ejecución inseguro: {event.run_id}")

        event_dict = event.model_dump()
        # Redactar todo el objeto recursivamente
        safe_dict = DataRedactor.redact_obj(event_dict)

        filename = f"{event.run_id}_audit.jsonl"
        log_path = (self.log_dir / filename).resolve()

        # Evitar path traversal
        try:
            log_path.relative_to(self.log_dir)
        except ValueError as exc:
            raise ValueError("Ruta de auditoría fuera del directorio permitido.") from exc

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe_dict, default=str) + "\n")

