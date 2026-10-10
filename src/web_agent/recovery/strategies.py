"""Clasificación de fallos y decisión de recuperación (documento maestro §8.12, §13.3).

Regla general: reobservar → clasificar → comprobar posibles efectos →
elegir alternativa autorizada. Las violaciones de política nunca se reintentan
y una operación persistente con resultado incierto no se repite a ciegas.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

from web_agent.core.errors import (
    ActionNotVerifiedError,
    ActionRejectedError,
    AmbiguousTargetError,
    AuditUnavailableError,
    DeadlineExceededError,
    ExecutionError,
    ModelOutputError,
    PolicyViolationError,
    StaleObservationError,
    TargetNotFoundError,
)
from web_agent.core.schemas import RISK_ORDER, RiskLevel

logger = logging.getLogger(__name__)


class FailureType(str, Enum):
    TRANSIENT_NETWORK = "transient_network"
    ELEMENT_NOT_FOUND = "element_not_found"
    AMBIGUOUS_TARGET = "ambiguous_target"
    STALE_ELEMENT = "stale_element"
    REJECTED_PROPOSAL = "rejected_proposal"
    ACTION_TIMEOUT = "action_timeout"
    NOT_VERIFIED = "not_verified"
    MODEL_OUTPUT = "model_output"
    SECURITY_VIOLATION = "security_violation"
    AUDIT_FAILURE = "audit_failure"
    DEADLINE = "deadline"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    REOBSERVE = "reobserve"
    REOBSERVE_AND_CHECK_EFFECT = "reobserve_and_check_effect"
    ABORT = "abort"


@dataclass(frozen=True)
class RecoveryDecision:
    failure: FailureType
    action: RecoveryAction
    possible_side_effect: bool
    guidance: str


class RecoveryStrategy:
    @staticmethod
    def classify_error(error: BaseException) -> FailureType:
        if isinstance(error, PolicyViolationError):
            return FailureType.SECURITY_VIOLATION
        if isinstance(error, AuditUnavailableError):
            return FailureType.AUDIT_FAILURE
        if isinstance(error, DeadlineExceededError):
            return FailureType.DEADLINE
        if isinstance(error, ActionRejectedError):
            return FailureType.REJECTED_PROPOSAL
        if isinstance(error, AmbiguousTargetError):
            return FailureType.AMBIGUOUS_TARGET
        if isinstance(error, TargetNotFoundError):
            return FailureType.ELEMENT_NOT_FOUND
        if isinstance(error, StaleObservationError):
            return FailureType.STALE_ELEMENT
        if isinstance(error, ModelOutputError):
            return FailureType.MODEL_OUTPUT
        if isinstance(error, ActionNotVerifiedError):
            return FailureType.NOT_VERIFIED
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            return FailureType.ACTION_TIMEOUT
        text = str(error).lower()
        if "timeout" in text:
            return FailureType.ACTION_TIMEOUT
        if "detached" in text or "stale" in text:
            return FailureType.STALE_ELEMENT
        if "net::err" in text:
            return FailureType.TRANSIENT_NETWORK
        if isinstance(error, ExecutionError):
            return FailureType.UNKNOWN
        return FailureType.UNKNOWN

    @staticmethod
    def can_recover(failure: FailureType) -> bool:
        """Las violaciones de política, fallos de auditoría y deadline nunca se recuperan."""
        if failure == FailureType.SECURITY_VIOLATION:
            logger.critical("Violación de política detectada. Abortando recuperación.")
            return False
        return failure not in (FailureType.AUDIT_FAILURE, FailureType.DEADLINE)

    @classmethod
    def decide(cls, error: BaseException, *, risk: RiskLevel, emitted: bool) -> RecoveryDecision:
        failure = cls.classify_error(error)
        if not cls.can_recover(failure):
            return RecoveryDecision(failure, RecoveryAction.ABORT, emitted, "No recuperable.")
        persistent = RISK_ORDER[risk] >= RISK_ORDER[RiskLevel.R2]
        if emitted and persistent and failure in (FailureType.ACTION_TIMEOUT, FailureType.UNKNOWN,
                                                  FailureType.TRANSIENT_NETWORK, FailureType.NOT_VERIFIED):
            return RecoveryDecision(
                failure,
                RecoveryAction.REOBSERVE_AND_CHECK_EFFECT,
                True,
                "La operación anterior pudo haber persistido. Antes de repetirla, comprueba en pantalla "
                "si ya se aplicó (registro creado, mensaje de confirmación). No la repitas a ciegas.",
            )
        return RecoveryDecision(
            failure,
            RecoveryAction.REOBSERVE,
            emitted and persistent,
            "La acción anterior no se completó. Analiza la nueva captura y elige una alternativa; "
            "no repitas la misma acción si no produjo efecto.",
        )
