"""Workers de tarea (documento maestro §5.3, §8.3).

- ``SubprocessWorker`` (producción): cada tarea corre en un proceso propio. El
  supervisor puede cancelarla de forma cooperativa (SIGINT → cancelación de
  asyncio → cierre de Playwright) y, si no responde en el margen de limpieza,
  terminar el grupo de procesos completo (worker + driver + Chromium) sin
  afectar a la API.
- ``InProcessWorker`` (pruebas/desarrollo): tarea asyncio con cancelación
  cooperativa y limpieza acotada.

Terminar un worker no revierte efectos ya enviados a la aplicación remota.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from web_agent.config import Settings
from web_agent.core.deadline import Deadline

ProgressFn = Callable[[dict], None]


@dataclass(frozen=True)
class TaskJob:
    task_id: str
    objective: str
    desktop_url: str
    deadline_started: float
    deadline_expires: float
    password: Optional[str] = field(default=None, repr=False)

    @property
    def deadline(self) -> Deadline:
        return Deadline(started_at=self.deadline_started, expires_at=self.deadline_expires)


# --------------------------------------------------------------------------- ejecución
def build_model(settings: Settings):
    from decimal import Decimal

    from web_agent.execution.budgets import BudgetManager

    if settings.model_provider == "scripted":
        from web_agent.models.adapters.scripted import ScriptedModelAdapter

        return ScriptedModelAdapter()
    from web_agent.models.adapters.openrouter import OpenRouterModelAdapter

    budget = BudgetManager(
        limit_usd=Decimal(settings.max_task_cost_usd),
        max_calls=settings.max_model_calls_per_task + 5,
        max_output_tokens=50_000_000,
    )
    return OpenRouterModelAdapter(
        api_key=settings.openrouter_api_key or "",
        model_name=settings.model_name,
        budget=budget,
        price_input_per_mtok=settings.price_input_per_mtok_usd,
        price_output_per_mtok=settings.price_output_per_mtok_usd,
    )


def default_runner_factory(job: TaskJob, settings: Settings):
    from web_agent.browser.session import SecureBrowserSession
    from web_agent.core.runner import AgentRunner
    from web_agent.core.schemas import build_desktop_profile

    profile = build_desktop_profile(
        task_id=job.task_id,
        desktop_url=job.desktop_url,
        allow_private_desktop=settings.allow_private_desktop,
        allow_loopback_desktop=settings.allow_loopback_desktop,
        max_steps=settings.max_actions_per_task,
        headless=settings.headless,
        chromium_sandbox=settings.chromium_sandbox,
        observation_max_age_seconds=settings.observation_max_age_seconds,
    )
    browser = SecureBrowserSession(
        headless=settings.headless,
        chromium_sandbox=settings.chromium_sandbox,
        viewport_width=settings.viewport_width,
        viewport_height=settings.viewport_height,
    )
    model = build_model(settings)
    runner = AgentRunner(profile, model, browser, settings=settings)
    return runner, model


async def run_task(job: TaskJob, settings: Settings, progress: Optional[ProgressFn] = None,
                   runner_factory=default_runner_factory) -> dict:
    from web_agent.core.context import SecretStore
    from web_agent.core.result import TaskState
    from web_agent.core.schemas import DESKTOP_PASSWORD_SECRET

    secrets = SecretStore({DESKTOP_PASSWORD_SECRET: job.password} if job.password else {})
    runner, model = runner_factory(job, settings)
    try:
        result = await runner.run(
            job.objective,
            task_id=job.task_id,
            deadline=job.deadline,
            desktop_url=job.desktop_url,
            secrets=secrets,
            progress=progress,
        )
        return result.model_dump(mode="json")
    except asyncio.CancelledError:
        return {"final_state": TaskState.CANCELLED.value, "summary": "Tarea cancelada.", "reason_code": "cancelled",
                "steps_executed": 0}
    finally:
        secrets.clear()
        try:
            await asyncio.wait_for(model.aclose(), timeout=2.0)
        except BaseException:
            pass


# --------------------------------------------------------------------------- workers
class InProcessWorker:
    def __init__(self, job: TaskJob, settings: Settings, on_progress: ProgressFn, runner_factory=None):
        self.job = job
        self.settings = settings
        self.on_progress = on_progress
        self.runner_factory = runner_factory or default_runner_factory
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(
            run_task(self.job, self.settings, self.on_progress, runner_factory=self.runner_factory)
        )

    async def wait_result(self) -> dict:
        return await asyncio.shield(self._task)

    async def terminate(self, grace: float) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=grace)
        except BaseException:
            pass


def _child_main(job_dict: dict, settings_dict: dict, conn) -> None:  # pragma: no cover - proceso hijo
    if hasattr(os, "setpgrp"):
        try:
            os.setpgrp()  # grupo propio: el supervisor puede terminar también a Chromium
        except OSError:
            pass
    settings = Settings.from_child_dict(settings_dict)
    job = TaskJob(**job_dict)

    def progress(fields: dict) -> None:
        try:
            conn.send({"type": "progress", **fields})
        except Exception:
            pass

    try:
        result = asyncio.run(run_task(job, settings, progress))
        conn.send({"type": "result", "result": result})
    except KeyboardInterrupt:
        try:
            conn.send({"type": "result", "result": {"final_state": "cancelled", "reason_code": "cancelled",
                                                     "summary": "Tarea cancelada.", "steps_executed": 0}})
        except Exception:
            pass
    except BaseException:
        try:
            conn.send({"type": "error"})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


class SubprocessWorker:
    def __init__(self, job: TaskJob, settings: Settings, on_progress: ProgressFn, runner_factory=None):
        if runner_factory is not None:
            raise ValueError("SubprocessWorker usa la fábrica por defecto en el proceso hijo.")
        self.job = job
        self.settings = settings
        self.on_progress = on_progress
        self._proc = None
        self._conn = None
        self._stop = False

    async def start(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        self._proc = ctx.Process(
            target=_child_main,
            args=(asdict(self.job), self.settings.to_child_dict(), child_conn),
            daemon=True,
            name=f"web-agent-{self.job.task_id}",
        )
        await asyncio.to_thread(self._proc.start)
        child_conn.close()
        self._conn = parent_conn

    def _recv(self) -> Optional[dict]:
        while not self._stop:
            try:
                if self._conn.poll(0.25):
                    return self._conn.recv()
            except (EOFError, OSError):
                return None
            if not self._proc.is_alive() and not self._conn.poll(0):
                return None
        return None

    async def wait_result(self) -> dict:
        while True:
            msg = await asyncio.to_thread(self._recv)
            if msg is None or msg.get("type") == "error":
                return {"final_state": "failed", "reason_code": "internal_error",
                        "summary": "El worker terminó sin resultado.", "steps_executed": 0}
            if msg.get("type") == "progress":
                self.on_progress({k: v for k, v in msg.items() if k != "type"})
                continue
            if msg.get("type") == "result":
                return msg["result"]

    async def terminate(self, grace: float) -> None:
        self._stop = True
        proc = self._proc
        if proc is None or not proc.is_alive():
            return
        try:
            if os.name == "posix":
                os.kill(proc.pid, signal.SIGINT)  # cancelación cooperativa → cierre de Playwright
            else:
                proc.terminate()
        except OSError:
            pass
        end = time.monotonic() + grace
        while proc.is_alive() and time.monotonic() < end:
            await asyncio.sleep(0.1)
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # worker + driver + navegador
            except OSError:
                pass
        if proc.is_alive():
            proc.kill()
        await asyncio.to_thread(proc.join, 2.0)
        if os.name == "posix":
            # Confirmar que el grupo (driver/Chromium) ha desaparecido.
            end = time.monotonic() + 3.0
            while time.monotonic() < end:
                try:
                    os.killpg(proc.pid, 0)
                except OSError:
                    break
                await asyncio.sleep(0.1)
