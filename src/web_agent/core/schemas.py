"""Contratos tipados del agente (documento maestro §8.8, §10).

Todo lo que entra al núcleo (perfiles, propuestas del modelo) se valida aquí:

- ``extra="forbid"``: un campo desconocido (errata o campo inyectado por el
  modelo) es un error, no se ignora.
- ``frozen=True``: un perfil o una propuesta no pueden modificarse tras validarse.
- Límites duros de plataforma que ningún perfil puede superar (300 s por tarea).
- La validación estructural NO autoriza: la validación contextual y la política
  viven en ``action_validator`` y ``policy``.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from web_agent.core.observation import ELEMENT_ID_PATTERN, OBSERVATION_ID_PATTERN
from web_agent.core.result import RunResult, TaskState  # noqa: F401  (re-export)
from web_agent.security.origins import (
    OriginError,
    parse_allowed_origin,
    parse_desktop_origin,
    parse_url_origin,
)

# ---------------------------------------------------------------------------
# Límites duros de plataforma
# ---------------------------------------------------------------------------
HARD_MAX_STEPS = 200
HARD_MAX_RETRIES = 5
HARD_MAX_TASK_TIMEOUT_S = 300
HARD_MAX_ACTION_TIMEOUT_MS = 60_000
HARD_MAX_MODEL_TIMEOUT_S = 120
MAX_TEXT_VALUE = 2_000
MAX_URL_LENGTH = 2_048
MAX_PROFILE_BYTES = 64 * 1024
MAX_OBJECTIVE_CHARS = 4_000
MAX_SCROLL_DELTA = 2_000
MAX_WAIT_SECONDS = 10.0

SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
SECRET_NAME_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"
DESKTOP_PASSWORD_SECRET = "DESKTOP_PASSWORD"


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=False,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


# ---------------------------------------------------------------------------
# Riesgo y acciones
# ---------------------------------------------------------------------------
class RiskLevel(str, Enum):
    R0 = "R0"  # Navegar, leer, mover foco
    R1 = "R1"  # Editar campos/borradores recuperables
    R2 = "R2"  # Guardar/modificar datos persistentes
    R3 = "R3"  # Eliminar, pagar, publicar o irreversible (requiere humano)


RISK_ORDER = {RiskLevel.R0: 0, RiskLevel.R1: 1, RiskLevel.R2: 2, RiskLevel.R3: 3}


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK_DOM = "click_dom"
    FILL_DOM = "fill_dom"
    CLICK_XY = "click_xy"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    SCROLL = "scroll"
    WAIT = "wait"
    EXTRACT = "extract"
    DONE = "done"
    ASK_HUMAN = "ask_human"


SPATIAL_ACTIONS = frozenset({ActionType.CLICK_XY, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK})
VISUAL_ACTIONS = SPATIAL_ACTIONS | {ActionType.TYPE_TEXT, ActionType.PRESS_KEY, ActionType.SCROLL}
DOM_ACTIONS = frozenset({ActionType.CLICK_DOM, ActionType.FILL_DOM, ActionType.EXTRACT})
CONTROL_ACTIONS = frozenset({ActionType.DONE, ActionType.ASK_HUMAN, ActionType.WAIT, ActionType.NAVIGATE})
# Acciones que dependen de la pantalla y deben citar la observación vigente.
OBSERVATION_BOUND_ACTIONS = VISUAL_ACTIONS | DOM_ACTIONS
STATE_CHANGING_ACTIONS = (
    VISUAL_ACTIONS | {ActionType.NAVIGATE, ActionType.CLICK_DOM, ActionType.FILL_DOM}
)

EffectClass = Literal["read", "navigate", "focus", "edit", "persist", "destructive"]
StepAssessment = Literal["effect_observed", "no_effect", "unclear", "not_applicable"]

# Campos genéricos admitidos en cualquier acción (no ejecutables).
_GENERIC_FIELDS = frozenset(
    {
        "action",
        "observation_id",
        "reasoning",
        "reason_code",
        "target_description",
        "expected_effect",
        "effect_class",
        "previous_step_assessment",
        "screen_summary",
    }
)
_ACTION_FIELDS: dict[ActionType, tuple[frozenset[str], frozenset[str]]] = {
    # acción: (requeridos, opcionales)
    ActionType.NAVIGATE: (frozenset({"url"}), frozenset()),
    ActionType.CLICK_DOM: (frozenset({"target_element_id"}), frozenset()),
    ActionType.FILL_DOM: (frozenset({"target_element_id"}), frozenset({"text", "secret_ref", "clear_before"})),
    ActionType.CLICK_XY: (frozenset({"x", "y"}), frozenset()),
    ActionType.DOUBLE_CLICK: (frozenset({"x", "y"}), frozenset()),
    ActionType.RIGHT_CLICK: (frozenset({"x", "y"}), frozenset()),
    ActionType.TYPE_TEXT: (frozenset(), frozenset({"x", "y", "text", "secret_ref", "clear_before"})),
    ActionType.PRESS_KEY: (frozenset({"key"}), frozenset()),
    ActionType.SCROLL: (frozenset(), frozenset({"x", "y", "delta_x", "delta_y"})),
    ActionType.WAIT: (frozenset({"duration_seconds"}), frozenset()),
    ActionType.EXTRACT: (frozenset({"target_element_id"}), frozenset()),
    ActionType.DONE: (frozenset(), frozenset({"claimed_evidence", "answer"})),
    ActionType.ASK_HUMAN: (frozenset(), frozenset()),
}

# --- Catálogo cerrado de teclas (§10.2 press_key) ---
_MODIFIER_ALIASES = {
    "control": "Control",
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
}
_BASE_KEYS = {
    k.lower(): k
    for k in (
        ["Enter", "Tab", "Escape", "Backspace", "Delete", "Space", "Insert", "Home", "End",
         "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]
        + [f"F{i}" for i in range(1, 13)]
    )
}
_BASE_KEYS.update({"esc": "Escape", "return": "Enter", "del": "Delete", "up": "ArrowUp",
                   "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight", " ": "Space"})
# Combinaciones prohibidas siempre (efectos de sistema fuera del alcance de la tarea).
FORBIDDEN_KEY_COMBOS = frozenset({"Control+Alt+Delete", "Control+Shift+Escape", "Alt+Tab", "Control+Alt+Backspace"})


def normalize_key(raw: str) -> str:
    """Normaliza una tecla/combinación del catálogo cerrado o lanza ``ValueError``."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 40:
        raise ValueError("Tecla vacía o demasiado larga.")
    parts = [p.strip() for p in raw.strip().split("+")]
    if any(p == "" for p in parts) or len(parts) > 4:
        raise ValueError("Combinación de teclas inválida.")
    *mods, base = parts
    norm_mods: list[str] = []
    for m in mods:
        nm = _MODIFIER_ALIASES.get(m.lower())
        if nm is None or nm in norm_mods:
            raise ValueError(f"Modificador no permitido: {m}")
        norm_mods.append(nm)
    norm_mods.sort(key=["Control", "Alt", "Shift"].index)
    if base.lower() in _BASE_KEYS:
        norm_base = _BASE_KEYS[base.lower()]
    elif re.fullmatch(r"[A-Za-z0-9]", base):
        norm_base = base.lower() if norm_mods else base
    else:
        raise ValueError(f"Tecla no permitida: {base}")
    combo = "+".join(norm_mods + [norm_base])
    if combo in FORBIDDEN_KEY_COMBOS or "+".join(norm_mods + [norm_base.title()]) in FORBIDDEN_KEY_COMBOS:
        raise ValueError("Combinación de sistema prohibida.")
    return combo


