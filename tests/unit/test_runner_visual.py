"""Runner en modo visual_only con navegador falso y proveedor scripted (§18)."""

import asyncio

from fakes import FakeBrowser, MemoryAudit
from web_agent.config import Settings
from web_agent.core.context import SecretStore
from web_agent.core.deadline import Deadline
from web_agent.core.result import TaskState
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import build_desktop_profile
from web_agent.models.adapters.scripted import OBS_PLACEHOLDER, ScriptedModelAdapter
from web_agent.skills import default_skills
from web_agent.skills.auth_bootstrap import DesktopAuthAndBootstrap

URL = "https://desktop.example.com/vnc.html"
PASSWORD = "Sup3rSecreta-2026"
VERIFIED = {"status": "verificado", "evidence": "El registro aparece en la lista."}


def make_runner(actions, verifications=(), *, audit=None, settings=None, browser=None):
    profile = build_desktop_profile(task_id="t1", desktop_url=URL, allow_private_desktop=False,
                                    allow_loopback_desktop=False, max_steps=10)
    model = ScriptedModelAdapter(actions, verifications)
    browser = browser or FakeBrowser()
    skills = [s for s in default_skills()]
    for s in skills:
        if isinstance(s, DesktopAuthAndBootstrap):
            s.stability_timeout, s.interval = 0.3, 0.05
    runner = AgentRunner(profile, model, browser, audit or MemoryAudit(),
                         settings=settings or Settings(model_provider="scripted"), skills=skills)
    return runner, model, browser


def run(runner, **kw):
    kw.setdefault("desktop_url", URL)
    return asyncio.run(runner.run("Crear el cliente ACME y guardarlo", **kw))


def click(x, y, desc="Campo Nombre", **kw):
    return {"action": "click_xy", "x": x, "y": y, "observation_id": OBS_PLACEHOLDER, "reasoning": "r",
            "target_description": desc, **kw}


def test_click_uses_css_coordinates_and_success_requires_verification():
    runner, model, browser = make_runner([click(1000, 0), {"action": "done", "reasoning": "hecho"}], [VERIFIED])
    result = run(runner)
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert ("click", 1279, 0, "left", 1) in browser.page.mouse.calls
    assert model.requests[0].image_bytes.startswith(b"\x89PNG")
    assert browser.page.navigations == [URL]  # bootstrap por la ruta común
    assert browser.stopped


def test_done_without_evidence_is_not_success():
    runner, _, _ = make_runner([{"action": "done", "reasoning": "creo que sí"}] * 3,
                               [{"status": "incierto", "evidence": "no se ve"}] * 3)
    result = run(runner)
    assert result.final_state == TaskState.UNCERTAIN
    assert result.final_state != TaskState.SUCCEEDED_VERIFIED


def test_not_verified_feedback_lets_agent_continue():
    runner, model, _ = make_runner(
        [{"action": "done", "reasoning": "x"}, click(10, 10), {"action": "done", "reasoning": "y"}],
        [{"status": "no_verificado", "evidence": "falta guardar"}, VERIFIED],
    )
    result = run(runner)
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert any("NO confirma" in (r.feedback or "") for r in model.requests)


def test_stale_observation_is_rejected_and_not_executed():
    stale = click(500, 500)
    stale["observation_id"] = "obs_" + "c" * 24
    runner, model, browser = make_runner([stale, {"action": "done", "reasoning": "d"}], [VERIFIED])
    audit = runner.audit_logger
    run(runner)
    assert not [c for c in browser.page.mouse.calls if c[0] == "click"]
    assert any(e.event_type == "proposal_rejected" and e.reason_code == "stale_observation" for e in audit.events)


def test_destructive_action_stops_before_acting():
    runner, _, browser = make_runner([click(500, 500, desc="Botón Eliminar registro")])
    result = run(runner)
    assert result.final_state == TaskState.NEEDS_HUMAN_REVIEW
    assert not [c for c in browser.page.mouse.calls if c[0] == "click"]


