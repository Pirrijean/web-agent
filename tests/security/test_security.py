import pytest
from pydantic import ValidationError
from web_agent.core.schemas import SecurityProfile, TaskProfile, ProposedAction, ActionType
from web_agent.security.origins import parse_url_origin, OriginError, is_private_or_local_host
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.errors import PolicyViolationError

def test_pydantic_forbids_extra_fields():
    """Prueba que los esquemas rechazan campos inyectados no definidos."""
    with pytest.raises(ValidationError) as exc:
        ProposedAction(
            action_type=ActionType.NAVIGATE,
            url="https://example.com",
            reasoning="Valid",
            malicious_field="hack"
        )
    assert "Extra inputs are not permitted" in str(exc.value) or "malicious_field" in str(exc.value)

def test_pydantic_cross_validation():
    """Prueba que no se pueden mezclar campos inválidos (e.g., NAVIGATE con target_element_id)."""
    with pytest.raises(ValidationError):
        ProposedAction(
            action_type=ActionType.NAVIGATE,
            url="https://example.com",
            target_element_id="e123", # Invalid for NAVIGATE
            reasoning="Valid"
        )

def test_origins_blocks_ssrf():
    """Verifica que el parser de orígenes bloquea IP locales, metadata y file://"""
    with pytest.raises(OriginError):
        parse_url_origin("file:///etc/passwd")
    
    with pytest.raises(OriginError):
        parse_url_origin("javascript:alert(1)")
        
    origin = parse_url_origin("http://169.254.169.254/latest/meta-data")
    assert is_private_or_local_host(origin.host) is True
    
    origin = parse_url_origin("http://localhost:8080")
    assert is_private_or_local_host(origin.host) is True

def test_security_policy_enforcer():
    """Prueba que el muro de políticas bloquea acciones no permitidas."""
    profile_data = {
        "id": "test",
        "version": "1.0",
        "security": {
            "allowed_origins": ["https://example.com"],
            "require_human_approval_for": ["R2", "R3"]
        },
        "permissions": {
            "navigation": True,
            "fill_forms": False,
            "submit_forms": False
        }
    }
    profile = TaskProfile(**profile_data)
    enforcer = SecurityPolicyEnforcer(profile)
    
    # Valid navigation
    action = ProposedAction(action_type=ActionType.NAVIGATE, url="https://example.com/path", reasoning="Test")
    assert enforcer.validate_action(action) is True
    
    # Blocked origin (SSRF)
    bad_action = ProposedAction(action_type=ActionType.NAVIGATE, url="https://metadata.google.internal", reasoning="SSRF")
    with pytest.raises(PolicyViolationError) as exc:
        enforcer.validate_action(bad_action)
    assert "Seguridad de Red" in str(exc.value)
    
    # Blocked action due to permissions / fail-closed
    fill_action = ProposedAction(action_type=ActionType.FILL, target_element_id="e1", value="test", reasoning="Fill")
    with pytest.raises(PolicyViolationError) as exc:
        enforcer.validate_action(fill_action)
    assert "Acción bloqueada" in str(exc.value) or "Inyección de texto" in str(exc.value)


@pytest.mark.anyio
async def test_network_policy_validator_dns_and_proxy():
    from web_agent.security.network_policy import NetworkPolicyValidator

    # Without proxy, require_request_allowed must fail
    profile = SecurityProfile(
        allowed_origins=["https://example.com"],
        require_human_approval_for=["R2", "R3"],
    )
    validator = NetworkPolicyValidator(profile)
    with pytest.raises(PolicyViolationError) as exc:
        await validator.require_request_allowed("https://example.com")
    assert "proxy de salida" in str(exc.value)

    # Allowed URL not in allowed_origins must return False
    assert await validator.is_request_allowed("https://google.com") is False

    # Private resolution / localhost must return False
    assert await validator.is_request_allowed("http://127.0.0.1:8000") is False


def test_enforcer_action_catalogue_validation():
    profile_data = {
        "id": "test_cat",
        "version": "1.0",
        "security": {
            "allowed_origins": ["https://example.com"],
            "require_human_approval_for": ["R2", "R3"],
        },
        "permissions": {
            "navigation": True,
            "fill_forms": True,
            "submit_forms": True,
        },
    }
    profile = TaskProfile(**profile_data)
    enforcer = SecurityPolicyEnforcer(profile)

    # Click without controls in profile fails closed
    click_action = ProposedAction(
        action_type=ActionType.CLICK,
        target_element_id="e1",
        reasoning="Click without control in profile",
    )
    with pytest.raises(PolicyViolationError) as exc:
        enforcer.validate_action(click_action)
    assert "Clic en elemento no autorizado" in str(exc.value)

    # Click with control in profile passes policy check
    profile_with_controls = TaskProfile(
        **{
            **profile_data,
            "controls": [
                {
                    "page_url": "https://example.com/app",
                    "role": "button",
                    "name": "Submit",
                    "operation": "click",
                    "after": {"url_equals": "https://example.com/home"},
                }
            ],
        }
    )
    enforcer_controls = SecurityPolicyEnforcer(profile_with_controls)
    assert enforcer_controls.validate_action(click_action) is True

    # Submit with R2 requiring human approval fails closed
    submit_action = ProposedAction(
        action_type=ActionType.SUBMIT,
        target_element_id="e1",
        reasoning="Submit form",
    )
    with pytest.raises(PolicyViolationError) as exc:
        enforcer.validate_action(submit_action)
    assert "aprobación humana" in str(exc.value)


