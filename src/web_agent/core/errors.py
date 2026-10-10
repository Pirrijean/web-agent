"""Errores tipados del agente. Permiten clasificar fallos sin depender de texto."""


class WebAgentError(Exception):
    """Base de todos los errores controlados del agente."""


class PolicyViolationError(PermissionError, WebAgentError):
    """Una regla de seguridad o autorización impide continuar. Nunca se reintenta."""


class HumanApprovalRequiredError(PolicyViolationError):
    """La operación requiere aprobación humana y no existe canal (§8.9)."""


class AmbiguousTargetError(WebAgentError):
    """El objetivo no es único o carece de nombre accesible. No se clica a ciegas."""


class TargetNotFoundError(WebAgentError):
    """El objetivo ya no existe en la página."""


class StaleObservationError(WebAgentError):
    """La página cambió desde la observación sobre la que se decidió la acción."""


class ActionRejectedError(WebAgentError):
    """La validación contextual rechazó la propuesta: se reobserva, no se ejecuta.

    No es una violación de política: indica que la propuesta no puede aplicarse
    a la observación vigente (caducada, fuera de geometría, repetida...).
    """

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class ActionNotVerifiedError(WebAgentError):
    """La acción se ejecutó pero su consecuencia no pudo comprobarse."""


class ExecutionError(WebAgentError):
    """El ejecutor no pudo completar los eventos de una acción autorizada."""


class ObservationError(WebAgentError):
    """No se pudo producir una observación válida."""


class ModelOutputError(WebAgentError):
    """El modelo devolvió una salida que no respeta el contrato de acciones."""


class ModelProviderError(WebAgentError):
    """El proveedor de inferencia falló (red, cuota, error HTTP)."""


class BudgetExhaustedError(WebAgentError):
    """El presupuesto de inferencia de la tarea está agotado."""


class DeadlineExceededError(TimeoutError, WebAgentError):
    """El deadline absoluto de la tarea se agotó."""


class AuditUnavailableError(WebAgentError):
    """No es posible conservar la trazabilidad obligatoria."""
