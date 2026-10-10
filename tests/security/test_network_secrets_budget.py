"""Red y WebSocket (§11.4), secretos (§11.3) y presupuesto (§8.13)."""

import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from web_agent.core.schemas import SecurityProfile, TaskProfile, build_desktop_profile
from web_agent.execution.budgets import BudgetExceededError, BudgetManager
from web_agent.security.network_policy import NetworkPolicyValidator
from web_agent.security.origins import OriginError, parse_desktop_origin, parse_url_origin
from web_agent.security.redaction import DataRedactor


def validator(url="https://desktop.example.com:6080/vnc.html", **kw):
    profile = build_desktop_profile(task_id="t", desktop_url=url, max_steps=10,
                                    allow_private_desktop=kw.get("private", False),
                                    allow_loopback_desktop=kw.get("loopback", False))
    return NetworkPolicyValidator(profile.security)


def test_websocket_only_to_desktop_origin():
    v = validator()
    assert v.is_websocket_allowed("wss://desktop.example.com:6080/websockify")
    assert not v.is_websocket_allowed("ws://desktop.example.com:6080/websockify")  # cambio de esquema
    assert not v.is_websocket_allowed("wss://desktop.example.com:7000/websockify")  # otro puerto
    assert not v.is_websocket_allowed("wss://tracker.example.org/ws")
    assert not v.is_websocket_allowed("https://desktop.example.com:6080/")  # no es WS


def test_websocket_path_prefix_restriction():
    sec = SecurityProfile(desktop_origin="https://desktop.example.com", websocket_path_prefixes=("/websockify",))
    v = NetworkPolicyValidator(sec)
    assert v.is_websocket_allowed("wss://desktop.example.com/websockify?token=x")
    assert not v.is_websocket_allowed("wss://desktop.example.com/other")


def test_only_exact_desktop_origin_is_allowed():
    v = validator()
    assert v.is_url_allowed("https://desktop.example.com:6080/app/vnc.html?autoconnect=1")
    assert not v.is_url_allowed("https://desktop.example.com/")  # otro puerto
    assert not v.is_url_allowed("https://other.example.com:6080/")
    for bad in ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "http://169.254.169.254/"]:
        assert not v.is_url_allowed(bad)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "http://metadata.google.internal/",
    "http://0.0.0.0:6080/",
    "http://[fe80::1]/",
    "http://2130706433/",
])
def test_metadata_and_link_local_never_allowed_as_desktop(url):
    with pytest.raises(OriginError):
        parse_desktop_origin(url, allow_private=True, allow_loopback=True)


def test_private_and_loopback_desktop_need_explicit_flags():
    with pytest.raises(OriginError):
        parse_desktop_origin("http://10.0.0.5:6080/", allow_private=False, allow_loopback=False)
    assert parse_desktop_origin("http://10.0.0.5:6080/", allow_private=True, allow_loopback=False)
    assert parse_desktop_origin("http://eval.desktop.internal:6080/", allow_private=True, allow_loopback=False)
    with pytest.raises(OriginError):
        parse_desktop_origin("http://127.0.0.1:6080/", allow_private=True, allow_loopback=False)
    assert parse_desktop_origin("http://127.0.0.1:6080/", allow_private=False, allow_loopback=True)


def test_private_exception_does_not_open_private_network():
    v = validator("http://10.0.0.5:6080/vnc.html", private=True)
    assert v.is_url_allowed("http://10.0.0.5:6080/vnc.html")
    assert not v.is_url_allowed("http://10.0.0.6:6080/")
    assert not v.is_url_allowed("http://10.0.0.5:22/")


def test_dns_check_rejects_non_global_for_regular_origins():
    sec = SecurityProfile(allowed_origins=("https://example.com",))
    v = NetworkPolicyValidator(sec)
    assert asyncio.run(v.is_request_allowed("http://127.0.0.1:8000")) is False
    assert asyncio.run(v.is_request_allowed("https://google.com")) is False


def test_allowed_origins_reject_private_hosts():
    with pytest.raises(ValidationError):
        TaskProfile(id="x", security={"allowed_origins": ["http://192.168.1.10"]})
    assert parse_url_origin("WSS://Example.com/ws", allow_websocket=True).scheme == "https"


def test_literal_secret_redaction_and_operational_data():
    DataRedactor.register_secret("ClaveUltra123")
    try:
        assert "ClaveUltra123" not in DataRedactor.redact("la clave es ClaveUltra123")
        text = DataRedactor.redact_secrets_only("email ana@example.com y ClaveUltra123")
        assert "ana@example.com" in text and "ClaveUltra123" not in text
        assert "[REDACTED_EMAIL]" in DataRedactor.redact("email ana@example.com")
        assert "abcdef123456" not in DataRedactor.redact("Authorization: Bearer abcdef123456")
    finally:
        DataRedactor.unregister_secret("ClaveUltra123")


def test_budget_reserve_confirm_and_limits(tmp_path):
    bm = BudgetManager(limit_usd=Decimal("1.00"), max_calls=3, storage_path=tmp_path / "b.json")
    rid = bm.reserve(Decimal("0.40"))
    bm.confirm(rid, actual_spend=Decimal("0.35"), tokens_used=150)
    assert bm.confirmed_spend_usd == Decimal("0.35") and bm.calls_completed == 1
    with pytest.raises(BudgetExceededError):
        bm.reserve(Decimal("0.70"))
    rid = bm.reserve(Decimal("0.10"))
    with pytest.raises(BudgetExceededError):
        bm.confirm(rid, actual_spend=Decimal("0.90"))  # sobrecoste registrado de forma conservadora
    assert bm.exhausted and not bm.can_afford(Decimal("0.01"))


def test_budget_state_file_cannot_raise_operator_limits(tmp_path):
    path = tmp_path / "b.json"
    BudgetManager(limit_usd=Decimal("5"), max_calls=1000, storage_path=path).reserve(Decimal("0.01"))
    reloaded = BudgetManager(limit_usd=Decimal("5"), max_calls=3, storage_path=path)
    assert reloaded.max_calls == 3
