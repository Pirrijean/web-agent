"""AgentRunner: máquina de estados de una tarea (documento maestro §6, §8.4).

Ciclo: observar → proponer → validar (estructura) → validar (contexto) →
autorizar → auditar intención → ejecutar → reobservar → verificar.

Invariantes impuestas aquí:
- no se ejecuta una propuesta inválida ni basada en una observación caducada;
- no se ejecuta nada fuera del ámbito autorizado (política local);
- no se inicia una acción tras agotar el deadline ni una llamada sin presupuesto;
- no hay éxito sin verificación de la postcondición (``DONE`` solo la solicita);
- no se continúa si falla la auditoría obligatoria;
- no se repite a ciegas una operación que pudo persistir.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from web_agent.audit.events import AuditEvent, AuditLogger
from web_agent.browser.executor import Executor
from web_agent.browser.observe import DOMObserver
from web_agent.browser.observe_visual import VisualObserver
from web_agent.config import Settings
from web_agent.core.action_validator import ContextualValidator
from web_agent.core.context import RunState, SecretStore, StepRecord, TaskContext
from web_agent.core.deadline import Deadline
from web_agent.core.errors import (
    ActionRejectedError,
    AuditUnavailableError,
    BudgetExhaustedError,
    DeadlineExceededError,
    HumanApprovalRequiredError,
    ModelOutputError,
    ModelProviderError,
    ObservationError,
    PolicyViolationError,
)
from web_agent.core.policy import DecisionOutcome, SecurityPolicyEnforcer
from web_agent.core.result import RunResult, TaskState, VerificationResult, VerificationStatus
from web_agent.core.schemas import (
    MAX_OBJECTIVE_CHARS,
    RISK_ORDER,
    ActionType,
    ProposedAction,
    RiskLevel,
    TaskProfile,
)
from web_agent.core.verifier import Verifier
from web_agent.models.base import ModelRequest
from web_agent.recovery.strategies import RecoveryAction
from web_agent.security.redaction import DataRedactor
from web_agent.skills import DesktopAuthAndBootstrap, SelfHealingRetry, ZeroShotDiscovery, default_skills

ProgressCallback = Callable[[dict], None]


class _RecoveryExhausted(Exception):
    pass


class _ModelUnavailable(Exception):
    pass


class _InvalidOutput(Exception):
    pass


class AgentRunner:
    def __init__(
        self,
        profile: TaskProfile,
        model,
        browser,
        audit_logger=None,
        *,
        settings: Optional[Settings] = None,
        skills=None,
    ):
        # Reconstruir: no confiar en una instancia creada con model_construct().
        self.profile = TaskProfile.model_validate(profile.model_dump(mode="json"))
        self.settings = settings or Settings()
        self.model = model
        self.browser = browser
        self.audit_logger = audit_logger if audit_logger is not None else AuditLogger(self.settings.audit_dir)
        self.policy_enforcer = SecurityPolicyEnforcer(self.profile)
        self.network_validator = self.policy_enforcer.network_validator
        self.validator = ContextualValidator(self.profile)
        self.verifier = Verifier(self.profile, self.network_validator)
        self.skills = list(skills) if skills is not None else default_skills(self.settings.max_recovery_attempts)
        self._healing = next((s for s in self.skills if isinstance(s, SelfHealingRetry)), None) or SelfHealingRetry(
            self.settings.max_recovery_attempts
        )
        self._bootstrap = next((s for s in self.skills if isinstance(s, DesktopAuthAndBootstrap)), None)

        self.browser.network_validator = self.network_validator
        self.browser.headless = self.profile.browser.headless
        self.browser.chromium_sandbox = self.profile.browser.chromium_sandbox

        self._running = False
        self._seq = 0
        self._ctx: Optional[TaskContext] = None
        self._state: Optional[RunState] = None
        self._progress: Optional[ProgressCallback] = None
        self._model_errors = 0
        self._last_typed_secret = False
        self.executor: Optional[Executor] = None

    # ================================================================ utilidades
    async def _audit(
        self,
        event_type: str,
        *,
        outcome: str = "info",
        reason_code: Optional[str] = None,
        observation_id: Optional[str] = None,
        details: str = "",
        data: Optional[dict] = None,
        success: bool = False,
        blocked: bool = False,
    ) -> None:
        ctx = self._ctx
        self._seq += 1
        try:
            event = AuditEvent(
                task_id=ctx.task_id if ctx else None,
                run_id=ctx.run_id if ctx else "run_unknown",
                seq=self._seq,
                event_type=event_type,
                outcome=outcome,
                reason_code=reason_code,
                observation_id=observation_id,
                details=DataRedactor.redact(details)[:4000],
                data=DataRedactor.redact_obj(data or {}),
                is_success=success,
                blocked_by_policy=blocked,
            )
            await asyncio.wait_for(asyncio.to_thread(self.audit_logger.log_event, event), timeout=5.0)
        except Exception as exc:
            # No propagar texto externo que pueda contener secretos.
            raise AuditUnavailableError("Auditoría obligatoria no disponible.") from exc

    def _emit_progress(self, **fields: Any) -> None:
        if self._progress is None:
            return
        try:
            self._progress(fields)
        except Exception:
            pass

    @staticmethod
    def _validate_model_action(value) -> ProposedAction:
        try:
            if isinstance(value, ProposedAction):
                value = value.model_dump(mode="json", exclude_none=True)
            if not isinstance(value, dict):
                raise ValueError("Tipo de propuesta no permitido.")
            # Los modelos a veces devuelven null en campos no usados.
            value = {k: v for k, v in value.items() if v is not None}
            return ProposedAction.model_validate(value)
        except (ValidationError, ValueError, TypeError) as exc:
            detail = ""
            if isinstance(exc, ValidationError):
                detail = "; ".join(str(e.get("msg", ""))[:120] for e in exc.errors()[:3])
            raise ModelOutputError(f"La propuesta no cumple el contrato. {detail}".strip()) from None

    def _result(
        self,
        state_enum: TaskState,
        reason_code: str,
        reason: str,
        *,
        verification: Optional[VerificationResult] = None,
        summary: Optional[str] = None,
        possible_side_effects: Optional[bool] = None,
    ) -> RunResult:
        ctx, state = self._ctx, self._state
        steps = state.steps_executed if state else 0
        side = (
            possible_side_effects
            if possible_side_effects is not None
            else bool(state and (state.pending_uncertain_effect or (
                state.persistent_actions_executed > 0 and state_enum != TaskState.SUCCEEDED_VERIFIED)))
        )
        if summary is None:
            summary = reason
            if verification is not None and verification.evidence:
                summary = f"{reason} Evidencia: {verification.evidence}"
            if side and state_enum != TaskState.SUCCEEDED_VERIFIED:
                summary += " No se pudo confirmar si alguna operación persistente llegó a aplicarse."
        return RunResult(
            run_id=ctx.run_id if ctx else "run_unknown",
            task_id=ctx.task_id if ctx else None,
            final_state=state_enum,
            steps_executed=steps,
            reason=reason[:2000],
            summary=DataRedactor.redact(summary)[:2000],
            verification=verification,
            extracted_data=tuple(state.extracted) if state else (),
            blocked_requests=getattr(self.browser, "blocked_requests", 0) or 0,
            model_calls=state.model_calls if state else 0,
            elapsed_seconds=round(ctx.deadline.elapsed(), 2) if ctx else 0.0,
            possible_side_effects=side,
            reason_code=reason_code,  # type: ignore[arg-type]
        )

    # ================================================================ ciclo de vida
    async def run(
        self,
        objective: str,
        *,
        task_id: Optional[str] = None,
        deadline: Optional[Deadline] = None,
        desktop_url: Optional[str] = None,
        secrets: Optional[SecretStore] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> RunResult:
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or len(objective) > MAX_OBJECTIVE_CHARS
            or "\x00" in objective
        ):
            raise ValueError("Objetivo inválido.")
        if self._running:
            raise RuntimeError("Una sesión no puede ejecutar tareas concurrentes.")
        self._running = True

        run_id = f"run_{uuid.uuid4().hex}"
        total = min(float(self.profile.execution.task_timeout_seconds), self.settings.task_timeout_seconds)
        deadline = deadline or Deadline.start(total)
        secrets = secrets or SecretStore()
        if desktop_url is not None and not self.network_validator.is_url_allowed(desktop_url):
            self._running = False
            raise PolicyViolationError("El escritorio no pertenece al ámbito autorizado.")
        self._ctx = TaskContext(
            task_id=task_id or run_id,
            run_id=run_id,
            objective=objective.strip(),
            perception_mode=self.profile.perception.mode,
            deadline=deadline,
            max_steps=min(self.profile.execution.max_steps, self.settings.max_actions_per_task),
            desktop_url=desktop_url,
            secret_names=secrets.names() & self.profile.security.allowed_secret_names,
        )
        self._state = RunState()
        self._seq = 0
        self._progress = progress
        self._model_errors = 0
        self.executor = Executor(
            self.browser,
            self.profile,
            self.network_validator,
            secrets,
            typing_delay_ms=self.settings.typing_delay_ms,
            action_timeout_seconds=min(self.settings.action_timeout_seconds,
                                       self.profile.execution.action_timeout_ms / 1000.0),
        )
        result: Optional[RunResult] = None
        cancelled = False
        session_started = False
        try:
            async with asyncio.timeout(deadline.remaining()):
                await self._audit(
                    "task_started",
                    details="Inicio autorizado.",
                    data={"mode": self._ctx.perception_mode, "max_steps": self._ctx.max_steps,
                          "deadline_seconds": round(deadline.remaining(), 1),
                          "model": getattr(self.model, "model_id", "unknown")},
                )
                session_started = True
                await self.browser.start()
                await self._audit("session_initialized", data={"session_id": getattr(self.browser, "session_id", "")})
                result = await self._run_loop()
        except AuditUnavailableError:
            result = self._result(TaskState.BLOCKED, "audit_unavailable", "Ejecución detenida: auditoría no disponible.")
        except HumanApprovalRequiredError as exc:
            result = self._result(
                TaskState.NEEDS_HUMAN_REVIEW,
                "human_required",
                f"Se detuvo antes de actuar: {exc} No existe canal de aprobación humana.",
            )
        except PolicyViolationError as exc:
            result = self._result(TaskState.BLOCKED, "policy_blocked",
                                  f"Operación bloqueada por la política de seguridad: {exc}")
        except BudgetExhaustedError:
            result = self._result(TaskState.FAILED, "budget_exhausted", "Presupuesto de inferencia agotado.")
        except _InvalidOutput:
            result = self._result(TaskState.FAILED, "invalid_model_output",
                                  "El modelo devolvió repetidamente propuestas fuera del contrato.")
        except _ModelUnavailable:
            result = self._result(TaskState.FAILED, "model_unavailable", "El proveedor de inferencia no está disponible.")
        except _RecoveryExhausted:
            st = TaskState.UNCERTAIN if self._state.pending_uncertain_effect else TaskState.FAILED
            result = self._result(st, "recovery_exhausted", "No se encontró una alternativa segura tras varios intentos.")
        except (TimeoutError, DeadlineExceededError):
            st = (
                TaskState.UNCERTAIN
                if (self._state.pending_uncertain_effect or self._state.persistent_actions_executed)
                else TaskState.TIMED_OUT
            )
            result = self._result(st, "timeout", "Tiempo global agotado. No se repiten operaciones.")
        except asyncio.CancelledError:
            cancelled = True
            result = self._result(TaskState.CANCELLED, "cancelled", "Tarea cancelada por el supervisor.")
        except Exception:
            result = self._result(TaskState.FAILED, "internal_error", "Ejecución abortada por un fallo interno.")
        finally:
            cleanup_ok = True
            if session_started:
                try:
                    await asyncio.wait_for(self.browser.stop(), timeout=self.settings.cleanup_timeout_seconds)
                except BaseException:
                    cleanup_ok = False
            secrets.clear()
            self._running = False

        if not cleanup_ok and result is not None and result.final_state == TaskState.SUCCEEDED_VERIFIED:
            # El objetivo se verificó; el cierre fallido se informa sin alterar el resultado de negocio.
            result = result.model_copy(update={"summary": (result.summary + " (Aviso: cierre de recursos no confirmado.)")[:2000]})
        elif not cleanup_ok and result is not None and result.final_state not in (TaskState.CANCELLED,):
            result = self._result(TaskState.FAILED, "cleanup_failed",
                                  "No se pudo confirmar el cierre de todos los recursos.")

        try:
            await self._audit(
                "task_finished",
                outcome=result.final_state.value,
                reason_code=result.reason_code,
                details=result.reason,
                data={"steps": result.steps_executed, "model_calls": result.model_calls,
                      "elapsed": result.elapsed_seconds},
                success=result.final_state == TaskState.SUCCEEDED_VERIFIED,
                blocked=result.final_state == TaskState.BLOCKED,
            )
        except AuditUnavailableError:
            result = self._result(TaskState.BLOCKED, "audit_unavailable", "No se pudo persistir el estado final.")
        if cancelled:
            raise asyncio.CancelledError()
        return result

    # ================================================================ bucle
    async def _run_loop(self) -> RunResult:
        ctx, state = self._ctx, self._state
        if ctx.desktop_url and self._bootstrap is not None:
            await self._audit("skill_started", details=self._bootstrap.spec.skill_id)
            ok = await self._bootstrap.bootstrap(self, ctx)
            await self._audit("skill_result", outcome="ok" if ok else "failed", details=self._bootstrap.spec.skill_id)
            if not ok:
                return self._result(TaskState.FAILED, "internal_error", "No se pudo conectar con el escritorio autorizado.")

        for step in range(ctx.max_steps):
            ctx.deadline.check(margin=1.0)
            await self._observe()
            if state.model_calls >= self.settings.max_model_calls_per_task:
                await self._audit("budget_exhausted", reason_code="max_model_calls")
                raise BudgetExhaustedError("Máximo de llamadas al modelo alcanzado.")

            action = await self._propose(step)
            if action is None:
                continue

            ZeroShotDiscovery.remember(state, action.screen_summary)
            if state.history and action.previous_step_assessment and state.history[-1].model_assessment is None:
                state.history[-1].model_assessment = action.previous_step_assessment

            if action.action == ActionType.ASK_HUMAN:
                await self._audit("policy_decision", outcome="require_human", reason_code="ask_human",
                                  details=action.reasoning)
                return self._result(TaskState.NEEDS_HUMAN_REVIEW, "human_required",
                                    f"El agente necesita intervención humana: {action.reasoning}")
            if action.action == ActionType.DONE:
                final = await self._handle_done(action)
                if final is not None:
                    return final
                continue
            await self._execute_pipeline(action, origin="model")

        st = TaskState.UNCERTAIN if state.pending_uncertain_effect else TaskState.FAILED
        return self._result(st, "max_steps", "Límite de pasos alcanzado sin verificar el objetivo.")

    async def _propose(self, step: int) -> Optional[ProposedAction]:
        ctx, state = self._ctx, self._state
        request = self._build_request(step)
        obs_id = request.observation_meta.get("observation_id")
        await self._audit("model_request_started", observation_id=obs_id, data={"step": step})
        try:
            timeout = ctx.deadline.timeout_for(self.settings.model_timeout_seconds, margin=1.0)
            raw = await asyncio.wait_for(self.model.propose_action(request), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError, ModelProviderError) as exc:
            if ctx.deadline.remaining() <= 1.0:
                raise DeadlineExceededError("Deadline agotado esperando al modelo.") from None
            self._model_errors += 1
            state.model_calls += 1
            await self._audit("model_response_received", outcome="failed", reason_code="provider_error",
                              observation_id=obs_id, details=type(exc).__name__)
            if self._model_errors >= 3:
                raise _ModelUnavailable() from None
            state.mark_stale()
            return None
        except ModelOutputError as exc:
            state.model_calls += 1
            return await self._reject_output(obs_id, str(exc))
        state.model_calls += 1
        usage = getattr(self.model, "last_usage", None)
        await self._audit(
            "model_response_received",
            observation_id=obs_id,
            data={"input_tokens": getattr(usage, "input_tokens", 0),
                  "output_tokens": getattr(usage, "output_tokens", 0),
                  "estimated_cost_usd": str(getattr(usage, "estimated_cost_usd", "0"))},
        )
        try:
            action = self._validate_model_action(raw)
        except ModelOutputError as exc:
            return await self._reject_output(obs_id, str(exc))
        await self._audit(
            "proposal_validated",
            observation_id=action.observation_id,
            details=f"{action.action.value}: {action.reasoning[:300]}",
            data={"action": action.action.value, "target": action.target_description},
        )
        return action

    async def _reject_output(self, obs_id, message: str) -> None:
        state = self._state
        state.invalid_proposals += 1
        await self._audit("proposal_rejected", outcome="rejected", reason_code="invalid_model_output",
                          observation_id=obs_id, details=message)
        if state.invalid_proposals >= self.settings.max_invalid_proposals:
            raise _InvalidOutput()
        state.add_feedback(f"Tu respuesta anterior no cumplía el contrato JSON ({message[:200]}). Corrígela.")
        state.mark_stale()
        return None

    def _build_request(self, step: int) -> ModelRequest:
        ctx, state = self._ctx, self._state
        image = None
        mime = None
        if state.current_visual is not None and ctx.perception_mode in ("visual_only", "auto"):
            meta = state.current_visual.model_metadata()
            image = state.current_image
            mime = f"image/{state.current_visual.image_format}"
        elif state.current_dom is not None:
            meta = {"observation_id": state.current_dom.observation_id}
        else:
            meta = {}
        dom_text = None
        if state.current_dom is not None and ctx.perception_mode in ("dom", "auto"):
            dom_text = state.current_dom.to_model_prompt(DataRedactor.redact(state.current_dom.page_url))
            meta["dom_observation_id"] = state.current_dom.observation_id
        guidance: list[str] = []
        for skill in self.skills:
            if skill.applies(ctx):
                guidance.extend(skill.guidance(ctx, state))
        return ModelRequest(
            objective=DataRedactor.redact_secrets_only(ctx.objective),
            perception_mode=ctx.perception_mode,
            step_index=step,
            max_steps=ctx.max_steps,
            remaining_seconds=ctx.deadline.remaining(),
            observation_meta=meta,
            image_bytes=image,
            image_mime=mime,
            dom_text=dom_text,
            history=tuple(DataRedactor.redact_secrets_only(r.to_prompt_line()) for r in state.history),
            navigation_memory=tuple(state.navigation_memory),
            feedback=state.pop_feedback(),
            secret_names=tuple(sorted(ctx.secret_names)),
            guidance=tuple(guidance),
            max_output_tokens=self.settings.max_output_tokens,
            timeout_seconds=self.settings.model_timeout_seconds,
        )

    # ================================================================ observación
    async def _observe(self) -> None:
        ctx, state = self._ctx, self._state
        if not self.profile.permissions.read_visible_data:
            raise PolicyViolationError("Lectura de la interfaz deshabilitada por el perfil.")
        page = self.browser.page
        if page is None or page.is_closed():
            raise ObservationError("No existe página activa.")
        url = page.url
        if url.lower().startswith(("http:", "https:")) and not self.network_validator.is_url_allowed(url):
            raise PolicyViolationError("Página fuera del ámbito permitido.")
        session_id = getattr(self.browser, "session_id", "ses_unknown")
        page_id = getattr(self.browser, "page_id", "pag_unknown")
        epoch = getattr(self.browser, "navigation_epoch", 0)
        timeout = ctx.deadline.timeout_for(10.0, margin=0.5)

        if ctx.perception_mode in ("visual_only", "auto"):
            sensitive = self._last_typed_secret
            obs, image = await asyncio.wait_for(
                VisualObserver.capture(
                    page,
                    task_id=ctx.task_id,
                    run_id=ctx.run_id,
                    session_id=session_id,
                    page_id=page_id,
                    navigation_epoch=epoch,
                    geometry_revision=state.geometry_revision,
                    max_image_bytes=self.settings.max_image_bytes,
                    timeout_ms=timeout * 1000,
                    sensitive=sensitive,
                ),
                timeout=timeout,
            )
            if state.last_screen_hash is not None and state.history and state.history[-1].screen_changed is None:
                # Señal de diagnóstico, nunca veredicto de éxito (§13.1).
                state.history[-1].screen_changed = obs.image_sha256 != state.last_screen_hash
            state.last_screen_hash = obs.image_sha256
            state.current_visual = obs
            state.current_image = image
            self._last_typed_secret = False
            self._maybe_persist_evidence(obs, image)
            await self._audit(
                "observation_created",
                observation_id=obs.observation_id,
                data={"kind": "visual", "sha256": obs.image_sha256, "bytes": obs.image_bytes_len,
                      "viewport": [obs.viewport_width_css, obs.viewport_height_css],
                      "image": [obs.image_width_px, obs.image_height_px], "sensitive": obs.sensitive},
            )
        if ctx.perception_mode in ("dom", "auto"):
            dom = await asyncio.wait_for(
                DOMObserver.capture(
                    page,
                    run_id=ctx.run_id,
                    session_id=session_id,
                    page_id=page_id,
                    navigation_epoch=epoch,
                    geometry_revision=state.geometry_revision,
                ),
                timeout=timeout,
            )
            state.current_dom = dom
            await self._audit("observation_created", observation_id=dom.observation_id,
                              data={"kind": "dom", "elements": len(dom.elements)})
        state.observation_stale = False

    def _maybe_persist_evidence(self, obs, image: bytes) -> None:
        if not self.settings.persist_screenshots or obs.sensitive:
            return
        try:
            base = Path(self.settings.evidence_dir).resolve() / self._ctx.run_id
            base.mkdir(parents=True, exist_ok=True)
            (base / f"{obs.observation_id}.{obs.image_format}").write_bytes(image)
        except OSError:
            pass  # La evidencia visual es opcional; la auditoría JSONL es la obligatoria.

    # ================================================================ SkillRuntime
    async def run_primitive(self, action: ProposedAction, *, origin: str) -> bool:
        return await self._execute_pipeline(action, origin=origin)

    async def observe_visual(self) -> str:
        await self._observe()
        return self._state.last_screen_hash or ""

    # ================================================================ ejecución
    async def _execute_pipeline(self, action: ProposedAction, *, origin: str) -> bool:
        ctx, state = self._ctx, self._state
        live = await self.executor.live_geometry()
        try:
            validated = self.validator.validate(action, ctx, state, live)
        except ActionRejectedError as rej:
            await self._audit("proposal_rejected", outcome="rejected", reason_code=rej.reason_code,
                              observation_id=action.observation_id, details=str(rej))
            if rej.reason_code == "deadline":
                raise DeadlineExceededError("Sin tiempo para nuevas acciones.") from None
            if not self._healing.on_rejection(state, rej.reason_code, str(rej)):
                raise _RecoveryExhausted() from None
            return False

        decision = self.policy_enforcer.decide(validated, ctx)
        await self._audit(
            "policy_decision",
            outcome=decision.outcome.value,
            reason_code=decision.reason_code,
            observation_id=action.observation_id,
            details=decision.message,
            data={"risk": decision.risk.value, "origin": origin},
            blocked=decision.outcome != DecisionOutcome.ALLOW,
        )
        if decision.outcome == DecisionOutcome.DENY:
            raise PolicyViolationError(decision.message)
        if decision.outcome == DecisionOutcome.REQUIRE_HUMAN:
            raise HumanApprovalRequiredError(decision.message)

        summary = self._describe(action, validated)
        await self._audit("action_started", observation_id=action.observation_id, details=summary,
                          data={"action": action.action.value, "risk": decision.risk.value,
                                "x_css": validated.x_css, "y_css": validated.y_css})
        state.steps_executed += 1
        coords = (validated.x_css, validated.y_css) if validated.x_css is not None else None
        persistent = RISK_ORDER[decision.risk] >= RISK_ORDER[RiskLevel.R2]
        try:
            outcome = await self.executor.execute(validated, ctx)
        except PolicyViolationError:
            raise
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                if persistent:
                    state.pending_uncertain_effect = True
                raise
            if isinstance(exc, DeadlineExceededError) or ctx.deadline.remaining() <= 0.5:
                if persistent:
                    state.pending_uncertain_effect = True
                raise DeadlineExceededError("Deadline agotado durante la acción.") from None
            recovery = self._healing.on_failure(state, exc, risk=decision.risk, emitted=True)
            state.history.append(StepRecord(index=state.steps_executed, action=action.action.value,
                                            summary=summary, outcome=f"FALLO ({recovery.failure.value})",
                                            risk=decision.risk.value, coords=coords))
            await self._audit("action_failed", outcome="failed", reason_code=recovery.failure.value,
                              observation_id=action.observation_id, details=type(exc).__name__)
            await self._audit("recovery_started", reason_code=recovery.action.value,
                              data={"possible_side_effect": recovery.possible_side_effect})
            if recovery.action == RecoveryAction.ABORT:
                raise _RecoveryExhausted() from None
            return False

        if persistent:
            state.persistent_actions_executed += 1
        if action.secret_ref is not None:
            self._last_typed_secret = True
        state.mark_stale()
        state.history.append(StepRecord(index=state.steps_executed, action=action.action.value, summary=summary,
                                        outcome=outcome.detail, risk=decision.risk.value, coords=coords))
        if outcome.extracted:
            state.extracted.append(DataRedactor.redact(outcome.extracted)[:2000])
        await self._audit("action_completed", outcome="ok", observation_id=action.observation_id,
                          details=outcome.detail)
        self._emit_progress(steps_executed=state.steps_executed, last_action=summary[:200])
        return True

    @staticmethod
    def _describe(action: ProposedAction, validated) -> str:
        t = action.action
        target = f" '{action.target_description[:80]}'" if action.target_description else ""
        if t in (ActionType.CLICK_XY, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK):
            return f"{t.value}{target} en norm({action.x},{action.y})"
        if t == ActionType.TYPE_TEXT:
            what = f"secret_ref={action.secret_ref}" if action.secret_ref else f"texto '{(action.text or '')[:60]}'"
            return f"type_text {what}{target}"
        if t == ActionType.PRESS_KEY:
            return f"press_key {action.key}{target}"
        if t == ActionType.SCROLL:
            return f"scroll ({action.delta_x or 0},{action.delta_y or 0}){target}"
        if t == ActionType.WAIT:
            return f"wait {action.duration_seconds}s"
        if t == ActionType.NAVIGATE:
            return "navigate al destino autorizado"
        return f"{t.value} {action.target_element_id or ''}{target}"

    # ================================================================ verificación
    async def _handle_done(self, action: ProposedAction) -> Optional[RunResult]:
        ctx, state = self._ctx, self._state
        state.done_attempts += 1
        vr: Optional[VerificationResult] = None
        if ctx.perception_mode in ("dom", "auto"):
            vr = await asyncio.wait_for(self.verifier.verify_dom_condition(self.browser.page),
                                        timeout=ctx.deadline.timeout_for(10.0, margin=0.5))
        if vr is None and ctx.perception_mode in ("visual_only", "auto"):
            if state.model_calls >= self.settings.max_model_calls_per_task:
                raise BudgetExhaustedError("Máximo de llamadas al modelo alcanzado.")
            timeout = ctx.deadline.timeout_for(self.settings.model_timeout_seconds, margin=1.0)
            try:
                vr = await self.verifier.verify_visual(
                    self.model, ctx, state,
                    claimed_evidence=action.claimed_evidence,
                    timeout=timeout,
                    max_output_tokens=min(512, self.settings.max_output_tokens),
                )
            except (TimeoutError, asyncio.TimeoutError, ModelProviderError, ModelOutputError):
                if ctx.deadline.remaining() <= 1.0:
                    raise DeadlineExceededError("Deadline agotado durante la verificación.") from None
                vr = VerificationResult(status=VerificationStatus.INCIERTO,
                                        evidence="La verificación independiente no respondió.")
            state.model_calls += 1
        if vr is None:
            vr = VerificationResult(status=VerificationStatus.INCIERTO,
                                    evidence="El objetivo no define una postcondición verificable.")
        await self._audit(
            "verification_result",
            outcome=vr.status.value,
            observation_id=state.current_observation_id,
            details=vr.evidence,
            data={"evidence_kind": vr.evidence_kind.value, "criteria": list(vr.criteria),
                  "attempt": state.done_attempts},
            success=vr.status == VerificationStatus.VERIFICADO,
        )
        if vr.status == VerificationStatus.VERIFICADO:
            state.pending_uncertain_effect = False
            summary = f"Objetivo completado y verificado ({vr.evidence_kind.value}). {vr.evidence}"
            return self._result(TaskState.SUCCEEDED_VERIFIED, "verified", "Condición final verificada.",
                                verification=vr, summary=summary, possible_side_effects=False)
        if state.done_attempts < self.settings.max_done_attempts and ctx.deadline.remaining() > 10.0:
            state.add_feedback(
                "La verificación independiente NO confirma que el objetivo esté cumplido: "
                f"{vr.evidence[:400]} Continúa trabajando hacia el objetivo o corrige lo que falte."
            )
            state.mark_stale()
            return None
        if vr.status == VerificationStatus.NO_VERIFICADO:
            return self._result(TaskState.FAILED, "not_verified",
                                "No existe evidencia suficiente de que el objetivo se haya cumplido.", verification=vr)
        return self._result(TaskState.UNCERTAIN, "uncertain",
                            "La evidencia disponible no permite confirmar el resultado.", verification=vr)
