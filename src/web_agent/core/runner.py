import asyncio
import logging
import uuid
from typing import Optional

from web_agent.audit.events import AuditEvent, AuditLogger
from web_agent.browser.session import SecureBrowserSession
from web_agent.core.errors import ActionNotVerifiedError, PolicyViolationError
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.schemas import ActionType, FinalState, ProposedAction, RunResult, TaskProfile
from web_agent.models.base import BaseModelAdapter
from web_agent.recovery.strategies import FailureType, RecoveryStrategy
from web_agent.security.redaction import DataRedactor

logger = logging.getLogger(__name__)


class AgentRunner:
    """Planificador que orquesta el bucle estricto: Observar -> Validar -> Ejecutar -> Verificar (Fase 3)."""

    def __init__(
        self,
        profile: TaskProfile,
        model: BaseModelAdapter,
        browser: SecureBrowserSession,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.profile = profile
        self.model = model
        self.browser = browser
        self.policy_enforcer = SecurityPolicyEnforcer(profile)

        # WA-04: Conectar la política de red obligatoria antes de iniciar la sesión
        self.browser.network_validator = self.policy_enforcer.network_validator

        # WA-07: Conectar registrador de auditoría
        self.audit_logger = audit_logger or AuditLogger()

    async def run(self, objective: str) -> RunResult:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        try:
            return await asyncio.wait_for(
                self._run_loop(run_id, objective),
                timeout=self.profile.execution.task_timeout_seconds,
            )
        except asyncio.TimeoutError:
            safe_err = DataRedactor.redact("Global task timeout exceeded.")
            logger.error(safe_err)
            return RunResult(
                run_id=run_id,
                final_state=FinalState.FAILED,
                steps_executed=0,
                reason="Límite de tiempo global de la tarea superado.",
            )

    async def _run_loop(self, run_id: str, objective: str) -> RunResult:
        steps_executed = 0
        await self.browser.start()
        try:
            for step in range(self.profile.execution.max_steps):
                steps_executed += 1

                # 1. Observar
                page_state = await self._get_page_state()

                # 2. Proponer (El LLM delibera sin tocar el DOM)
                try:
                    action: ProposedAction = await asyncio.wait_for(
                        self.model.propose_action(objective, page_state),
                        timeout=self.profile.execution.model_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.error("Tiempo límite del modelo agotado.")
                    return RunResult(
                        run_id=run_id,
                        final_state=FinalState.FAILED,
                        steps_executed=steps_executed,
                        reason="El modelo no respondió dentro del tiempo límite.",
                    )
                except Exception as exc:
                    safe_msg = DataRedactor.redact(f"Fallo del modelo: {exc}")
                    logger.error(safe_msg)
                    return RunResult(
                        run_id=run_id,
                        final_state=FinalState.FAILED,
                        steps_executed=steps_executed,
                        reason="Error irrecuperable al consultar el modelo.",
                    )

                # WA-08: Redactar antes de registrar logs
                logger.info("Paso %d | Acción propuesta: %s", step, action.action_type.value)

                # WA-03: Manejo estricto de DONE e independencia del modelo
                if action.action_type == ActionType.DONE:
                    verified = await self._verify_success()
                    final_state = (
                        FinalState.SUCCEEDED_VERIFIED
                        if verified
                        else FinalState.NEEDS_HUMAN_REVIEW
                    )
                    self._audit(
                        run_id=run_id,
                        action_type=action.action_type.value,
                        details=f"DONE propuesto por el modelo. Verificado={verified}",
                        is_success=verified,
                        blocked=False,
                    )
                    return RunResult(
                        run_id=run_id,
                        final_state=final_state,
                        steps_executed=steps_executed,
                        reason=(
                            "Objetivo verificado exitosamente."
                            if verified
                            else "El modelo propuso DONE pero la condición de éxito no pudo verificarse."
                        ),
                    )

                if action.action_type == ActionType.ASK_HUMAN:
                    self._audit(
                        run_id=run_id,
                        action_type=action.action_type.value,
                        details="Intervención humana solicitada.",
                        is_success=True,
                        blocked=False,
                    )
                    return RunResult(
                        run_id=run_id,
                        final_state=FinalState.NEEDS_HUMAN_REVIEW,
                        steps_executed=steps_executed,
                        reason="El agente solicitó confirmación humana.",
                    )

                # 3. Validar con política de seguridad
                try:
                    self.policy_enforcer.validate_action(action)
                except PolicyViolationError as exc:
                    safe_reason = DataRedactor.redact(str(exc))
                    logger.error("Acción bloqueada por política: %s", safe_reason)
                    self._audit(
                        run_id=run_id,
                        action_type=action.action_type.value,
                        details=safe_reason,
                        is_success=False,
                        blocked=True,
                    )
                    return RunResult(
                        run_id=run_id,
                        final_state=FinalState.BLOCKED,
                        steps_executed=steps_executed,
                        reason=safe_reason,
                        blocked_requests=1,
                    )

                # 4. Ejecutar con política de reintentos
                success = False
                last_error: Optional[Exception] = None

                for attempt in range(self.profile.execution.max_retries_per_action + 1):
                    try:
                        await asyncio.wait_for(
                            self._execute_action(action),
                            timeout=self.profile.execution.action_timeout_ms / 1000.0,
                        )
                        success = True
                        break
                    except PolicyViolationError as exc:
                        # Una violación durante la ejecución (ej. redirección no autorizada) nunca se reintenta
                        last_error = exc
                        safe_reason = DataRedactor.redact(str(exc))
                        logger.error("Violación de política durante ejecución: %s", safe_reason)
                        self._audit(
                            run_id=run_id,
                            action_type=action.action_type.value,
                            details=safe_reason,
                            is_success=False,
                            blocked=True,
                        )
                        return RunResult(
                            run_id=run_id,
                            final_state=FinalState.BLOCKED,
                            steps_executed=steps_executed,
                            reason=safe_reason,
                            blocked_requests=1,
                        )
                    except Exception as exc:
                        last_error = exc
                        failure_type = RecoveryStrategy.classify_error(exc)
                        if not RecoveryStrategy.can_recover(failure_type):
                            safe_msg = DataRedactor.redact(f"Error irrecuperable: {exc}")
                            logger.error(safe_msg)
                            break
                        safe_warn = DataRedactor.redact(f"Reintento {attempt} tras fallo transitorio: {exc}")
                        logger.warning(safe_warn)
                        await asyncio.sleep(1)

                self._audit(
                    run_id=run_id,
                    action_type=action.action_type.value,
                    details="Ejecución exitosa" if success else DataRedactor.redact(str(last_error)),
                    is_success=success,
                    blocked=False,
                )

                if not success:
                    return RunResult(
                        run_id=run_id,
                        final_state=FinalState.FAILED,
                        steps_executed=steps_executed,
                        reason=DataRedactor.redact(f"Fallo tras agotar reintentos: {last_error}"),
                    )

            return RunResult(
                run_id=run_id,
                final_state=FinalState.FAILED,
                steps_executed=steps_executed,
                reason="Límite máximo de pasos alcanzado sin cumplir el objetivo.",
            )

        finally:
            await self.browser.stop()

    def _audit(self, run_id: str, action_type: str, details: str, is_success: bool, blocked: bool):
        try:
            self.audit_logger.log_event(
                AuditEvent(
                    run_id=run_id,
                    action_type=action_type,
                    details=details,
                    is_success=is_success,
                    blocked_by_policy=blocked,
                )
            )
        except Exception as exc:
            logger.warning("Fallo al escribir evento de auditoría: %s", DataRedactor.redact(str(exc)))

    async def _get_page_state(self) -> str:
        """Extrae de forma segura la semántica, ocultando atributos frágiles."""
        if self.browser.page and not self.browser.page.is_closed():
            try:
                return await self.browser.page.title()
            except Exception:
                return "Error al leer página"
        return "No page loaded"

    async def _execute_action(self, action: ProposedAction):
        """WA-02: Rechazar operaciones no implementadas; no permitir falsos éxitos."""
        if action.action_type != ActionType.NAVIGATE:
            raise PolicyViolationError("Operación no implementada.")

        page = self.browser.page
        if page is None or page.is_closed():
            raise ActionNotVerifiedError("No existe una página activa.")

        response = await page.goto(
            action.url,
            wait_until="domcontentloaded",
            timeout=self.profile.execution.action_timeout_ms,
        )

        if response is None or response.status >= 400:
            raise ActionNotVerifiedError("Navegación sin respuesta HTTP válida.")

        if not self.policy_enforcer.network_validator.is_url_allowed(page.url):
            raise PolicyViolationError("Destino final no autorizado.")

    async def _verify_success(self) -> bool:
        """WA-03: Verificación determinista independiente contra la condición del perfil."""
        condition = self.profile.verification.success_condition
        page = self.browser.page

        if condition is None or page is None or page.is_closed():
            return False

        if not self.policy_enforcer.network_validator.is_url_allowed(page.url):
            return False

        if condition.url_equals is not None:
            if page.url != condition.url_equals:
                return False

        if condition.visible_text is not None:
            locator = page.get_by_text(condition.visible_text, exact=True)
            if await locator.count() != 1 or not await locator.is_visible():
                return False

        if condition.element_role is not None:
            locator = page.get_by_role(
                condition.element_role,
                name=condition.element_name,
                exact=True,
            )
            if await locator.count() != 1 or not await locator.is_visible():
                return False

        return True

