from typing import Optional
from pydantic import ValidationError

from web_agent.core.schemas import ProposedAction, ActionType, TaskProfile, RiskLevel
from web_agent.core.errors import PolicyViolationError
from web_agent.security.network_policy import NetworkPolicyValidator


class SecurityPolicyEnforcer:
    def __init__(self, profile: TaskProfile):
        self.profile = profile
        self.network_validator = NetworkPolicyValidator(profile.security)

    def validate_action(self, action: ProposedAction) -> bool:
        if not isinstance(action, ProposedAction):
            raise PolicyViolationError("Contrato de acción inválido.")

        # Revalidar campos, incluso si la instancia llegó desde un adaptador o model_construct.
        try:
            action = ProposedAction.model_validate(action.model_dump())
        except ValidationError:
            raise PolicyViolationError(
                "La propuesta no respeta el contrato permitido."
            ) from None

        t = action.action_type
        if t == ActionType.NAVIGATE:
            if not self.profile.permissions.navigation:
                raise PolicyViolationError(
                    "Navegación deshabilitada en este perfil."
                )
            if not self.network_validator.is_url_allowed(action.url):
                # No incluir la URL: puede contener información sensible.
                raise PolicyViolationError("Seguridad de Red: URL no permitida.")
            return True

        if t in (ActionType.DONE, ActionType.ASK_HUMAN):
            return True

        if t == ActionType.FILL:
            if not self.profile.permissions.fill_forms:
                raise PolicyViolationError(
                    "Acción bloqueada: Permisos insuficientes para rellenar formularios."
                )
            if self.profile.controls:
                if not any(c.operation == ActionType.FILL for c in self.profile.controls):
                    raise PolicyViolationError("Acción bloqueada: Control de relleno no autorizado en el perfil.")
            return True

        if t == ActionType.SUBMIT:
            if not self.profile.permissions.submit_forms:
                raise PolicyViolationError(
                    "Acción bloqueada: Permisos insuficientes para enviar formularios."
                )
            if RiskLevel.R2 in self.profile.security.require_human_approval_for:
                raise PolicyViolationError("Acción bloqueada: El envío de formularios requiere aprobación humana (R2).")
            return True

        if t == ActionType.CLICK:
            if not self.profile.controls:
                raise PolicyViolationError("Acción bloqueada: Clic en elemento no autorizado en el perfil.")
            return True

        if t == ActionType.EXTRACT:
            if not self.profile.permissions.read_visible_data:
                raise PolicyViolationError(
                    "Acción bloqueada: Permisos insuficientes para lectura de datos."
                )
            if not self.profile.controls:
                raise PolicyViolationError("Acción bloqueada: Extracción no autorizada en el perfil.")
            return True

        raise PolicyViolationError("Acción bloqueada: Operación no permitida.")

