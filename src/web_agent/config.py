"""Configuración del servicio (documento maestro §15.2).

Los límites se leen del entorno del operador, nunca del payload de una tarea.
El plazo total por tarea tiene un tope duro de 300 s que la configuración solo
puede reducir, no ampliar (§12.3).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

HARD_TASK_TIMEOUT_SECONDS = 300.0

WorkerMode = Literal["subprocess", "inprocess"]
ModelProvider = Literal["openrouter", "scripted"]


def _load_env_file() -> None:
    candidates = (
        Path(".env"),
        Path(__file__).resolve().parents[2] / ".env",
    )
    for env_path in candidates:
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            break


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "si", "sí"}


def _env_decimal(name: str, default: str) -> Decimal:
    raw = _env_str(name, default)
    try:
        value = Decimal(raw)
    except Exception:
        value = Decimal(default)
    if not value.is_finite() or value < 0:
        value = Decimal(default)
    return value


@dataclass(frozen=True)
class Settings:
    # --- Servicio REST ---
    bearer_token: str | None = field(default=None, repr=False)
    host: str = "0.0.0.0"
    port: int = 8000
    max_request_body_bytes: int = 64 * 1024
    expose_docs: bool = False

    # --- Modelo ---
    model_provider: ModelProvider = "openrouter"
    model_name: str = "google/gemini-2.0-flash-001"
    openrouter_api_key: str | None = field(default=None, repr=False)
    model_timeout_seconds: float = 60.0
    max_output_tokens: int = 1024
    max_model_calls_per_task: int = 60
    max_task_cost_usd: Decimal = Decimal("1.00")
    price_input_per_mtok_usd: Decimal = Decimal("0.10")
    price_output_per_mtok_usd: Decimal = Decimal("0.40")
    max_image_bytes: int = 3_500_000

    # --- Ejecución ---
    task_timeout_seconds: float = HARD_TASK_TIMEOUT_SECONDS
    max_actions_per_task: int = 40
    max_recovery_attempts: int = 6
    max_invalid_proposals: int = 3
    max_done_attempts: int = 3
    action_timeout_seconds: float = 15.0
    cleanup_timeout_seconds: float = 5.0
    observation_max_age_seconds: float = 15.0
    typing_delay_ms: int = 20
    max_concurrent_tasks: int = 2
    worker_mode: WorkerMode = "subprocess"

    # --- Navegador ---
    headless: bool = True
    chromium_sandbox: bool = True
    viewport_width: int = 1280
    viewport_height: int = 800

    # --- Red ---
    allow_private_desktop: bool = True
    allow_loopback_desktop: bool = False

    # --- Auditoría / evidencia ---
    audit_dir: str = "./data/audit"
    persist_screenshots: bool = False
    evidence_dir: str = "./data/evidence"

    def __post_init__(self) -> None:
        # El tope de 300 s nunca puede ampliarse por configuración.
        if not 0 < self.task_timeout_seconds <= HARD_TASK_TIMEOUT_SECONDS:
            object.__setattr__(self, "task_timeout_seconds", HARD_TASK_TIMEOUT_SECONDS)

    @property
    def model_configured(self) -> bool:
        if self.model_provider == "scripted":
            return True
        return bool(self.openrouter_api_key)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        provider = _env_str("WEB_AGENT_MODEL_PROVIDER", "openrouter")
        if provider not in ("openrouter", "scripted"):
            provider = "openrouter"
        worker_mode = _env_str("WEB_AGENT_WORKER_MODE", "subprocess")
        if worker_mode not in ("subprocess", "inprocess"):
            worker_mode = "subprocess"
        return cls(
            bearer_token=_env_str("EVALUATION_BEARER_TOKEN") or _env_str("WEB_AGENT_BEARER_TOKEN"),
            host=_env_str("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8000, 1, 65535),
            max_request_body_bytes=_env_int("WEB_AGENT_MAX_BODY_BYTES", 64 * 1024, 1024, 1024 * 1024),
            expose_docs=_env_bool("WEB_AGENT_EXPOSE_DOCS", False),
            model_provider=provider,  # type: ignore[arg-type]
            model_name=_env_str("MODEL_NAME", "google/gemini-2.0-flash-001"),
            openrouter_api_key=_env_str("OPENROUTER_API_KEY"),
            model_timeout_seconds=_env_float("WEB_AGENT_MODEL_TIMEOUT_SECONDS", 60.0, 1.0, 120.0),
            max_output_tokens=_env_int("WEB_AGENT_MAX_OUTPUT_TOKENS", 1024, 64, 8192),
            max_model_calls_per_task=_env_int("WEB_AGENT_MAX_MODEL_CALLS", 60, 1, 500),
            max_task_cost_usd=_env_decimal("WEB_AGENT_MAX_TASK_COST_USD", "1.00"),
            price_input_per_mtok_usd=_env_decimal("WEB_AGENT_PRICE_INPUT_PER_MTOK", "0.10"),
            price_output_per_mtok_usd=_env_decimal("WEB_AGENT_PRICE_OUTPUT_PER_MTOK", "0.40"),
            max_image_bytes=_env_int("WEB_AGENT_MAX_IMAGE_BYTES", 3_500_000, 100_000, 20_000_000),
            task_timeout_seconds=_env_float("TASK_TIMEOUT_SECONDS", HARD_TASK_TIMEOUT_SECONDS, 1.0, HARD_TASK_TIMEOUT_SECONDS),
            max_actions_per_task=_env_int("WEB_AGENT_MAX_ACTIONS", 40, 1, 200),
            max_recovery_attempts=_env_int("WEB_AGENT_MAX_RECOVERY_ATTEMPTS", 6, 0, 50),
            action_timeout_seconds=_env_float("WEB_AGENT_ACTION_TIMEOUT_SECONDS", 15.0, 1.0, 60.0),
            cleanup_timeout_seconds=_env_float("WEB_AGENT_CLEANUP_TIMEOUT_SECONDS", 5.0, 1.0, 30.0),
            observation_max_age_seconds=_env_float("WEB_AGENT_OBSERVATION_MAX_AGE_SECONDS", 15.0, 1.0, 120.0),
            typing_delay_ms=_env_int("WEB_AGENT_TYPING_DELAY_MS", 20, 0, 200),
            max_concurrent_tasks=_env_int("MAX_CONCURRENT_TASKS", 2, 1, 16),
            worker_mode=worker_mode,  # type: ignore[arg-type]
            headless=_env_bool("WEB_AGENT_HEADLESS", True),
            chromium_sandbox=_env_bool("WEB_AGENT_CHROMIUM_SANDBOX", True),
            viewport_width=_env_int("WEB_AGENT_VIEWPORT_WIDTH", 1280, 320, 3840),
            viewport_height=_env_int("WEB_AGENT_VIEWPORT_HEIGHT", 800, 240, 2160),
            allow_private_desktop=_env_bool("WEB_AGENT_ALLOW_PRIVATE_DESKTOP", True),
            allow_loopback_desktop=_env_bool("WEB_AGENT_ALLOW_LOOPBACK_DESKTOP", False),
            audit_dir=_env_str("WEB_AGENT_AUDIT_DIR", "./data/audit"),
            persist_screenshots=_env_bool("WEB_AGENT_PERSIST_SCREENSHOTS", False),
            evidence_dir=_env_str("WEB_AGENT_EVIDENCE_DIR", "./data/evidence"),
        )

    def to_child_dict(self) -> dict:
        """Serialización para el worker en subproceso (incluye secretos de proceso)."""
        data = asdict(self)
        data["max_task_cost_usd"] = str(self.max_task_cost_usd)
        data["price_input_per_mtok_usd"] = str(self.price_input_per_mtok_usd)
        data["price_output_per_mtok_usd"] = str(self.price_output_per_mtok_usd)
        return data

    @classmethod
    def from_child_dict(cls, data: dict) -> "Settings":
        data = dict(data)
        for key in ("max_task_cost_usd", "price_input_per_mtok_usd", "price_output_per_mtok_usd"):
            data[key] = Decimal(data[key])
        return cls(**data)
