from pydantic import ValidationError

from web_agent.core.schemas import ProposedAction, ActionType, TaskProfile
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

        if action.action_type == ActionType.NAVIGATE:
            if not self.profile.permissions.navigation:
                raise PolicyViolationError(
                    "Navegación deshabilitada en este perfil."
                )
            if not self.network_validator.is_url_allowed(action.url):
                # No incluir la URL: puede contener información sensible.
                raise PolicyViolationError("Seguridad de Red: URL no permitida.")
            return True

        if action.action_type in (ActionType.DONE, ActionType.ASK_HUMAN):
            return True

        raise PolicyViolationError(
            "Acción bloqueada hasta implementar observación, "
            "autorización contextual y verificación independiente."
        )

