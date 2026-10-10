"""Errores tipados del agente. Permiten clasificar fallos sin depender de texto."""


class WebAgentError(Exception):
    """Base de todos los errores controlados del agente."""


class PolicyViolationError(PermissionError, WebAgentError):
    """Una regla de seguridad o autorización impide continuar. Nunca se reintenta."""


class AmbiguousTargetError(WebAgentError):
    """El objetivo no es único o carece de nombre accesible. No se clica a ciegas."""


class TargetNotFoundError(WebAgentError):
    """El objetivo ya no existe en la página."""


class StaleObservationError(WebAgentError):
    """La página cambió desde la observación sobre la que se decidió la acción."""


class ActionNotVerifiedError(WebAgentError):
    """La acción se ejecutó pero su consecuencia no pudo comprobarse."""


class ModelOutputError(WebAgentError):
    """El modelo devolvió una salida que no respeta el contrato de acciones."""
