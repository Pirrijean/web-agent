import logging
from enum import Enum

logger = logging.getLogger(__name__)

class FailureType(str, Enum):
    TRANSIENT_NETWORK = "transient_network"
    ELEMENT_NOT_FOUND = "element_not_found"
    STALE_ELEMENT = "stale_element"
    SECURITY_VIOLATION = "security_violation"
    UNKNOWN = "unknown"

class RecoveryStrategy:
    """Clasifica los fallos de Playwright y Políticas para decidir si son recuperables (Fase 4)."""
    
    @staticmethod
    def classify_error(error: Exception) -> FailureType:
        from web_agent.core.errors import PolicyViolationError, TargetNotFoundError, StaleObservationError
        
        error_str = str(error).lower()
        if isinstance(error, PolicyViolationError):
            return FailureType.SECURITY_VIOLATION
        if isinstance(error, TargetNotFoundError):
            return FailureType.ELEMENT_NOT_FOUND
        if isinstance(error, StaleObservationError):
            return FailureType.STALE_ELEMENT
        
        # Fallback for underlying playwright errors
        if "timeout" in error_str and "waiting for locator" in error_str:
            return FailureType.ELEMENT_NOT_FOUND
        if "detached" in error_str or "stale" in error_str:
            return FailureType.STALE_ELEMENT
        if "net::err" in error_str:
            return FailureType.TRANSIENT_NETWORK
        return FailureType.UNKNOWN

    @staticmethod
    def can_recover(failure: FailureType) -> bool:
        """
        Regla inquebrantable: Las violaciones de política nunca se reintentan.
        Se requiere intervención o abortar.
        """
        if failure == FailureType.SECURITY_VIOLATION:
            logger.critical("Violación de política detectada. Abortando recuperación.")
            return False
        return failure in [FailureType.TRANSIENT_NETWORK, FailureType.ELEMENT_NOT_FOUND, FailureType.STALE_ELEMENT]