class ProposedAction(_Strict):
    """Propuesta de un único paso emitida por el modelo (§10.1). Entrada NO CONFIABLE."""

    action: ActionType = Field(validation_alias=AliasChoices("action", "action_type"))
    observation_id: Optional[str] = Field(None, pattern=OBSERVATION_ID_PATTERN)
    reasoning: str = Field(..., min_length=1, max_length=1_000)
    reason_code: Optional[str] = Field(None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    target_description: Optional[str] = Field(None, max_length=200)
    expected_effect: Optional[str] = Field(None, max_length=300)
    effect_class: Optional[EffectClass] = None
    previous_step_assessment: Optional[StepAssessment] = None
    screen_summary: Optional[str] = Field(None, max_length=300)

    # Parámetros por acción
    url: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)
    target_element_id: Optional[str] = Field(None, pattern=ELEMENT_ID_PATTERN)
    x: Optional[int] = Field(None, ge=0, le=1000)
    y: Optional[int] = Field(None, ge=0, le=1000)
    text: Optional[str] = Field(None, min_length=1, max_length=MAX_TEXT_VALUE)
    secret_ref: Optional[str] = Field(None, pattern=SECRET_NAME_PATTERN)
    clear_before: Optional[bool] = None
    key: Optional[str] = Field(None, max_length=40)
    delta_x: Optional[int] = Field(None, ge=-MAX_SCROLL_DELTA, le=MAX_SCROLL_DELTA)
    delta_y: Optional[int] = Field(None, ge=-MAX_SCROLL_DELTA, le=MAX_SCROLL_DELTA)
    duration_seconds: Optional[float] = Field(None, ge=0.1, le=MAX_WAIT_SECONDS)
    claimed_evidence: Optional[str] = Field(None, max_length=500)
    # Solo en ``done`` de una pregunta: el valor pedido (número primero; varios datos con "; ").
    answer: Optional[str] = Field(None, min_length=1, max_length=300)

    @field_validator(
        "reasoning", "text", "target_description", "expected_effect", "screen_summary", "claimed_evidence",
        "answer",
    )
    @classmethod
    def _no_nul(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and "\x00" in v:
            raise ValueError("no se admiten bytes NUL")
        return v

    @field_validator("key")
    @classmethod
    def _key_catalogue(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else normalize_key(v)

    @model_validator(mode="after")
    def _check_shape(self) -> "ProposedAction":
        t = self.action
        required, optional = _ACTION_FIELDS[t]
        present = {
            name
            for name in type(self).model_fields
            if name not in _GENERIC_FIELDS and getattr(self, name) is not None
        }
        missing = required - present
        if missing:
            raise ValueError(f"{t.value} requiere: {', '.join(sorted(missing))}")
        extra = present - required - optional
        if extra:
            raise ValueError(f"{t.value} no admite: {', '.join(sorted(extra))}")

        if t in OBSERVATION_BOUND_ACTIONS and self.observation_id is None:
            raise ValueError(f"{t.value} requiere el observation_id de la captura analizada")
        if (self.x is None) != (self.y is None):
            raise ValueError("x e y deben indicarse juntas")
        if t in (ActionType.TYPE_TEXT, ActionType.FILL_DOM):
            if (self.text is None) == (self.secret_ref is None):
                raise ValueError(f"{t.value} requiere exactamente uno de 'text' o 'secret_ref'")
        if t == ActionType.SCROLL and not (self.delta_x or self.delta_y):
            raise ValueError("scroll requiere un desplazamiento no nulo")
        return self


# ---------------------------------------------------------------------------
# Perfil de tarea
# ---------------------------------------------------------------------------
class BrowserConfig(_Strict):
    engine: Literal["chromium"] = "chromium"
    headless: bool = Field(True, strict=True)
    chromium_sandbox: bool = Field(True, strict=True)


class PermissionsConfig(_Strict):
    # Mínimo privilegio: solo lectura por defecto.
    navigation: bool = Field(True, strict=True)
    read_visible_data: bool = Field(True, strict=True)
    pointer_input: bool = Field(True, strict=True)
    keyboard_input: bool = Field(True, strict=True)
    fill_forms: bool = Field(False, strict=True)
    submit_forms: bool = Field(False, strict=True)
    delete_records: bool = Field(False, strict=True)


class EgressProxyConfig(_Strict):
    """Frontera de red de infraestructura (defensa adicional recomendada en despliegue)."""

    server: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH)

    @field_validator("server")
    @classmethod
    def validate_server(cls, server: str) -> str:
        parts = urlsplit(server)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Proxy de salida inválido; se requiere un origen HTTP(S) sin credenciales.")
        return server.rstrip("/")


class SecurityProfile(_Strict):
    allowed_origins: tuple[str, ...] = Field((), max_length=50)
    # Excepción explícita y estrecha para el escritorio suministrado (§11.4).
    desktop_origin: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)
    allow_private_desktop: bool = Field(False, strict=True)
    allow_loopback_desktop: bool = Field(False, strict=True)
    websocket_path_prefixes: tuple[str, ...] = Field((), max_length=10)
    allow_downloads: Literal[False] = False
    allow_arbitrary_code: Literal[False] = False
    egress_proxy: Optional[EgressProxyConfig] = None
    require_human_approval_for: frozenset[RiskLevel] = frozenset({RiskLevel.R2, RiskLevel.R3})
    allowed_secret_names: frozenset[str] = frozenset()

    @field_validator("allowed_origins")
    @classmethod
    def _validate_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        normalized = []
        for origin in origins:
            try:
                normalized.append(parse_allowed_origin(origin).serialize())
            except OriginError as exc:
                raise ValueError(f"origen no válido '{origin}': {exc}") from exc
        return tuple(dict.fromkeys(normalized))

    @field_validator("websocket_path_prefixes")
    @classmethod
    def _validate_ws_paths(cls, prefixes: tuple[str, ...]) -> tuple[str, ...]:
        for p in prefixes:
            if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,200}", p) or ".." in p:
                raise ValueError(f"prefijo de ruta WebSocket inválido: {p}")
        return prefixes

    @field_validator("require_human_approval_for")
    @classmethod
    def _r3_always_requires_human(cls, levels: frozenset[RiskLevel]) -> frozenset[RiskLevel]:
        if RiskLevel.R3 not in levels:
            raise ValueError("R3 (acciones destructivas/irreversibles) siempre requiere aprobación humana")
        return levels

    @field_validator("allowed_secret_names")
    @classmethod
    def _secret_names(cls, names: frozenset[str]) -> frozenset[str]:
        for name in names:
            if not re.fullmatch(SECRET_NAME_PATTERN, name):
                raise ValueError(f"nombre de secreto inválido: {name}")
        return names

    @model_validator(mode="after")
    def _origins_and_desktop(self) -> "SecurityProfile":
        if self.desktop_origin is not None:
            try:
                origin = parse_desktop_origin(
                    self.desktop_origin,
                    allow_private=self.allow_private_desktop,
                    allow_loopback=self.allow_loopback_desktop,
                )
            except OriginError as exc:
                raise ValueError(f"origen de escritorio no válido: {exc}") from exc
            object.__setattr__(self, "desktop_origin", origin.serialize())
        if not self.allowed_origins and self.desktop_origin is None:
            raise ValueError("Se requiere al menos un origen permitido o un origen de escritorio.")
        return self


