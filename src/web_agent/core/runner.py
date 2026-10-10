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
from web_agent.browser.novnc import connect_viewer
from web_agent.browser.observe import DOMObserver
from web_agent.browser.observe_visual import VisualObserver
from web_agent.config import Settings
from web_agent.core.action_validator import ContextualValidator, ValidatedAction
from web_agent.core.adaptive_router import (
    LEVEL_CLASSIC,
    LEVEL_LOCAL,
    LEVEL_MACRO,
    AdaptiveRouter,
    Level3Candidate,
    extract_click_targets,
)
from web_agent.core.context import RunState, SecretStore, StepRecord, TaskContext
from web_agent.core.deadline import Deadline
from web_agent.core.macro_schemas import ParsedProposal, parse_model_output, risk_allowed_in_burst
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
from web_agent.core.policy import Decision, DecisionOutcome, SecurityPolicyEnforcer
from web_agent.core.result import RunResult, TaskState, VerificationResult, VerificationStatus
from web_agent.core.schemas import (
    DESKTOP_PASSWORD_SECRET,
    VISUAL_ACTIONS,
    MAX_OBJECTIVE_CHARS,
    RISK_ORDER,
    ActionType,
    ProposedAction,
    RiskLevel,
    TaskProfile,
)
from web_agent.core.verifier import Verifier
from web_agent.models.base import ModelRequest
from web_agent.perception.dual_vision import THUMB_SIZE, build_dual_view
from web_agent.perception.image_ops import change_map
from web_agent.perception.ocr_engine import NullOcrEngine, OcrEngine, OcrUnavailable, create_ocr_engine
from web_agent.recovery.strategies import RecoveryAction
from web_agent.security.redaction import DataRedactor
from web_agent.skills import DesktopAuthAndBootstrap, SelfHealingRetry, ZeroShotDiscovery, default_skills

ProgressCallback = Callable[[dict], None]


def _answer_line(answer: str) -> str:
    """``respuesta: <valor>`` en una sola línea (el evaluador lee el primer número de esa línea)."""
    value = " ".join(answer.replace("respuesta:", " ").split())
    return f"respuesta: {value}"


def _report_summary(summary: str, *, final: TaskState) -> str:
    """Resumen público: sin secretos ni credenciales; solo un éxito verificado lleva ``respuesta:``."""
    text = DataRedactor.redact_for_report(summary or "")
    if final != TaskState.SUCCEEDED_VERIFIED:
        text = "\n".join(line for line in text.splitlines() if not line.strip().lower().startswith("respuesta:"))
    return text[:2000]

BURST_MAX_CLUSTERS = 3
_QUIET_LEVEL3_REASONS = frozenset({"level3_not_allowed", "level3_no_literal_target", "level3_screen_already_analyzed"})
OCR_LOAD_TIMEOUT_SECONDS = 60.0
SUCCESS_TERMS = ("guardado", "completado", "operacion exitosa", "exitosa", "exito", "informe generado",
                 "generado", "exportado", "registrado", "creado", "actualizado")
