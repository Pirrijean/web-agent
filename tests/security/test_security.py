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

