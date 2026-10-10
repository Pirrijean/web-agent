"""Supervisor de tareas (documento maestro §8.3, §15; ADR-009).

- Concurrencia acotada (``MAX_CONCURRENT_TASKS``); el tiempo en cola consume
  el mismo deadline monotónico fijado en la admisión.
- Al vencer el deadline publica el estado final de inmediato y después
  termina el worker; la limpieza no cambia retroactivamente el estado.
- La cancelación no revierte efectos externos: si hubo acciones, el resumen
  lo indica.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from web_agent.config import Settings
from web_agent.core.result import TaskState
from web_agent.service.task_store import TaskStore
from web_agent.service.worker import InProcessWorker, SubprocessWorker, TaskJob

logger = logging.getLogger(__name__)

TIMEOUT_SUMMARY = "Tiempo global de 5 minutos agotado."


class Supervisor:
    def __init__(self, store: TaskStore, settings: Settings, worker_factory: Optional[Callable] = None):
        self.store = store
        self.settings = settings
        if worker_factory is None:
            worker_factory = SubprocessWorker if settings.worker_mode == "subprocess" else InProcessWorker
        self.worker_factory = worker_factory
        self._sem: Optional[asyncio.Semaphore] = None
        self._tasks: set[asyncio.Task] = set()
        self._running = 0

    @property
    def running(self) -> int:
        return self._running

    def submit(self, job: TaskJob) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.settings.max_concurrent_tasks)
        task = asyncio.create_task(self._supervise(job), name=f"supervise-{job.task_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_progress(self, task_id: str):
        def handler(fields: dict) -> None:
            updates = {}
            if isinstance(fields.get("steps_executed"), int):
                updates["steps_executed"] = fields["steps_executed"]
            if isinstance(fields.get("last_action"), str):
                updates["last_action"] = fields["last_action"][:200]
            if updates:
                self.store.update(task_id, **updates)
        return handler

    async def _supervise(self, job: TaskJob) -> None:
        deadline = job.deadline
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=max(0.001, deadline.remaining()))
        except (TimeoutError, asyncio.TimeoutError):
            self.store.update(job.task_id, state=TaskState.TIMED_OUT, reason_code="timeout",
                              summary=TIMEOUT_SUMMARY + " La tarea no llegó a ejecutarse (cola).")
            return
        self._running += 1
        worker = None
        try:
            self.store.update(job.task_id, state=TaskState.RUNNING, summary="Tarea en ejecución.")
            worker = self.worker_factory(job, self.settings, self._on_progress(job.task_id))
            await worker.start()
            try:
                result = await asyncio.wait_for(worker.wait_result(), timeout=max(0.001, deadline.remaining()))
            except (TimeoutError, asyncio.TimeoutError):
                record = self.store.get(job.task_id)
                acted = bool(record and record.steps_executed > 0)
                summary = TIMEOUT_SUMMARY
                if acted:
                    summary += " Se ejecutaron acciones; no se pudo confirmar si alguna operación persistió."
                self.store.update(job.task_id, state=TaskState.TIMED_OUT, reason_code="timeout",
                                  summary=summary, possible_side_effects=acted)
                return
            self._apply_result(job.task_id, result)
        except asyncio.CancelledError:
            self.store.update(job.task_id, state=TaskState.CANCELLED, reason_code="cancelled",
                              summary="Servicio detenido antes de completar la tarea.")
            raise
        except Exception:
            logger.exception("Fallo del supervisor en %s", job.task_id)
            self.store.update(job.task_id, state=TaskState.FAILED, reason_code="internal_error",
                              summary="Fallo interno del supervisor.")
        finally:
            self._running -= 1
            self._sem.release()
            if worker is not None:
                try:
                    await worker.terminate(grace=self.settings.cleanup_timeout_seconds)
                except Exception:
                    logger.exception("No se pudo terminar el worker de %s", job.task_id)

    def _apply_result(self, task_id: str, result: dict) -> None:
        try:
            state = TaskState(result.get("final_state", "failed"))
        except ValueError:
            state = TaskState.FAILED
        if state not in (
            TaskState.SUCCEEDED_VERIFIED, TaskState.FAILED, TaskState.BLOCKED, TaskState.NEEDS_HUMAN_REVIEW,
            TaskState.TIMED_OUT, TaskState.CANCELLED, TaskState.UNCERTAIN,
        ):
            state = TaskState.FAILED
        verification = result.get("verification") or {}
        updates = {
            "state": state,
            "summary": str(result.get("summary") or result.get("reason") or "")[:2000] or "Sin resumen.",
            "reason_code": result.get("reason_code"),
            "run_id": result.get("run_id"),
            "verification_status": verification.get("status"),
            "evidence_kind": verification.get("evidence_kind"),
            "possible_side_effects": bool(result.get("possible_side_effects", False)),
        }
        if isinstance(result.get("steps_executed"), int):
            updates["steps_executed"] = result["steps_executed"]
        if isinstance(result.get("elapsed_seconds"), (int, float)) and result["elapsed_seconds"] > 0:
            updates["finished_elapsed_seconds"] = float(result["elapsed_seconds"])
        self.store.update(task_id, **updates)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