ERROR_TERMS = ("error", "fallo", "fallido", "no se pudo", "invalido", "denegado")


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
        ocr_engine: Optional[OcrEngine] = None,
        router: Optional[AdaptiveRouter] = None,
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
        self._ocr_engine = ocr_engine
        self._router_override = router
        self.router: AdaptiveRouter = router or AdaptiveRouter(level3_enabled=False, level2_enabled=False)
        self._roi_box: Optional[tuple[int, int, int, int]] = None
        self._last_snapshot: Optional[bytes] = None
        self._ocr_loading: Optional[asyncio.Future] = None

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
            summary=_report_summary(summary, final=state_enum),
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
            click_hold_ms=self.settings.click_hold_ms,
            clear_before_default=self.settings.clear_before_default,
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
                self.router = await self._build_router()
                await self._audit("router_configured", data=self.router.describe())
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
            if self._ocr_loading is not None:
                self._ocr_loading.cancel()
                self._ocr_loading = None
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

    # ================================================================ motor tri-nivel
    async def _build_router(self) -> AdaptiveRouter:
        ctx, settings = self._ctx, self.settings
        if self._router_override is not None:
            router = self._router_override
        elif not settings.tiered_engine:
            router = AdaptiveRouter(level3_enabled=False, level2_enabled=False)
        else:
            ocr: OcrEngine = self._ocr_engine or NullOcrEngine("Sin objetivos literales: OCR no necesario.")
            wants_ocr = (settings.level3_enabled and self._ocr_engine is None
                         and ctx.perception_mode != "dom" and extract_click_targets(ctx.objective))
            if wants_ocr:
                # Carga en segundo plano (en paralelo a la conexión del visor): el Nivel 3 se activa
                # en cuanto el OCR está listo; mientras tanto deciden los niveles 2 y 1.
                ocr = NullOcrEngine("OCR cargando en segundo plano.")
                self._ocr_loading = asyncio.ensure_future(asyncio.wait_for(asyncio.to_thread(
                    create_ocr_engine, enabled=True, min_free_memory_mb=settings.min_free_memory_mb,
                    threads=settings.ocr_threads), timeout=OCR_LOAD_TIMEOUT_SECONDS))
            router = AdaptiveRouter(
                ocr=ocr,
                level3_enabled=settings.level3_enabled,
                level2_enabled=settings.level2_enabled,
                dual_vision_enabled=settings.dual_vision_enabled,
                level3_timeout_seconds=settings.level3_timeout_seconds,
            )
        router.start(ctx)
        return router

    async def _attach_loaded_ocr(self) -> None:
        task = self._ocr_loading
        if task is None or not task.done():
            return
        self._ocr_loading = None
        try:
            engine = task.result()
        except BaseException:
            engine = NullOcrEngine("La carga del OCR falló o superó su plazo.")
        self.router.attach_ocr(engine, self._ctx)
        await self._audit("router_ocr_ready", outcome="ok" if engine.available else "unavailable",
                          data={"ocr": engine.name if engine.available else engine.unavailable_reason})

    # ================================================================ bucle
    async def _run_loop(self) -> RunResult:
        ctx, state = self._ctx, self._state
        if ctx.desktop_url and self._bootstrap is not None:
            await self._audit("skill_started", details=self._bootstrap.spec.skill_id)
            ok = await self._bootstrap.bootstrap(self, ctx)
            await self._audit("skill_result", outcome="ok" if ok else "failed", details=self._bootstrap.spec.skill_id)
            if not ok:
                return self._result(TaskState.FAILED, "internal_error", "No se pudo conectar con el escritorio autorizado.")

        router = self.router
        for step in range(ctx.max_steps):
            ctx.deadline.check(margin=1.0)
            previous_image = state.current_image
            await self._observe()
            router.remember_image(previous_image)
            await self._attach_loaded_ocr()
            event = router.begin_step(state)
            if event is not None:
                await self._audit(event, observation_id=state.current_observation_id, data={"level": LEVEL_LOCAL})
            await self._check_expected_text()

            # Nivel 3: vía local sin inferencia (solo R0).
            attempt = await router.try_level_3(ctx, state)
            if attempt.candidate is not None:
                if await self._execute_level3(attempt.candidate):
                    continue
            elif attempt.reason not in _QUIET_LEVEL3_REASONS:
                await self._audit("level3_escalated", reason_code=attempt.reason,
                                  observation_id=state.current_observation_id)

            if state.model_calls >= self.settings.max_model_calls_per_task:
                await self._audit("budget_exhausted", reason_code="max_model_calls")
                raise BudgetExhaustedError("Máximo de llamadas al modelo alcanzado.")

            parsed = await self._propose(step)
            if parsed is None:
                router.force_classic()
                continue
            action = parsed.actions[0]

            ZeroShotDiscovery.remember(state, action.screen_summary)
            if state.history and action.previous_step_assessment and state.history[-1].model_assessment is None:
                state.history[-1].model_assessment = action.previous_step_assessment

            if action.action == ActionType.ASK_HUMAN:
                await self._audit("policy_decision", outcome="require_human", reason_code="ask_human",
                                  details=action.reasoning)
                return self._result(TaskState.NEEDS_HUMAN_REVIEW, "human_required",
                                    f"No se realizó la tarea: {action.reasoning}")
            if action.action == ActionType.DONE:
                final = await self._handle_done(action)
                if final is not None:
                    return final
                continue
            if parsed.is_burst:
                await self._execute_burst(parsed)
            else:
                ok = await self._execute_pipeline(action, origin="model", level=LEVEL_CLASSIC)
                router.on_model_step(LEVEL_CLASSIC, ok)

        st = TaskState.UNCERTAIN if state.pending_uncertain_effect else TaskState.FAILED
        return self._result(st, "max_steps", "Límite de pasos alcanzado sin verificar el objetivo.")

    async def _propose(self, step: int) -> Optional[ParsedProposal]:
        ctx, state = self._ctx, self._state
        request = self._build_request(step)
        obs_id = request.observation_meta.get("observation_id")
        await self._audit("model_request_started", observation_id=obs_id,
                          data={"step": step, "level": LEVEL_MACRO if request.allow_macro else LEVEL_CLASSIC,
                                "dual_vision": bool(request.aux_images)})
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
            parsed = parse_model_output(
                raw,
                validate=self._validate_model_action,
                observation_id=state.current_observation_id,
                allow_macro=request.allow_macro,
                roi_box=self._roi_box,
            )
        except ModelOutputError as exc:
            return await self._reject_output(obs_id, str(exc))
        action = parsed.actions[0]
        await self._audit(
            "proposal_validated",
            observation_id=action.observation_id,
            details=f"{action.action.value}: {action.reasoning[:300]}",
            data={"action": action.action.value, "target": action.target_description},
        )
        if parsed.macro is not None:
            await self._audit(
                "level2_macro_proposed",
                outcome="burst" if parsed.is_burst else "single",
                reason_code=parsed.truncated_reason,
                observation_id=action.observation_id,
                details=parsed.macro.macro_action,
                data={"steps_proposed": len(parsed.macro.steps), "steps_accepted": len(parsed.actions),
                      "level": LEVEL_MACRO},
            )
        return parsed

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
        aux: tuple = ()
        self._roi_box = None
        allow_macro = self.router.macro_allowed(ctx, state)
        if state.current_visual is not None and ctx.perception_mode in ("visual_only", "auto"):
            meta = state.current_visual.model_metadata()
            image = state.current_image
            mime = f"image/{state.current_visual.image_format}"
            dual = self._dual_view() if self.router.dual_vision_allowed(ctx, state) else None
            if dual is not None:
                image, mime, aux = dual.thumbnail, dual.thumbnail_mime, ((dual.roi, dual.roi_mime),)
                meta.update(dual.meta(), image_width_px=THUMB_SIZE[0], image_height_px=THUMB_SIZE[1])
                self._roi_box = dual.roi_box_norm
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
            allow_macro=allow_macro,
            aux_images=aux,
        )

    def _dual_view(self):
        state = self._state
        vis = state.current_visual
        last = state.history[-1] if state.history else None
        if vis is None or last is None or last.coords is None:
            return None
        try:
            return build_dual_view(state.current_image, self.router.previous_image, last.coords,
                                   width=vis.image_width_px, height=vis.image_height_px)
        except Exception:
            return None  # ante cualquier problema de imagen: captura completa

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

    async def connect_viewer(self) -> str:
        """Conecta noVNC si el destino es un visor noVNC (el escritorio llega como wss://…/websockify)."""
        ctx = self._ctx
        page = self.browser.page
        if page is None:
            return "not_novnc"
        password = None
        if DESKTOP_PASSWORD_SECRET in ctx.secret_names:
            try:
                password = self.executor.secrets.resolve(DESKTOP_PASSWORD_SECRET)
            except KeyError:
                password = None
        budget = min(20.0, max(1.0, ctx.deadline.remaining() - 30.0))
        session = self.browser if hasattr(self.browser, "expect_password_prompt") else None
        try:
            status = await asyncio.wait_for(connect_viewer(page, password, timeout=budget, session=session),
                                            timeout=budget + 2.0)
        except (TimeoutError, asyncio.TimeoutError):
            status = "timeout"
        except Exception:
            status = "error"
        finally:
            password = None
        await self._audit("viewer_connect", outcome=status, data={"viewer": "novnc"},
                          success=status == "connected")
        return status

    # ================================================================ ejecución
    async def _execute_pipeline(self, action: ProposedAction, *, origin: str, level: int = LEVEL_CLASSIC) -> bool:
        authorized = await self._authorize(action, origin=origin, level=level)
        if authorized is None:
            return False
        validated, decision = authorized
        return await self._perform(action, validated, decision, origin=origin, level=level)

    async def _authorize(
        self,
        action: ProposedAction,
        *,
        origin: str,
        level: int,
        live=None,
        strict: bool = True,
        count_recovery: bool = True,
    ) -> Optional[tuple[ValidatedAction, Decision]]:
        """Validación contextual + política. ``strict=False`` no aborta la tarea ante DENY/R3:
        lo usan el Nivel 3 y la ráfaga del Nivel 2, que simplemente escalan."""
        ctx, state = self._ctx, self._state
        live = live or await self.executor.live_geometry()
        try:
            validated = self.validator.validate(action, ctx, state, live)
        except ActionRejectedError as rej:
            await self._audit("proposal_rejected", outcome="rejected", reason_code=rej.reason_code,
                              observation_id=action.observation_id, details=str(rej),
                              data={"origin": origin, "level": level})
            if rej.reason_code == "deadline":
                raise DeadlineExceededError("Sin tiempo para nuevas acciones.") from None
            if count_recovery and not self._healing.on_rejection(state, rej.reason_code, str(rej)):
                raise _RecoveryExhausted() from None
            return None

        decision = self.policy_enforcer.decide(validated, ctx)
        await self._audit(
            "policy_decision",
            outcome=decision.outcome.value,
            reason_code=decision.reason_code,
            observation_id=action.observation_id,
            details=decision.message,
            data={"risk": decision.risk.value, "origin": origin, "level": level},
            blocked=decision.outcome != DecisionOutcome.ALLOW,
        )
        if strict:
            if decision.outcome == DecisionOutcome.DENY:
                raise PolicyViolationError(decision.message)
            if decision.outcome == DecisionOutcome.REQUIRE_HUMAN:
                raise HumanApprovalRequiredError(decision.message)
        return validated, decision

    async def _perform(self, action: ProposedAction, validated: ValidatedAction, decision: Decision, *,
                       origin: str, level: int) -> bool:
        ctx, state = self._ctx, self._state
        summary = self._describe(action, validated)
        await self._audit("action_started", observation_id=action.observation_id, details=summary,
                          data={"action": action.action.value, "risk": decision.risk.value,
                                "x_css": validated.x_css, "y_css": validated.y_css,
                                "origin": origin, "level": level})
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
        reference = state.current_image
        state.mark_stale()
        self._last_snapshot = None
        if action.action in VISUAL_ACTIONS:
            # Tras guardar/aplicar (R2) el repintado puede llegar >1 s después: con una espera corta
            # el modelo veía el formulario sin guardar y volvía a pulsar (registro duplicado).
            min_wait = self.settings.persist_settle_seconds if persistent else 0.0
            self._last_snapshot = await self._settle(reference, min_wait=min_wait)
        state.history.append(StepRecord(index=state.steps_executed, action=action.action.value, summary=summary,
                                        outcome=outcome.detail, risk=decision.risk.value, coords=coords))
        if outcome.extracted:
            state.extracted.append(DataRedactor.redact(outcome.extracted)[:2000])
        await self._audit("action_completed", outcome="ok", observation_id=action.observation_id,
                          details=outcome.detail)
        self._emit_progress(steps_executed=state.steps_executed, last_action=summary[:200])
        return True

    # ================================================================ Nivel 3
    async def _execute_level3(self, cand: Level3Candidate) -> bool:
        """Ejecuta un candidato local solo si la política lo clasifica R0 y lo permite."""
        authorized = await self._authorize(cand.action, origin="level3", level=LEVEL_LOCAL,
                                           strict=False, count_recovery=False)
        if authorized is None:
            self.router.on_level3_escalated(cand)
            return False
        validated, decision = authorized
        if decision.outcome != DecisionOutcome.ALLOW or decision.risk != RiskLevel.R0:
            # Mitigación 3: el Nivel 3 nunca ejecuta nada por encima de R0.
            await self._audit("level3_escalated", reason_code="risk_above_r0",
                              observation_id=cand.action.observation_id,
                              data={"risk": decision.risk.value, "target": cand.target.text})
            self.router.on_level3_escalated(cand)
            return False
        await self._audit(
            "level3_action_selected",
            observation_id=cand.action.observation_id,
            details=f"Acción local determinista: '{cand.target.text}'",
            data={"target": cand.target.text, "from_cache": cand.from_cache,
                  "ocr_seconds": round(cand.elapsed_seconds, 3), "confidence": round(cand.box.confidence, 3),
                  "level": LEVEL_LOCAL},
        )
        ok = await self._perform(cand.action, validated, decision, origin="level3", level=LEVEL_LOCAL)
        if ok:
            self.router.on_level3_executed(cand)
        else:
            self.router.on_level3_escalated(cand)
        return ok

    # ================================================================ Nivel 2
    async def _snapshot(self) -> Optional[bytes]:
        page = self.browser.page
        if page is None or page.is_closed():
            return None
        timeout = self._ctx.deadline.timeout_for(5.0, margin=0.5)
        return await asyncio.wait_for(
            page.screenshot(type="png", full_page=False, scale="css", timeout=timeout * 1000), timeout=timeout)

    async def _settle(self, reference: Optional[bytes], min_wait: float = 0.0) -> Optional[bytes]:
        """Espera el repintado tras una acción: primero a que la pantalla cambie (hasta
        ``settle_max_seconds``) y después a que se mantenga estable. Devuelve la última captura.
        ``min_wait``: tiempo mínimo antes de devolver (acciones persistentes con repintado tardío)."""
        max_wait = self.settings.settle_max_seconds
        if max_wait <= 0 or reference is None:
            return None
        loop = asyncio.get_running_loop()
        start = loop.time()
        min_wait = max(0.0, min(min_wait, self._ctx.deadline.remaining() - 5.0))
        max_wait = max(max_wait, min_wait)
        hard_end = start + min(max_wait + 1.5, max(0.0, self._ctx.deadline.remaining() - 3.0))
        stable_for = self.settings.settle_stable_seconds
        last: Optional[bytes] = None
        changed_at: Optional[float] = None
        while True:
            await asyncio.sleep(0.12)
            try:
                snap = await self._snapshot()
            except Exception:
                return last
            if snap is None:
                return last
            now = loop.time()
            if changed_at is None:
                if not change_map(reference, snap).unchanged:
                    last, changed_at = snap, now
                elif now - start >= max_wait:
                    return snap  # sin efecto visible: se informa al modelo como «pantalla SIN cambios»
            elif change_map(last, snap).unchanged:
                if now - changed_at >= stable_for and now - start >= min_wait:
                    return snap
            else:
                last, changed_at = snap, now
            if now >= hard_end:
                return snap

    async def _execute_burst(self, parsed: ParsedProposal) -> None:
        """Macro-acción: autoriza todos los pasos sobre la observación vigente y ejecuta en ráfaga.

        - El primer paso tiene semántica de Nivel 1 (política estricta).
        - Los siguientes solo si la política los permite con riesgo ≤ R1; si no, la ráfaga
          se trunca ahí (el paso se re-propondrá en Nivel 1 con una captura nueva).
        - Entre pasos se comprueba que la página y la geometría no cambiaron y que la
          pantalla solo cambió localmente (guardián de cambios); si no, se interrumpe.
        """
        ctx, state, router = self._ctx, self._state, self.router
        live0 = await self.executor.live_geometry()
        plan: list[tuple[ProposedAction, ValidatedAction, Decision]] = []
        truncated: Optional[str] = parsed.truncated_reason
        for idx, action in enumerate(parsed.actions):
            first = idx == 0
            authorized = await self._authorize(action, origin="level2_macro", level=LEVEL_MACRO, live=live0,
                                               strict=first, count_recovery=first)
            if authorized is None:
                if first:
                    router.on_model_step(LEVEL_MACRO, False)
                    return
                truncated = f"paso_{idx + 1}_rechazado"
                break
            validated, decision = authorized
            if not first and (decision.outcome != DecisionOutcome.ALLOW or not risk_allowed_in_burst(decision.risk)):
                truncated = f"paso_{idx + 1}_riesgo_{decision.risk.value}"
                break
            plan.append((action, validated, decision))

        executed = 0
        aborted_reason: Optional[str] = None
        previous = state.current_image
        for idx, (action, validated, decision) in enumerate(plan):
            if idx > 0:
                if ctx.deadline.remaining() <= 3.0:
                    aborted_reason = "sin_tiempo"
                    break
                live = await self.executor.live_geometry()
                if live != live0:
                    aborted_reason = "pagina_o_geometria_cambio"
                    break
                current = self._last_snapshot  # captura tras el repintado del paso anterior
                if current is None:
                    try:
                        current = await self._snapshot()
                    except Exception:
                        current = None
                if current is None or previous is None:
                    aborted_reason = "sin_captura_de_control"
                    break
                changes = change_map(previous, current)
                if (changes.changed_fraction > self.settings.burst_max_changed_fraction
                        or changes.clusters > BURST_MAX_CLUSTERS):
                    aborted_reason = "pantalla_cambio_inesperadamente"
                    break
                previous = current
            ok = await self._perform(action, validated, decision, origin="level2_macro", level=LEVEL_MACRO)
            if not ok:
                aborted_reason = f"paso_{idx + 1}_fallo"
                break
            executed += 1

        aborted = aborted_reason is not None
        router.on_burst(executed, aborted)
        router.on_model_step(LEVEL_MACRO, executed > 0 and not aborted)
        if not aborted and parsed.macro is not None and parsed.macro.expected_final_text:
            router.expected_text = parsed.macro.expected_final_text
        await self._audit(
            "level2_burst_finished",
            outcome="aborted" if aborted else "ok",
            reason_code=aborted_reason or truncated,
            details=parsed.macro.macro_action if parsed.macro else "",
            data={"planned": len(plan), "executed": executed, "level": LEVEL_MACRO},
        )
        if aborted and executed:
            state.add_feedback(
                f"La macro se interrumpió tras {executed} paso(s) ({aborted_reason}). "
                "Revisa la captura actual antes de continuar; no repitas los pasos ya hechos."
            )
        elif truncated and executed == len(plan) and len(plan) < len(parsed.macro.steps if parsed.macro else ()):
            state.add_feedback(
                f"Solo se ejecutaron {executed} paso(s) de la macro ({truncated}): los pasos persistentes "
                "o arriesgados deben proponerse como acción individual."
            )

    async def _check_expected_text(self) -> None:
        """Postcondición local de la ráfaga: ¿se ve el texto que la macro anunció?"""
        router, state = self.router, self._state
        expected, router.expected_text = router.expected_text, None
        if not expected or not router.ocr.available or state.current_image is None:
            return
        try:
            found = await router.ocr.find_text(state.current_image, [expected],
                                               timeout=self.settings.level3_timeout_seconds)
        except (OcrUnavailable, TimeoutError, asyncio.TimeoutError):
            return
        visible = bool(found.matches)
        await self._audit("level2_expected_text", outcome="found" if visible else "not_found",
                          observation_id=state.current_observation_id, data={"level": LEVEL_MACRO})
        if not visible:
            router.force_classic()
            state.add_feedback(f"Tras la macro no se ve el texto esperado «{expected[:80]}». "
                               "Comprueba la pantalla antes de seguir.")

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
    async def _wait_canvas_stable(self) -> bool:
        """Espera (acotada) a que el lienzo no cambie durante ``verify_stability_seconds``.

        Devuelve ``True`` si la pantalla difiere de la observación vigente (hay que reobservar).
        La observación ya cuenta como muestra: si se tomó hace más que la ventana y la captura
        actual coincide, no se espera nada.
        """
        ctx, state = self._ctx, self._state
        window = self.settings.verify_stability_seconds
        vis = state.current_visual
        if window <= 0 or vis is None or state.current_image is None:
            return False
        loop = asyncio.get_running_loop()
        end = loop.time() + min(window * 2.5, max(0.0, ctx.deadline.remaining() - 15.0))
        reference, stable_since = state.current_image, vis.captured_at_monotonic
        differs = False
        while True:
            try:
                current = await self._snapshot()
            except Exception:
                return differs
            if current is None:
                return differs
            if change_map(reference, current).unchanged:
                if loop.time() - stable_since >= window:
                    break
            else:
                differs, reference, stable_since = True, current, loop.time()
            if loop.time() >= end:
                break
            await asyncio.sleep(min(0.3, max(0.05, window - (loop.time() - stable_since))))
        await self._audit("canvas_stability", outcome="stable" if loop.time() - stable_since >= window else "unstable",
                          observation_id=state.current_observation_id, data={"window_s": window})
        return differs

    async def _ocr_hints(self) -> tuple[str, ...]:
        router, state = self.router, self._state
        if not router.ocr.available or state.current_image is None:
            return ()
        try:
            found = await router.ocr.find_text(state.current_image, SUCCESS_TERMS + ERROR_TERMS,
                                               timeout=self.settings.level3_timeout_seconds, contains=True)
        except (OcrUnavailable, TimeoutError, asyncio.TimeoutError):
            return ()
        hints = tuple(f"OCR local leyó «{b.text}»" for b in found.matches[:6])
        if hints:
            await self._audit("ocr_hints", observation_id=state.current_observation_id,
                              data={"hints": list(hints)})
        return hints

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
            # Mitigación 2: en escritorios remotos no hay respuestas HTTP que verificar; se exige
            # un lienzo estable y se aportan lecturas OCR como señal NO concluyente.
            done_image = state.current_image  # puede contener un toast de confirmación transitorio
            if await self._wait_canvas_stable():
                await self._observe()
            hints = await self._ocr_hints()
            timeout = ctx.deadline.timeout_for(self.settings.model_timeout_seconds, margin=1.0)
            try:
                claimed = action.claimed_evidence
                if action.answer:
                    claimed = f"{claimed or ''} Respuesta propuesta: {action.answer}".strip()
                vr = await self.verifier.verify_visual(
                    self.model, ctx, state,
                    claimed_evidence=claimed,
                    timeout=timeout,
                    max_output_tokens=min(512, self.settings.max_output_tokens),
                    local_hints=hints,
                    prior_image=done_image,
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
            if action.answer:
                summary = f"{_answer_line(action.answer)}\n{summary}"
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
