"""Validación contextual (§8.8, §9.3) y política (§8.9, §11.5)."""

import time

import pytest
from pydantic import ValidationError

from web_agent.core.action_validator import ContextualValidator, LiveGeometry, ValidatedAction
from web_agent.core.context import RunState, StepRecord, TaskContext
from web_agent.core.deadline import Deadline
from web_agent.core.errors import ActionRejectedError, PolicyViolationError
from web_agent.core.observation import ElementRef, VisualObservation, new_observation_id
from web_agent.core.policy import DecisionOutcome, SecurityPolicyEnforcer
from web_agent.core.schemas import (
    ProposedAction,
    RiskLevel,
    TaskProfile,
    build_desktop_profile,
)

URL = "https://desktop.example.com/vnc.html"


def desktop_profile():
    return build_desktop_profile(task_id="t", desktop_url=URL, allow_private_desktop=False,
                                 allow_loopback_desktop=False, max_steps=40)


def visual_obs(**over):
    oid = new_observation_id()
    data = dict(observation_id=oid, run_id="run_x", session_id="ses_test", page_id="pag_test",
                navigation_epoch=0, geometry_revision=0, captured_at_monotonic=time.monotonic(),
                page_url=URL, viewport_width_css=1280, viewport_height_css=800, image_width_px=1280,
                image_height_px=800, image_format="png", image_sha256="0" * 64, image_bytes_len=10,
                image_reference=f"mem:{oid}")
    data.update(over)
    return VisualObservation(**data)


def ctx(mode="visual_only", seconds=300.0, secrets=frozenset()):
    return TaskContext(task_id="t", run_id="run_x", objective="o", perception_mode=mode,
                       deadline=Deadline.start(seconds), max_steps=40, secret_names=secrets)


def live(**over):
    data = dict(page_url=URL, page_id="pag_test", navigation_epoch=0, viewport_width_css=1280, viewport_height_css=800)
    data.update(over)
    return LiveGeometry(**data)


def state_with(obs):
    s = RunState()
    s.current_visual = obs
    s.observation_stale = False
    return s


def click(obs_id, x=500, y=500, **kw):
    return ProposedAction(action="click_xy", x=x, y=y, observation_id=obs_id, reasoning="r", **kw)


def test_valid_click_converts_to_css():
    obs = visual_obs()
    v = ContextualValidator(desktop_profile()).validate(click(obs.observation_id, 1000, 0), ctx(), state_with(obs), live())
    assert (v.x_css, v.y_css) == (1279, 0)


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda s, o: setattr(s, "observation_stale", True), "stale_observation"),
        (lambda s, o: setattr(s, "geometry_revision", 1), "geometry_changed"),
    ],
)
def test_stale_or_changed_state_rejected(mutate, code):
    obs = visual_obs()
    s = state_with(obs)
    mutate(s, obs)
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(click(obs.observation_id), ctx(), s, live())
    assert exc.value.reason_code == code


def test_other_observation_id_rejected():
    obs = visual_obs()
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(click(new_observation_id()), ctx(), state_with(obs), live())
    assert exc.value.reason_code == "stale_observation"


def test_expired_observation_rejected():
    obs = visual_obs(captured_at_monotonic=time.monotonic() - 60)
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(click(obs.observation_id), ctx(), state_with(obs), live())
    assert exc.value.reason_code == "observation_expired"


@pytest.mark.parametrize("over,code", [
    ({"navigation_epoch": 3}, "page_changed"),
    ({"page_id": "pag_other"}, "page_changed"),
    ({"viewport_width_css": 1024}, "geometry_changed"),
])
def test_live_page_mismatch_rejected(over, code):
    obs = visual_obs()
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(click(obs.observation_id), ctx(), state_with(obs), live(**over))
    assert exc.value.reason_code == code


def test_dom_action_incompatible_with_visual_only():
    obs = visual_obs()
    a = ProposedAction(action="click_dom", target_element_id="e1", observation_id=obs.observation_id, reasoning="r")
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(a, ctx(), state_with(obs), live())
    assert exc.value.reason_code == "mode_incompatible"


def test_wait_longer_than_remaining_rejected():
    a = ProposedAction(action="wait", duration_seconds=5, reasoning="r")
    with pytest.raises(ActionRejectedError):
        ContextualValidator(desktop_profile()).validate(a, ctx(seconds=3), RunState(), live())


def test_unknown_secret_reference_rejected():
    obs = visual_obs()
    a = ProposedAction(action="type_text", secret_ref="OTHER_SECRET", observation_id=obs.observation_id, reasoning="r")
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(a, ctx(secrets=frozenset({"DESKTOP_PASSWORD"})),
                                                        state_with(obs), live())
    assert exc.value.reason_code == "secret_unavailable"


def test_repeated_click_without_effect_rejected():
    obs = visual_obs()
    s = state_with(obs)
    for i in range(2):
        s.history.append(StepRecord(index=i, action="click_xy", summary="", outcome="", coords=(640, 400),
                                    screen_changed=False))
    with pytest.raises(ActionRejectedError) as exc:
        ContextualValidator(desktop_profile()).validate(click(obs.observation_id, 500, 500), ctx(), s, live())
    assert exc.value.reason_code == "repeated_without_effect"


# ------------------------------------------------------------------- política
def decide(action, profile=None, page_url=URL, element=None):
    return SecurityPolicyEnforcer(profile or desktop_profile()).decide(
        ValidatedAction(action=action, page_url=page_url, element=element))