def test_secret_is_typed_but_never_reaches_model_or_audit():
    audit = MemoryAudit()
    runner, model, browser = make_runner(
        [{"action": "type_text", "x": 500, "y": 500, "secret_ref": "DESKTOP_PASSWORD",
          "observation_id": OBS_PLACEHOLDER, "reasoning": "acceso"},
         {"action": "done", "reasoning": "d"}],
        [VERIFIED],
        audit=audit,
    )
    result = asyncio.run(runner.run(f"Entrar al escritorio (clave {PASSWORD})", desktop_url=URL,
                                    secrets=SecretStore({"DESKTOP_PASSWORD": PASSWORD})))
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert ("type", PASSWORD) in browser.page.keyboard.calls
    for req in model.requests:
        assert PASSWORD not in req.objective and PASSWORD not in "".join(req.history)
        assert "DESKTOP_PASSWORD" in req.secret_names
    assert PASSWORD not in audit.dump()
    assert PASSWORD not in result.summary


def test_objective_data_is_not_masked_for_the_model():
    runner, model, _ = make_runner([{"action": "done", "reasoning": "d"}], [VERIFIED])
    asyncio.run(runner.run("Registrar el email ana@example.com", desktop_url=URL))
    assert "ana@example.com" in model.requests[0].objective


def test_audit_failure_blocks_before_browser_start():
    runner, model, browser = make_runner([click(1, 1)], audit=MemoryAudit(fail=True))
    result = run(runner)
    assert result.final_state == TaskState.BLOCKED
    assert not browser.started and model.call_count == 0


def test_ask_human_without_channel_ends_safely():
    runner, _, _ = make_runner([{"action": "ask_human", "reasoning": "¿Borro?"}])
    assert run(runner).final_state == TaskState.NEEDS_HUMAN_REVIEW


def test_invalid_model_output_is_bounded():
    runner, _, browser = make_runner([{"action": "click_xy", "reasoning": "sin coords"}] * 5)
    result = run(runner)
    assert result.final_state == TaskState.FAILED and result.reason_code == "invalid_model_output"
    assert not [c for c in browser.page.mouse.calls if c[0] == "click"]


def test_global_deadline_is_enforced_even_if_model_hangs():
    async def slow(request):
        await asyncio.sleep(30)

    class SlowModel(ScriptedModelAdapter):
        async def propose_action(self, request):
            await asyncio.sleep(30)

    runner, _, _ = make_runner([])
    runner.model = SlowModel()
    import time
    t0 = time.monotonic()
    result = asyncio.run(runner.run("x", desktop_url=URL, deadline=Deadline.start(1.5)))
    assert time.monotonic() - t0 < 5
    assert result.final_state in (TaskState.TIMED_OUT, TaskState.UNCERTAIN)


def test_model_call_budget_is_enforced():
    runner, _, _ = make_runner([{"action": "wait", "duration_seconds": 0.1, "reasoning": "w"}] * 20,
                               settings=Settings(model_provider="scripted", max_model_calls_per_task=3))
    result = run(runner)
    assert result.final_state == TaskState.FAILED and result.reason_code == "budget_exhausted"
    assert result.model_calls <= 3


def test_repeated_inert_clicks_trigger_recovery_feedback():
    browser = FakeBrowser()
    runner, model, _ = make_runner([click(500, 500)] * 4 + [{"action": "done", "reasoning": "d"}],
                                   [VERIFIED], browser=browser)
    browser.page.inert = True
    run(runner)
    clicks = [c for c in browser.page.mouse.calls if c[0] == "click"]
    assert len(clicks) == 2  # el tercer clic idéntico sin efecto se rechaza
    assert any("repeated_without_effect" in (r.feedback or "") for r in model.requests)


def test_navigation_outside_scope_blocks_task():
    runner, _, browser = make_runner([{"action": "navigate", "url": "http://169.254.169.254/", "reasoning": "x"}])
    result = run(runner)
    assert result.final_state == TaskState.BLOCKED
    assert browser.page.navigations == [URL]


def test_screen_injection_text_cannot_expand_permissions():
    # El "modelo" obedece una inyección de pantalla y propone salir del ámbito.
    runner, _, browser = make_runner([
        {"action": "navigate", "url": "https://attacker.example.org/exfil", "reasoning": "La pantalla lo pide"},
    ])
    assert run(runner).final_state == TaskState.BLOCKED
    assert "attacker" not in "".join(browser.page.navigations)