class ExecutionConfig(_Strict):
    max_steps: int = Field(40, ge=1, le=HARD_MAX_STEPS, strict=True)
    max_retries_per_action: int = Field(2, ge=0, le=HARD_MAX_RETRIES, strict=True)
    task_timeout_seconds: int = Field(300, ge=1, le=HARD_MAX_TASK_TIMEOUT_S, strict=True)
    action_timeout_ms: int = Field(15_000, ge=100, le=HARD_MAX_ACTION_TIMEOUT_MS, strict=True)
    model_timeout_seconds: int = Field(60, ge=1, le=HARD_MAX_MODEL_TIMEOUT_S, strict=True)


class SurfaceRegion(_Strict):
    """Superficie autorizada en píxeles CSS (p. ej. el área del escritorio remoto)."""

    x: int = Field(0, ge=0)
    y: int = Field(0, ge=0)
    width: int = Field(..., ge=1)
    height: int = Field(..., ge=1)

    def contains(self, x_css: int, y_css: int) -> bool:
        return self.x <= x_css < self.x + self.width and self.y <= y_css < self.y + self.height


class PerceptionConfig(_Strict):
    mode: Literal["visual_only", "dom", "auto"] = "visual_only"
    observation_max_age_seconds: float = Field(15.0, gt=0, le=120)
    authorized_surface: Optional[SurfaceRegion] = None