OBS = "obs_" + "b" * 24


def test_destructive_click_requires_human():
    d = decide(click(OBS, target_description="Botón Eliminar cliente"))
    assert d.outcome == DecisionOutcome.REQUIRE_HUMAN and d.risk == RiskLevel.R3


def test_declared_effect_can_only_raise_risk():
    d = decide(click(OBS, effect_class="destructive", target_description="Botón Ver detalle"))
    assert d.risk == RiskLevel.R3
    d = decide(click(OBS, effect_class="read", target_description="Botón Guardar"))
    assert d.risk == RiskLevel.R2 and d.outcome == DecisionOutcome.ALLOW


def test_enter_is_persistent_and_allowed_only_with_scope():
    enter = ProposedAction(action="press_key", key="Enter", observation_id=OBS, reasoning="r")
    assert decide(enter).risk == RiskLevel.R2
    readonly = TaskProfile(id="ro", security={"desktop_origin": URL})
    d = decide(enter, readonly)
    assert d.outcome != DecisionOutcome.ALLOW


def test_secret_only_typed_inside_authorized_destination():
    a = ProposedAction(action="type_text", secret_ref="DESKTOP_PASSWORD", observation_id=OBS, reasoning="r")
    assert decide(a).outcome == DecisionOutcome.ALLOW
    assert decide(a, page_url="https://evil.example.org/").outcome == DecisionOutcome.DENY


def test_navigation_ssrf_blocked():
    enforcer = SecurityPolicyEnforcer(desktop_profile())
    for url in ["http://169.254.169.254/latest", "http://localhost:22", "https://evil.example.org"]:
        with pytest.raises(PolicyViolationError):
            enforcer.validate_action(ProposedAction(action="navigate", url=url, reasoning="r"))
    assert enforcer.validate_action(ProposedAction(action="navigate", url=URL, reasoning="r"))


def test_dom_control_bound_to_page_role_and_name():
    profile = TaskProfile(
        id="dom",
        security={"allowed_origins": ["https://example.com"]},
        perception={"mode": "dom"},
        permissions={"fill_forms": True},
        controls=[{"page_url": "https://example.com/app", "role": "button", "name": "Buscar",
                   "operation": "click_dom"}],
    )
    a = ProposedAction(action="click_dom", target_element_id="e1", observation_id=OBS, reasoning="r")
    ok = ElementRef(element_id="e1", role="button", name="Buscar", enabled=True, unique=True)
    other = ElementRef(element_id="e1", role="button", name="Borrar todo", enabled=True, unique=True)
    assert decide(a, profile, "https://example.com/app", ok).outcome == DecisionOutcome.ALLOW
    assert decide(a, profile, "https://example.com/app", other).outcome == DecisionOutcome.DENY
    assert decide(a, profile, "https://example.com/otra", ok).outcome == DecisionOutcome.DENY


def test_dom_actions_fail_closed_without_controls():
    profile = TaskProfile(id="dom", security={"allowed_origins": ["https://example.com"]}, perception={"mode": "dom"})
    for t in ("click_dom", "extract"):
        a = ProposedAction(action=t, target_element_id="e1", observation_id=OBS, reasoning="r")
        el = ElementRef(element_id="e1", role="button", name="X", enabled=True, unique=True)
        assert decide(a, profile, "https://example.com/", el).outcome == DecisionOutcome.DENY


def test_dom_control_denied_without_validated_element():
    """Regresión: sin elemento validado no basta con que exista un control con esa operación."""
    profile = TaskProfile(
        id="dom",
        security={"allowed_origins": ["https://example.com"]},
        perception={"mode": "dom"},
        controls=[{"page_url": "https://example.com/app", "role": "button", "name": "Buscar",
                   "operation": "click_dom"}],
    )
    a = ProposedAction(action="click_dom", target_element_id="e1", observation_id=OBS, reasoning="r")
    assert decide(a, profile, "https://example.com/app", None).outcome == DecisionOutcome.DENY


@pytest.mark.parametrize(
    "legacy,params",
    [
        ("click", {"target_element_id": "e1"}),
        ("fill", {"target_element_id": "e1", "text": "t"}),
        ("submit", {"target_element_id": "e1"}),
        ("click_coordinates", {"x": 1, "y": 1}),
        ("type_at_coordinates", {"x": 1, "y": 1, "text": "t"}),
        ("keyboard_hotkey", {"key": "Enter"}),
        ("scroll_coordinates", {"delta_y": 100}),
    ],
)
def test_action_catalog_is_closed(legacy, params):
    """Regresión (§10.2): las acciones heredadas no forman parte del contrato."""
    with pytest.raises(ValidationError) as exc:
        ProposedAction(action=legacy, observation_id=OBS, reasoning="r", **params)
    assert any(e["loc"] == ("action",) for e in exc.value.errors())


def test_proposed_action_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ProposedAction(action="fill_dom", target_element_id="e1", value="x", observation_id=OBS, reasoning="r")


def test_r3_cannot_be_removed_from_human_approval():
    with pytest.raises(ValidationError):
        TaskProfile(id="x", security={"desktop_origin": URL, "require_human_approval_for": ["R2"]})


def test_profile_cannot_exceed_300_seconds():
    with pytest.raises(ValidationError):
        TaskProfile(id="x", security={"desktop_origin": URL}, execution={"task_timeout_seconds": 3600})
