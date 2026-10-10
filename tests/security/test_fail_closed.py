import pytest

from web_agent.core.schemas import ProposedAction, TaskProfile
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.errors import PolicyViolationError


@pytest.mark.parametrize("action_type", ["click", "submit", "extract"])
def test_element_actions_are_fail_closed(action_type):
    profile = TaskProfile(
        id="test",
        security={"allowed_origins": ["https://example.com"]},
    )
    action = ProposedAction(
        action_type=action_type,
        target_element_id="e1",
        reasoning="Prueba de autorización",
    )

    with pytest.raises(PolicyViolationError):
        SecurityPolicyEnforcer(profile).validate_action(action)
