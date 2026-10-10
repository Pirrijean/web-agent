from playwright.async_api import Locator
import logging

logger = logging.getLogger(__name__)

class DeterministicActions:
    """Capa de interacción estricta y determinista. No hay IA aquí (Fase 1)."""
    
    @staticmethod
    async def click_element(locator: Locator, timeout_ms: int = 5000):
        """Hace clic comprobando accionabilidad y esperando que esté visible."""
        await locator.wait_for(state="visible", timeout=timeout_ms)
        await locator.click(timeout=timeout_ms)
        logger.info("Clic verificado en el elemento estructural.")

    @staticmethod
    async def fill_form(locator: Locator, value: str, timeout_ms: int = 5000):
        """Rellena un campo de formulario con validación estricta de estado."""
        await locator.wait_for(state="visible", timeout=timeout_ms)
        
        # Confirmación determinista de viabilidad
        is_disabled = await locator.is_disabled()
        if is_disabled:
            raise ValueError("Inyección bloqueada: El elemento está deshabilitado.")
            
        await locator.fill(value, timeout=timeout_ms)
        logger.info("Campo de texto completado de forma segura.")