class SuccessCondition(_Strict):
    """Condición comprobada por el sistema (no por el modelo) para declarar éxito en modo DOM."""

    url_equals: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)
    visible_text: Optional[str] = Field(None, min_length=1, max_length=500)
    element_role: Optional[str] = Field(None, pattern=r"^[a-z]{2,32}$")
    element_name: Optional[str] = Field(None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def _at_least_one(self) -> "SuccessCondition":
        if not any([self.url_equals, self.visible_text, self.element_role]):
            raise ValueError("la condición de éxito debe definir al menos un criterio comprobable")
        if (self.element_role is None) != (self.element_name is None):
            raise ValueError("element_role y element_name deben definirse juntos")
        return self


class AuthorizedControl(_Strict):
    """Control DOM autorizado por el operador, ligado a una página y operación exactas."""

    page_url: str = Field(..., max_length=MAX_URL_LENGTH)
    role: str = Field(..., pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    name: str = Field(..., min_length=1, max_length=300)
    operation: Literal[ActionType.CLICK_DOM, ActionType.FILL_DOM, ActionType.EXTRACT]
    risk: Literal[RiskLevel.R0, RiskLevel.R1, RiskLevel.R2] = RiskLevel.R0
    input_key: Optional[str] = Field(None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    after: Optional[SuccessCondition] = None

    @field_validator("page_url")
    @classmethod
    def valid_page_url(cls, value: str) -> str:
        parse_url_origin(value)
        return value

    @model_validator(mode="after")
    def safe_control_shape(self) -> "AuthorizedControl":
        if self.operation == ActionType.FILL_DOM:
            if self.after is not None:
                raise ValueError("fill_dom no admite condición post-acción.")
        elif self.operation == ActionType.CLICK_DOM:
            if self.input_key is not None:
                raise ValueError("click_dom no admite input_key.")
        elif self.input_key is not None or self.after is not None:
            raise ValueError("extract no admite input_key ni condición post-acción.")
        return self


class ProfileInput(_Strict):
    key: str = Field(..., pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    value: str = Field(..., min_length=1, max_length=MAX_TEXT_VALUE)


class VerificationConfig(_Strict):
    require_post_action_check: Literal[True] = True
    fail_on_ambiguous_target: Literal[True] = True
    success_condition: Optional[SuccessCondition] = None


class AuditConfig(_Strict):
    record_action_metadata: bool = True
    redact_sensitive_values: Literal[True] = True


class TaskProfile(_Strict):
    """Perfil inmutable de cada tarea."""

    id: str = Field(..., pattern=SAFE_ID_PATTERN)
    version: str = Field("1.0", pattern=r"^\d+(\.\d+){0,2}$")
    browser: BrowserConfig = BrowserConfig()
    security: SecurityProfile
    permissions: PermissionsConfig = PermissionsConfig()
    perception: PerceptionConfig = PerceptionConfig()
    controls: tuple[AuthorizedControl, ...] = ()
    inputs: tuple[ProfileInput, ...] = ()
    execution: ExecutionConfig = ExecutionConfig()
    verification: VerificationConfig = VerificationConfig()
    audit: AuditConfig = AuditConfig()

    @model_validator(mode="after")
    def validate_catalogue(self) -> "TaskProfile":
        allowed = {parse_allowed_origin(origin) for origin in self.security.allowed_origins}
        if self.security.desktop_origin:
            allowed.add(parse_url_origin(self.security.desktop_origin))
        input_keys = {item.key for item in self.inputs}
        if len(input_keys) != len(self.inputs):
            raise ValueError("No se admiten claves de entrada duplicadas.")
        seen_controls = set()
        for control in self.controls:
            if parse_url_origin(control.page_url) not in allowed:
                raise ValueError("Un control autorizado debe pertenecer a un origen permitido.")
            key = (control.page_url, control.role, control.name, control.operation.value)
            if key in seen_controls:
                raise ValueError("No se admiten controles autorizados duplicados.")
            seen_controls.add(key)
            if control.input_key is not None and control.input_key not in input_keys:
                raise ValueError("El control fill_dom referencia una entrada inexistente.")
        return self


class RunRequest(_Strict):
    objective: str = Field(..., strict=True, min_length=1, max_length=MAX_OBJECTIVE_CHARS)

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Objetivo vacío o inválido.")
        return value.strip()


def load_profile(path: str | Path) -> TaskProfile:
    """Carga y valida un perfil YAML (``yaml.safe_load``, tamaño limitado, raíz ``profile``)."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Perfil no encontrado: {p}")
    with p.open("rb") as handle:
        data = handle.read(MAX_PROFILE_BYTES + 1)
    if len(data) > MAX_PROFILE_BYTES:
        raise ValueError("perfil demasiado grande")
    doc = yaml.safe_load(data.decode("utf-8"))
    if not isinstance(doc, dict) or set(doc.keys()) != {"profile"}:
        raise ValueError("el perfil debe tener una única clave raíz 'profile'")
    return TaskProfile.model_validate(doc["profile"])


def build_desktop_profile(
    *,
    task_id: str,
    desktop_url: str,
    allow_private_desktop: bool,
    allow_loopback_desktop: bool,
    max_steps: int,
    headless: bool = True,
    chromium_sandbox: bool = True,
    observation_max_age_seconds: float = 15.0,
) -> TaskProfile:
    """Perfil de ámbito de tarea para el contrato REST (modo ``visual_only``).

    Ámbito = escritorio autorizado × clases de operación (R0–R2) × límites.
    R3 siempre requiere humano; como no existe canal, queda bloqueado (§8.9).
    """
    origin = parse_desktop_origin(
        desktop_url, allow_private=allow_private_desktop, allow_loopback=allow_loopback_desktop
    )
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:64] or "task"
    return TaskProfile(
        id=safe_id,
        browser=BrowserConfig(headless=headless, chromium_sandbox=chromium_sandbox),
        security=SecurityProfile(
            desktop_origin=origin.serialize(),
            allow_private_desktop=allow_private_desktop,
            allow_loopback_desktop=allow_loopback_desktop,
            require_human_approval_for=frozenset({RiskLevel.R3}),
            allowed_secret_names=frozenset({DESKTOP_PASSWORD_SECRET}),
        ),
        permissions=PermissionsConfig(
            navigation=True,
            read_visible_data=True,
            pointer_input=True,
            keyboard_input=True,
            fill_forms=True,
            submit_forms=True,
            delete_records=False,
        ),
        perception=PerceptionConfig(
            mode="visual_only", observation_max_age_seconds=observation_max_age_seconds
        ),
        execution=ExecutionConfig(max_steps=max_steps, task_timeout_seconds=HARD_MAX_TASK_TIMEOUT_S),
    )
