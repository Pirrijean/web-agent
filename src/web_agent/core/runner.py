import logging
import asyncio
from web_agent.browser.session import SecureBrowserSession
from web_agent.core.schemas import TaskProfile, ActionType, ProposedAction
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.errors import PolicyViolationError, ActionNotVerifiedError
from web_agent.models.base import BaseModelAdapter
from web_agent.recovery.strategies import RecoveryStrategy, FailureType

logger = logging.getLogger(__name__)

class AgentRunner:
    """Planificador que orquesta el bucle estricto: Observar -> Validar -> Ejecutar (Fase 3)."""
    
    def __init__(self, profile: TaskProfile, model: BaseModelAdapter, browser: SecureBrowserSession):
        self.profile = profile
        self.model = model
        self.browser = browser
        self.policy_enforcer = SecurityPolicyEnforcer(profile)

    async def run(self, objective: str):
        try:
            await asyncio.wait_for(self._run_loop(objective), timeout=self.profile.execution.task_timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("Global task timeout exceeded.")
            
    async def _run_loop(self, objective: str):
        await self.browser.start()
        try:
            for step in range(self.profile.execution.max_steps):
                # 1. Observar
                page_state = await self._get_page_state()
                
                # 2. Proponer (El LLM delibera sin tocar el DOM)
                try:
                    action: ProposedAction = await asyncio.wait_for(
                        self.model.propose_action(objective, page_state),
                        timeout=self.profile.execution.model_timeout_seconds
                    )
                except asyncio.TimeoutError:
                    logger.error("Model timed out.")
                    break
                except Exception as e:
                    logger.error(f"Model failed: {e}")
                    break
                    
                logger.info(f"Paso {step} | IA Propone: {action.action_type} | Razón: {action.reasoning}")
                
                if action.action_type == ActionType.DONE:
                    logger.info("Objetivo alcanzado según el modelo.")
                    break
                    
                # 3. Validar (El Sistema dispone)
                try:
                    self.policy_enforcer.validate_action(action)
                except PolicyViolationError as e:
                    logger.error(f"BLOCKED: {e}")
                    break 
                    
                # 4. Ejecutar (Núcleo determinista - Fase 1)
                success = False
                for attempt in range(self.profile.execution.max_retries_per_action + 1):
                    try:
                        await asyncio.wait_for(
                            self._execute_action(action),
                            timeout=self.profile.execution.action_timeout_ms / 1000.0
                        )
                        success = True
                        break
                    except Exception as e:
                        failure_type = RecoveryStrategy.classify_error(e)
                        if not RecoveryStrategy.can_recover(failure_type):
                            logger.error(f"Unrecoverable error during action: {e}")
                            break
                        logger.warning(f"Action failed (attempt {attempt}), retrying... {e}")
                        await asyncio.sleep(1)
                
                if not success:
                    logger.error("Action failed after retries.")
                    break
                    
        finally:
            await self.browser.stop()

    async def _get_page_state(self) -> str:
        """Extrae de forma segura la semántica, ocultando atributos frágiles."""
        if self.browser.page:
            return await self.browser.page.title()
        return "No page loaded"

    async def _execute_action(self, action: ProposedAction):
        """Traduce el contrato validado a llamadas de Playwright."""
        if action.action_type == ActionType.NAVIGATE and action.url:
            await self.browser.page.goto(action.url)
            
        if self.profile.verification.require_post_action_check:
            # Fake verification for now: check if page still exists
            if not self.browser.page or self.browser.page.is_closed():
                raise ActionNotVerifiedError("Página cerrada tras acción.")
