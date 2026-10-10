"""Estados internos, verificación y resultado (documento maestro §8.2, §13.2)."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskState(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED_VERIFIED = "succeeded_verified"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


TERMINAL_STATES = frozenset(
    {
        TaskState.SUCCEEDED_VERIFIED,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.NEEDS_HUMAN_REVIEW,
        TaskState.TIMED_OUT,
        TaskState.CANCELLED,
        TaskState.UNCERTAIN,
    }
)

# Mapeo al contrato público (estados `en_curso`, `terminada`, `fallida`).
PUBLIC_STATUS = {
    TaskState.ACCEPTED: "en_curso",
    TaskState.QUEUED: "en_curso",
    TaskState.RUNNING: "en_curso",
    TaskState.SUCCEEDED_VERIFIED: "terminada",
    TaskState.FAILED: "fallida",
    TaskState.BLOCKED: "fallida",
    TaskState.NEEDS_HUMAN_REVIEW: "fallida",
    TaskState.TIMED_OUT: "fallida",
    TaskState.CANCELLED: "fallida",
    TaskState.UNCERTAIN: "fallida",
}


class VerificationStatus(str, Enum):
    VERIFICADO = "verificado"
    NO_VERIFICADO = "no_verificado"
    INCIERTO = "incierto"


class EvidenceKind(str, Enum):
    DETERMINISTIC = "deterministic"
    DOM_CONDITION = "dom_condition"
    VISUAL_INTERPRETED = "visual_interpreted"
    NONE = "none"


class VerificationResult(_Strict):
    status: VerificationStatus
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence: str = Field("", max_length=1_000)
    criteria: tuple[str, ...] = ()


class RunResult(_Strict):
    run_id: str
    task_id: Optional[str] = None
    final_state: TaskState
    steps_executed: int = Field(..., ge=0)
    reason: str = Field(..., max_length=2_000)
    summary: str = Field("", max_length=2_000)
    verification: Optional[VerificationResult] = None
    extracted_data: tuple[str, ...] = ()
    blocked_requests: int = Field(0, ge=0)
    model_calls: int = Field(0, ge=0)
    elapsed_seconds: float = Field(0.0, ge=0)
    possible_side_effects: bool = False
    reason_code: Literal[
        "verified",
        "not_verified",
        "uncertain",
        "policy_blocked",
        "human_required",
        "audit_unavailable",
        "timeout",
        "budget_exhausted",
        "max_steps",
        "invalid_model_output",
        "model_unavailable",
        "recovery_exhausted",
        "cleanup_failed",
        "cancelled",
        "internal_error",
    ] = "internal_error"
