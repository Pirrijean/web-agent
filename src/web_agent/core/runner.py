import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from web_agent.audit.events import AuditEvent, AuditLogger
from web_agent.browser.observe import DOMObserver
from web_agent.core.errors import (
    ActionNotVerifiedError,
    AuditUnavailableError,
    ModelOutputError,
    PolicyViolationError,
)
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.schemas import (
    MAX_OBJECTIVE_CHARS,
    ActionType,
    ElementRef,
    FinalState,
    PageObservation,
    ProposedAction,
    RunResult,
    TaskProfile,
)
from web_agent.security.redaction import DataRedactor


@dataclass
class _Progress:
    actions_attempted: int = 0
    policy_blocks: int = 0


class AgentRunner:
    def __init__(self, profile: TaskProfile, model, browser, audit_logger=None):
        # Reconstruir: no confiar en una instancia creada con model_construct().
        self.profile = TaskProfile.model_validate(
            profile.model_dump(mode="json")
        )
        self.model = model
        self.browser = browser
        self.audit_logger = (
            audit_logger if audit_logger is not None else AuditLogger()
        )
        self.policy_enforcer = SecurityPolicyEnforcer(self.profile)

        self.browser.network_validator = (
            self.policy_enforcer.network_validator
        )
        self.browser.headless = self.profile.browser.headless
        self.browser.chromium_sandbox = (
            self.profile.browser.chromium_sandbox
        )

        self._running = False
        self._current_elements: dict[str, ElementRef] = {}

    def _result(
        self,
        run_id: str,
        progress: _Progress,
        state: FinalState,
        reason: str,
    ) -> RunResult:
        return RunResult(
            run_id=run_id,
            final_state=state,
            steps_executed=progress.actions_attempted,
            blocked_requests=progress.policy_blocks,
            reason=reason,
        )

    async def _audit(
        self,
        run_id: str,
        action_type: str,
        details: str,
        success: bool,
        blocked: bool = False,
    ):
        event = AuditEvent(
            run_id=run_id,
            action_type=action_type,
            details=DataRedactor.redact(details),
            is_success=success,
            blocked_by_policy=blocked,
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.audit_logger.log_event, event),
                timeout=5.0,
            )
        except Exception as exc:
            # No propagar texto externo que pueda contener secretos.
            raise AuditUnavailableError(
                "Auditoría obligatoria no disponible."
            ) from exc

    @staticmethod
    def _validate_model_action(value) -> ProposedAction:
        try:
            if isinstance(value, ProposedAction):
                value = value.model_dump(mode="json")

            if not isinstance(value, dict):
                raise ValueError("Tipo de propuesta no permitido.")

            return ProposedAction.model_validate(value)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ModelOutputError(
                "La propuesta del modelo no cumple el contrato."
            ) from exc

    async def run(self, objective: str) -> RunResult:
        if (
            not isinstance(objective, str)
            or not objective.strip()
            or len(objective) > MAX_OBJECTIVE_CHARS
            or "\x00" in objective
        ):
            raise ValueError("Objetivo inválido.")

        if self._running:
            raise RuntimeError(
                "Una sesión no puede ejecutar tareas concurrentes."
            )

        self._running = True
        run_id = f"run_{uuid.uuid4().hex}"
        progress = _Progress()
        result = None
        cleanup_failed = False

        try:
            async with asyncio.timeout(
                self.profile.execution.task_timeout_seconds
            ):
                await self._audit(
                    run_id, "run_start", "Inicio autorizado.", True
                )
                # El inicio queda dentro del ámbito de limpieza.
                await self.browser.start()
                result = await self._run_loop(
                    run_id,
                    DataRedactor.redact(objective),
                    progress,
                )

        except AuditUnavailableError:
            result = self._result(
                run_id,
                progress,
                FinalState.BLOCKED,
                "Ejecución detenida: auditoría no disponible.",
            )

        except PolicyViolationError:
            progress.policy_blocks += 1
            result = self._result(
                run_id,
                progress,
                FinalState.BLOCKED,
                "Operación bloqueada por la política de seguridad.",
            )

        except (TimeoutError, asyncio.TimeoutError):
            result = self._result(
                run_id,
                progress,
                FinalState.FAILED,
                "Tiempo global agotado. No se repiten operaciones.",
            )

        except Exception:
            result = self._result(
                run_id,
                progress,
                FinalState.FAILED,
                "Ejecución abortada por un fallo interno.",
            )

        finally:
            try:
                await asyncio.wait_for(self.browser.stop(), timeout=7.0)
            except Exception:
                cleanup_failed = True
            finally:
                self._running = False

        if cleanup_failed:
            result = self._result(
                run_id,
                progress,
                FinalState.FAILED,
                "No se pudo confirmar el cierre de todos los recursos.",
            )

        try:
            await self._audit(
                run_id,
                "run_end",
                result.final_state.value,
                result.final_state == FinalState.SUCCEEDED_VERIFIED,
                result.final_state == FinalState.BLOCKED,
            )
        except AuditUnavailableError:
            result = self._result(
                run_id,
                progress,
                FinalState.BLOCKED,
                "No se pudo persistir el estado final.",
            )

        return result

    async def _observe(self) -> str:
        if not self.profile.permissions.read_visible_data:
            return PageObservation(
                url="about:blank",
                title="Observación deshabilitada por permisos.",
            ).to_model_prompt()

        page = self.browser.page
        if page is None or page.is_closed():
            raise ActionNotVerifiedError("No existe página activa.")

        if (
            page.url != "about:blank"
            and not self.policy_enforcer.network_validator.is_url_allowed(
                page.url
            )
        ):
            raise PolicyViolationError("Página fuera del ámbito permitido.")

        snapshot = await DOMObserver.capture_observation(page)
        elements_list = []
        for eid, item in snapshot.elements.items():
            elements_list.append(
                ElementRef(
                    element_id=eid,
                    role=item.role,
                    name=item.name,
                    enabled=item.is_enabled,
                    form_submit=(item.role == "button" and "submit" in (item.form_context or "").lower()),
                    context="form" if item.form_context else "page",
                    unique=True,
                )
            )
        self._current_elements = {el.element_id: el for el in elements_list}
        observation = PageObservation(
            url=DataRedactor.redact(page.url),
            title=DataRedactor.redact(snapshot.title)[:300],
            elements=tuple(elements_list),
            visible_text="",
            truncated=len(snapshot.title) > 300,
        )
        return observation.to_model_prompt()

    async def _run_loop(
        self,
        run_id: str,
        objective: str,
        progress: _Progress,
    ) -> RunResult:
        for _ in range(self.profile.execution.max_steps):
            page_state = await self._observe()

            try:
                proposed = await asyncio.wait_for(
                    self.model.propose_action(objective, page_state),
                    timeout=self.profile.execution.model_timeout_seconds,
                )
                action = self._validate_model_action(proposed)
            except (TimeoutError, asyncio.TimeoutError):
                return self._result(
                    run_id,
                    progress,
                    FinalState.FAILED,
                    "El modelo no respondió dentro del límite.",
                )
            except ModelOutputError:
                return self._result(
                    run_id,
                    progress,
                    FinalState.FAILED,
                    "Salida del modelo inválida.",
                )

            # Toda acción, incluso DONE, pasa por la política.
            self.policy_enforcer.validate_action(action)

            await self._audit(
                run_id,
                action.action_type.value,
                "Propuesta validada por la política.",
                True,
            )

            if action.action_type == ActionType.ASK_HUMAN:
                return self._result(
                    run_id,
                    progress,
                    FinalState.BLOCKED,
                    "Intervención humana no admitida en modo de ejecución autónoma.",
                )

            if action.action_type == ActionType.DONE:
                verified = await asyncio.wait_for(
                    self._verify_success(),
                    timeout=self.profile.execution.action_timeout_ms / 1000.0,
                )
                return self._result(
                    run_id,
                    progress,
                    (
                        FinalState.SUCCEEDED_VERIFIED
                        if verified
                        else FinalState.FAILED
                    ),
                    (
                        "Condición final verificada."
                        if verified
                        else "No existe evidencia suficiente de éxito."
                    ),
                )

            progress.actions_attempted += 1

            # Sin reintentos: no existe aún un contrato de repetición seguro.
            try:
                await asyncio.wait_for(
                    self._execute_action(action),
                    timeout=self.profile.execution.action_timeout_ms / 1000.0,
                )
            except PolicyViolationError:
                raise
            except Exception:
                return self._result(
                    run_id,
                    progress,
                    FinalState.FAILED,
                    f"Resultado de acción {action.action_type.value} no verificable. No se repite.",
                )

            await self._audit(
                run_id,
                action.action_type.value,
                "Acción ejecutada y verificada.",
                True,
            )

        return self._result(
            run_id,
            progress,
            FinalState.FAILED,
            "Límite de pasos alcanzado.",
        )

    async def _execute_action(self, action: ProposedAction):
        self.policy_enforcer.validate_action(action)

        page = self.browser.page
        if page is None or page.is_closed():
            raise ActionNotVerifiedError("No existe página activa.")

        t = action.action_type
        if t == ActionType.NAVIGATE:
            await self._execute_navigation(action)
        elif t == ActionType.CLICK:
            await self._execute_click(action)
        elif t == ActionType.FILL:
            await self._execute_fill(action)
        elif t == ActionType.SUBMIT:
            await self._execute_submit(action)
        elif t == ActionType.EXTRACT:
            await self._execute_extract(action)
        else:
            raise PolicyViolationError(f"Tipo de acción no soportado: {t}")

    async def _execute_navigation(self, action: ProposedAction):
        page = self.browser.page
        if page is None or page.is_closed():
            raise ActionNotVerifiedError("No existe página activa.")

        response = await page.goto(
            action.url,
            wait_until="domcontentloaded",
            timeout=self.profile.execution.action_timeout_ms,
        )

        if not self.policy_enforcer.network_validator.is_url_allowed(page.url):
            raise PolicyViolationError("Destino final no autorizado.")

        if response is None or not 200 <= response.status < 400:
            raise ActionNotVerifiedError("Respuesta HTTP no verificable.")

    async def _execute_click(self, action: ProposedAction):
        page = self.browser.page
        if not action.target_element_id or action.target_element_id not in self._current_elements:
            raise ActionNotVerifiedError(f"Elemento {action.target_element_id} no encontrado en la observación actual.")

        el = self._current_elements[action.target_element_id]
        locator = page.get_by_role(el.role, name=el.name, exact=True)
        count = await locator.count()
        if count != 1:
            locator = page.get_by_text(el.name, exact=True)
            count = await locator.count()
            if count != 1:
                raise ActionNotVerifiedError("Objetivo de clic ambiguo o no encontrado.")

        if not await locator.is_visible() or await locator.is_disabled():
            raise ActionNotVerifiedError("Elemento no accionable (oculto o deshabilitado).")

        await locator.click(timeout=self.profile.execution.action_timeout_ms)
        await page.wait_for_load_state("domcontentloaded")

    async def _execute_fill(self, action: ProposedAction):
        page = self.browser.page
        if not action.target_element_id or action.target_element_id not in self._current_elements:
            raise ActionNotVerifiedError(f"Elemento {action.target_element_id} no encontrado en la observación actual.")

        el = self._current_elements[action.target_element_id]
        val = action.value
        if val is None:
            for inp in self.profile.inputs:
                if inp.key == action.target_element_id or inp.key in el.name.lower():
                    val = inp.value
                    break
        if val is None:
            raise ActionNotVerifiedError("Valor para fill no proporcionado ni resuelto en catálogo.")

        locator = page.get_by_role(el.role, name=el.name, exact=True)
        count = await locator.count()
        if count != 1:
            locator = page.get_by_label(el.name, exact=True)
            count = await locator.count()
            if count != 1:
                locator = page.locator(f"input[name='{el.name}'], textarea[name='{el.name}']")
                count = await locator.count()
                if count != 1:
                    raise ActionNotVerifiedError("Campo de formulario ambiguo o no encontrado.")

        if not await locator.is_visible() or await locator.is_disabled():
            raise ActionNotVerifiedError("Campo no editable.")

        await locator.fill(val, timeout=self.profile.execution.action_timeout_ms)
        actual_val = await locator.input_value()
        if actual_val != val:
            raise ActionNotVerifiedError("El valor introducido no coincide con el valor esperado.")

    async def _execute_submit(self, action: ProposedAction):
        page = self.browser.page
        if not action.target_element_id or action.target_element_id not in self._current_elements:
            raise ActionNotVerifiedError(f"Elemento {action.target_element_id} no encontrado en la observación actual.")

        el = self._current_elements[action.target_element_id]
        locator = page.get_by_role(el.role, name=el.name, exact=True)
        if await locator.count() != 1:
            locator = page.locator(f"button[type='submit']:has-text('{el.name}')")
            if await locator.count() != 1:
                raise ActionNotVerifiedError("Botón de envío ambiguo o no encontrado.")

        await locator.click(timeout=self.profile.execution.action_timeout_ms)
        await page.wait_for_load_state("domcontentloaded")

    async def _execute_extract(self, action: ProposedAction):
        page = self.browser.page
        if action.target_element_id:
            if action.target_element_id not in self._current_elements:
                raise ActionNotVerifiedError(f"Elemento {action.target_element_id} no encontrado.")
            el = self._current_elements[action.target_element_id]
            locator = page.get_by_role(el.role, name=el.name, exact=True)
            if await locator.count() != 1:
                locator = page.get_by_text(el.name, exact=True)
            text = await locator.inner_text()
        else:
            text = await page.inner_text("body")
        return text

    async def _verify_success(self) -> bool:
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
