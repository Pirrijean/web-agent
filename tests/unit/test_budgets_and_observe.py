from decimal import Decimal
import pytest
from web_agent.execution.budgets import BudgetManager, BudgetExceededError
from web_agent.browser.observe import PageObservationSnapshot, ObservedElement


def test_budget_manager_reserve_confirm_and_exceed(tmp_path):
    storage = tmp_path / "budget_state.json"
    bm = BudgetManager(limit_usd=Decimal("1.00"), max_calls=3, storage_path=storage)

    assert bm.remaining_budget_usd == Decimal("1.00")
    assert bm.can_afford(Decimal("0.50")) is True

    # Reserva 0.40
    res_id = bm.reserve(Decimal("0.40"))
    assert bm.remaining_budget_usd == Decimal("0.60")

    # Confirma gasto real 0.35
    bm.confirm(res_id, actual_spend=Decimal("0.35"), tokens_used=150)
    assert bm.confirmed_spend_usd == Decimal("0.35")
    assert bm.remaining_budget_usd == Decimal("0.65")
    assert bm.calls_completed == 1
    assert bm.tokens_consumed == 150

    # Comprueba que no puede gastar más de lo disponible
    assert bm.can_afford(Decimal("0.70")) is False
    with pytest.raises(BudgetExceededError):
        bm.reserve(Decimal("0.70"))


def test_observation_snapshot_bounded_prompt():
    elements = {
        "e0": ObservedElement(element_id="e0", role="button", name="Entrar"),
        "e1": ObservedElement(element_id="e1", role="password_field", name="[CAMPO_PASSWORD_PROTEGIDO]"),
    }
    snapshot = PageObservationSnapshot(
        observation_id="obs_test_123",
        url="https://example.com/login",
        title="Inicio de Sesión",
        elements=elements,
    )
    prompt = snapshot.to_model_prompt()

    assert "OBSERVATION_ID: obs_test_123" in prompt
    assert "[e0] BUTTON: 'Entrar'" in prompt
    assert "[CAMPO_PASSWORD_PROTEGIDO]" in prompt
