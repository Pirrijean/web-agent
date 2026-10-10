"""Política de autorización determinista (documento maestro §8.9, §11.5).

Convierte una propuesta ya validada en ``allow``, ``deny`` o ``require_human``.

- El riesgo depende del efecto, no del periférico: un clic puede ser R0 o R3.
- La descripción y la clase de efecto que declara el modelo son entrada no
  confiable: solo pueden **elevar** el riesgo, nunca reducirlo.
- R3 siempre requiere aprobación humana. El contrato de evaluación no ofrece
  canal humano, así que la tarea se detiene de forma segura.
- Fallo cerrado: sin permiso explícito no hay ejecución.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import ValidationError

from web_agent.core.errors import PolicyViolationError
from web_agent.core.schemas import (
    RISK_ORDER,
    SPATIAL_ACTIONS,
    ActionType,
    ProposedAction,
    RiskLevel,
    TaskProfile,
)
from web_agent.security.network_policy import NetworkPolicyValidator
from web_agent.security.origins import OriginError, parse_url_origin


class DecisionOutcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HUMAN = "require_human"


@dataclass(frozen=True)
class Decision:
    outcome: DecisionOutcome
    risk: RiskLevel
    reason_code: str
    message: str

    @property
    def allowed(self) -> bool:
        return self.outcome == DecisionOutcome.ALLOW


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# Palabras que indican efectos destructivos/irreversibles (R3).
_DESTRUCTIVE = re.compile(
    r"\b(eliminar|elimina|borrar|borra|suprimir|delete|remove|erase|purge|drop|truncate|"
    r"formatear|format|wipe|destruir|destroy|pagar|pago|pay|payment|transferir|transfer|"
    r"publicar|publish|anular factura|void|desinstalar|uninstall|reset de fabrica|factory reset|"
    r"vaciar papelera|empty trash|dar de baja|baja definitiva)\b"
)
# Palabras que indican persistencia (R2).
_PERSIST = re.compile(
    r"\b(guardar|guarda|save|grabar|enviar|envia|send|submit|aceptar|accept|confirmar|confirm|"
    r"aplicar|apply|registrar|register|crear|create|alta|insertar|insert|actualizar|update|"
    r"modificar|modify|exportar|export|generar|generate|imprimir|print|procesar|process|"
    r"finalizar|finish|cerrar sesion|logout|ok)\b"
)
_PERSISTENT_KEYS = frozenset({"Enter", "Control+s", "Control+p", "Alt+F4", "Delete", "F12", "Control+Enter"})
_EDIT_KEYS_PREFIX = ("Backspace", "Control+a", "Control+x", "Control+v", "Control+z", "Space")


def _max_risk(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=lambda r: RISK_ORDER[r])


class SecurityPolicyEnforcer:
    def __init__(self, profile: TaskProfile):
        self.profile = profile
        self.network_validator = NetworkPolicyValidator(profile.security)

    # -------------------------------------------------------------- riesgo
    def classify_risk(self, action: ProposedAction, element=None) -> RiskLevel:
        t = action.action
        base = RiskLevel.R0
        if t in (ActionType.TYPE_TEXT, ActionType.FILL_DOM):
            base = RiskLevel.R1
        elif t == ActionType.PRESS_KEY:
            key = action.key or ""
            if key in _PERSISTENT_KEYS or key.startswith("F"):
                base = RiskLevel.R2
            elif key.startswith(_EDIT_KEYS_PREFIX):
                base = RiskLevel.R1
        elif t == ActionType.CLICK_DOM and element is not None and element.form_submit:
            base = RiskLevel.R2

        declared = {
            "read": RiskLevel.R0,
            "navigate": RiskLevel.R0,
            "focus": RiskLevel.R0,
            "edit": RiskLevel.R1,
            "persist": RiskLevel.R2,
            "destructive": RiskLevel.R3,
        }.get(action.effect_class or "", RiskLevel.R0)

        text_parts = [action.target_description or "", action.expected_effect or ""]
        if element is not None:
            text_parts.append(element.name)
        text = _fold(" ".join(text_parts))
        lexical = RiskLevel.R0
        if t in SPATIAL_ACTIONS or t in (ActionType.CLICK_DOM, ActionType.PRESS_KEY):
            if _DESTRUCTIVE.search(text):
                lexical = RiskLevel.R3
            elif _PERSIST.search(text):
                lexical = RiskLevel.R2
        elif _DESTRUCTIVE.search(text) and t in (ActionType.TYPE_TEXT, ActionType.FILL_DOM):
            lexical = RiskLevel.R1  # escribir texto no ejecuta la operación
        return _max_risk(base, declared, lexical)

    # ------------------------------------------------------------ decisión
    def _deny(self, risk, code, msg) -> Decision:
        return Decision(DecisionOutcome.DENY, risk, code, msg)

    def decide(self, validated, ctx=None) -> Decision:
        """Decisión contextual sobre una ``ValidatedAction``."""
        action: ProposedAction = validated.action
        perms = self.profile.permissions
        t = action.action
        element = getattr(validated, "element", None)
        risk = self.classify_risk(action, element)

        if t in (ActionType.DONE, ActionType.ASK_HUMAN, ActionType.WAIT):
            return Decision(DecisionOutcome.ALLOW, RiskLevel.R0, "control_flow", "Control de flujo.")

        if t == ActionType.NAVIGATE:
            if not perms.navigation:
                return self._deny(risk, "navigation_disabled", "Navegación deshabilitada en este perfil.")
            if not self.network_validator.is_url_allowed(action.url):
                return self._deny(risk, "url_not_allowed", "Seguridad de Red: URL no permitida.")
            return Decision(DecisionOutcome.ALLOW, risk, "navigation_allowed", "Destino autorizado.")

        if not perms.read_visible_data:
            return self._deny(risk, "read_disabled", "Lectura de la interfaz deshabilitada.")
        if t in SPATIAL_ACTIONS or t == ActionType.SCROLL or (t == ActionType.TYPE_TEXT and action.x is not None):
            if not perms.pointer_input:
                return self._deny(risk, "pointer_disabled", "Entrada de ratón deshabilitada.")
        if t in (ActionType.TYPE_TEXT, ActionType.PRESS_KEY) and not perms.keyboard_input:
            return self._deny(risk, "keyboard_disabled", "Entrada de teclado deshabilitada.")

        # Los secretos solo se introducen en el escritorio/página autorizados.
        if action.secret_ref is not None:
            page_url = getattr(validated, "page_url", "")
            if not self.network_validator.is_url_allowed(page_url):
                return self._deny(risk, "secret_outside_scope", "Secreto fuera del destino autorizado.")
            risk = _max_risk(risk, RiskLevel.R1)

        # DOM: el control concreto debe estar autorizado (§8.9, P1).
        if t in (ActionType.CLICK_DOM, ActionType.FILL_DOM, ActionType.EXTRACT):
            control_decision = self._authorize_dom_control(validated, risk)
            if control_decision is not None:
                return control_decision

        if risk == RiskLevel.R3 or risk in self.profile.security.require_human_approval_for:
            return Decision(
                DecisionOutcome.REQUIRE_HUMAN,
                risk,
                "human_approval_required",
                f"La operación ({risk.value}) requiere aprobación humana.",
            )
        if risk == RiskLevel.R2 and not perms.submit_forms:
            return self._deny(risk, "persist_not_permitted", "Operaciones persistentes no autorizadas.")
        if risk == RiskLevel.R1 and not perms.fill_forms:
            return self._deny(risk, "edit_not_permitted", "Acción bloqueada: edición no autorizada.")
        return Decision(DecisionOutcome.ALLOW, risk, "within_scope", "Dentro del ámbito de la tarea.")

    def _authorize_dom_control(self, validated, risk: RiskLevel) -> Optional[Decision]:
        el = validated.element
        t = validated.action.action
        if t == ActionType.EXTRACT and not self.profile.controls:
            return self._deny(risk, "extract_not_authorized", "Acción bloqueada: extracción no autorizada.")
        if not self.profile.controls:
            return self._deny(risk, "control_not_authorized", "Acción bloqueada: control no autorizado en el perfil.")
        if el is None:
            return self._deny(risk, "control_unknown", "Acción bloqueada: control desconocido.")
        try:
            page_origin = parse_url_origin(validated.page_url)
        except OriginError:
            return self._deny(risk, "page_unknown", "Página fuera del ámbito.")
        page_path = validated.page_url.split("#", 1)[0].split("?", 1)[0]
        for control in self.profile.controls:
            if control.operation != t or control.role != el.role or control.name != el.name:
                continue
            c_url = control.page_url.split("#", 1)[0].split("?", 1)[0]
            if parse_url_origin(control.page_url) == page_origin and c_url.rstrip("/") == page_path.rstrip("/"):
                return None  # autorizado; el riesgo se evalúa después
        return self._deny(risk, "control_not_authorized", "Acción bloqueada: control no autorizado en esta página.")

    # ------------------------------------------- compatibilidad estructural
    def validate_action(self, action: ProposedAction) -> bool:
        """Comprobación estructural + permisos sin contexto (fail-closed)."""
        if not isinstance(action, ProposedAction):
            raise PolicyViolationError("Contrato de acción inválido.")
        try:
            action = ProposedAction.model_validate(action.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise PolicyViolationError("La propuesta no respeta el contrato permitido.") from None

        from web_agent.core.action_validator import ValidatedAction

        decision = self.decide(ValidatedAction(action=action))
        if decision.outcome == DecisionOutcome.DENY:
            raise PolicyViolationError(decision.message)
        if decision.outcome == DecisionOutcome.REQUIRE_HUMAN:
            raise PolicyViolationError(decision.message)
        return True
