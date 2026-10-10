from pydantic import BaseModel, Field
from datetime import datetime, timezone
from web_agent.security.redaction import DataRedactor
import json
import os

class AuditEvent(BaseModel):
    """Registro inmutable de un evento auditable (Fase 5)."""
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action_type: str
    details: str
    is_success: bool
    blocked_by_policy: bool = False

class AuditLogger:
    def __init__(self, log_dir: str = "./data/audit"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def log_event(self, event: AuditEvent):
        """Escribe el evento de auditoría garantizando la redacción previa."""
        safe_details = DataRedactor.redact(event.details)
        event_dict = event.model_dump()
        event_dict["details"] = safe_details
        
        log_path = os.path.join(self.log_dir, f"{event.run_id}_audit.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_dict, default=str) + "\n")
