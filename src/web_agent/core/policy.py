from web_agent.core.schemas import ProposedAction, ActionType, TaskProfile, RiskLevel
from web_agent.security.network_policy import NetworkPolicyValidator
from web_agent.core.errors import PolicyViolationError

class SecurityPolicyEnforcer:
    """Muro de seguridad. Evalúa propuestas del LLM contra el Perfil de Tarea (Fase 2)."""
    
    def __init__(self, profile: TaskProfile):
        self.profile = profile
        self.network_validator = NetworkPolicyValidator(profile.security)

    def _map_action_to_risk(self, action: ProposedAction) -> RiskLevel:
        if action.action_type in [ActionType.NAVIGATE, ActionType.EXTRACT]:
            return RiskLevel.R0
        elif action.action_type in [ActionType.FILL, ActionType.CLICK]:
            return RiskLevel.R1
        elif action.action_type == ActionType.SUBMIT:
            return RiskLevel.R3
        return RiskLevel.R0

    def validate_action(self, action: ProposedAction) -> bool:
        # 1. Validación de permisos explícitos
        if action.action_type == ActionType.NAVIGATE and not self.profile.permissions.navigation:
            raise PolicyViolationError("Navegación deshabilitada en este perfil.")
        if action.action_type == ActionType.FILL and not self.profile.permissions.fill_forms:
            raise PolicyViolationError("Inyección de texto deshabilitada.")
        if action.action_type == ActionType.SUBMIT and not self.profile.permissions.submit_forms:
            raise PolicyViolationError("Envío de formularios bloqueado por seguridad.")

        # 2. Validación de Inyección de Red (SSRF / Phishing)
        if action.action_type == ActionType.NAVIGATE and action.url:
            if not self.network_validator.is_url_allowed(action.url):
                raise PolicyViolationError(f"Seguridad de Red: URL no permitida o inyectada ({action.url})")

        # 3. Validación de nivel de riesgo (Aprobación humana requerida)
        risk = self._map_action_to_risk(action)
        if risk in self.profile.security.require_human_approval_for:
            raise PolicyViolationError(f"Operación Bloqueada. Requiere aprobación humana (Nivel {risk.value}).")

        return True
