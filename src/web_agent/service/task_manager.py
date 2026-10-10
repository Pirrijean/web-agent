"""Alta y consulta de tareas (documento maestro §8.2, §12).

La API llama aquí, nunca al ejecutor. La admisión es rápida: valida el destino,
fija el deadline monotónico, audita ``task_accepted`` y encola. No espera al
navegador ni al modelo.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional

from web_agent.audit.events import AuditEvent, AuditLogger
from web_agent.config import Settings
from web_agent.core.deadline import Deadline
from web_agent.core.errors import AuditUnavailableError
from web_agent.core.result import TaskState
from web_agent.security.origins import OriginError, parse_desktop_origin, viewer_url_for_desktop
from web_agent.security.redaction import DataRedactor
from web_agent.service.supervisor import Supervisor
from web_agent.service.task_store import InMemoryTaskStore, TaskRecord, TaskStore
from web_agent.service.worker import TaskJob


class InvalidTaskError(ValueError):
    """La tarea no puede admitirse (destino no autorizado o datos inválidos)."""


class TaskManager:
    def __init__(
        self,
        settings: Settings,
        store: Optional[TaskStore] = None,
        worker_factory: Optional[Callable] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.settings = settings
        self.store = store or InMemoryTaskStore()
        self.supervisor = Supervisor(self.store, settings, worker_factory)
        self.audit_logger = audit_logger or AuditLogger(settings.audit_dir)

    def create_task(self, objective: str, desktop_url: str, password: Optional[str]) -> TaskRecord:
        try:
            # wss://host/websockify -> visor noVNC del mismo origen (la política se aplica al visor).
            desktop_url = viewer_url_for_desktop(desktop_url)
            parse_desktop_origin(
                desktop_url,
                allow_private=self.settings.allow_private_desktop,
                allow_loopback=self.settings.allow_loopback_desktop,
            )
        except OriginError as exc:
            raise InvalidTaskError(f"URL de escritorio no autorizada: {exc}") from None

        task_id = f"task_{uuid.uuid4().hex[:20]}"
        deadline = Deadline.start(self.settings.task_timeout_seconds)
        if password:
            DataRedactor.register_secret(password)
        try:
            self.audit_logger.log_event(
                AuditEvent(
                    task_id=task_id,
                    run_id=task_id,
                    event_type="task_accepted",
                    outcome="ok",
                    details=DataRedactor.redact(objective)[:500],
                    data={"deadline_seconds": self.settings.task_timeout_seconds},
                )
            )
        except Exception as exc:
            raise AuditUnavailableError("Auditoría no disponible.") from exc
        finally:
            if password:
                DataRedactor.unregister_secret(password)

        record = TaskRecord(task_id=task_id, state=TaskState.QUEUED, deadline=deadline, summary="Tarea en cola.")
        self.store.create(record)
        self.supervisor.submit(
            TaskJob(
                task_id=task_id,
                objective=objective,
                desktop_url=desktop_url,
                password=password,
                deadline_started=deadline.started_at,
                deadline_expires=deadline.expires_at,
            )
        )
        return record

    def get(self, task_id: str) -> Optional[TaskRecord]:
        return self.store.get(task_id)

    def health(self) -> dict:
        ready = bool(self.settings.bearer_token) and self.settings.model_configured
        return {
            "status": "healthy" if ready else "not_ready",
            "service": "web-agent",
            "ready": ready,
            "model_configured": self.settings.model_configured,
            "running_tasks": self.supervisor.running,
            "active_tasks": self.store.count_active(),
            "max_concurrent_tasks": self.settings.max_concurrent_tasks,
        }

    async def shutdown(self) -> None:
        await self.supervisor.shutdown()
