"""Motor tri-nivel integrado en el runner (navegador falso, modelo guionizado, OCR falso)."""

import asyncio

from fakes import FakeBrowser, FakeOcr, FakePage, MemoryAudit, RegionPage
from web_agent.config import Settings
from web_agent.core import runner as runner_mod
from web_agent.core.result import TaskState
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import build_desktop_profile
from web_agent.models.adapters.scripted import ScriptedModelAdapter
from web_agent.perception.ocr_engine import NullOcrEngine
from web_agent.skills import default_skills
from web_agent.skills.auth_bootstrap import DesktopAuthAndBootstrap

URL = "https://desktop.example.com/vnc.html"
VERIFIED = {"status": "verificado", "evidence": "El registro aparece en la lista."}
DONE = {"action": "done", "reasoning": "hecho"}


def make(actions, verifications=(VERIFIED,), *, page=None, ocr=None, **settings_kw):
    profile = build_desktop_profile(task_id="t1", desktop_url=URL, allow_private_desktop=False,
                                    allow_loopback_desktop=False, max_steps=12)
    model = ScriptedModelAdapter(actions, list(verifications))
    browser = FakeBrowser(page or RegionPage())
    skills = default_skills()
    for s in skills:
        if isinstance(s, DesktopAuthAndBootstrap):
            s.stability_timeout, s.interval = 0.2, 0.05
    settings_kw.setdefault("verify_stability_seconds", 0)
    settings_kw.setdefault("settle_max_seconds", 0)
    audit = MemoryAudit()
    runner = AgentRunner(profile, model, browser, audit, settings=Settings(model_provider="scripted", **settings_kw),
                         skills=skills, ocr_engine=ocr)
    return runner, model, browser, audit


def run(runner, objective="Crear el cliente ACME"):
    return asyncio.run(runner.run(objective, desktop_url=URL))


def events(audit, kind):
    return [e for e in audit.events if e.event_type == kind]


def macro(*steps, **kw):
    return {"macro_action": "rellenar_cliente", "reasoning": "formulario simple", "steps": list(steps), **kw}


FIELD = {"action": "click_xy", "x": 300, "y": 300, "target_description": "Campo Nombre"}
TYPE = {"action": "type_text", "text": "ACME"}
TAB = {"action": "press_key", "key": "Tab"}
CODE = {"action": "type_text", "text": "0180012345"}


# ------------------------------------------------------------------ Nivel 3
def test_level3_clicks_literal_target_without_calling_the_model():
    page = RegionPage()
    clients = (580, 380, 700, 404, "Clientes", 0.98)
    ocr = FakeOcr(lambda: [clients, (100, 100, 200, 124, "Inicio", 0.99), (100, 160, 200, 184, "Ayuda", 0.99)])
    runner, model, browser, audit = make([DONE], page=page, ocr=ocr)
    result = run(runner, "Haz clic en 'Clientes' para abrir el módulo")
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert ("click", 640, 392, "left", 1) in page.mouse.calls  # centro de la caja OCR, en píxeles CSS
    assert model.call_count == 1  # solo el `done`: el clic no consumió inferencia
    assert events(audit, "level3_action_selected") and events(audit, "level3_effect_observed")
    started = [e for e in events(audit, "action_started") if e.data.get("origin") == "level3"]
    assert started and started[0].data["level"] == 3 and started[0].data["risk"] == "R0"


def test_level3_never_executes_persistent_targets():
    page = RegionPage()
    ocr = FakeOcr(lambda: [(580, 380, 700, 404, "Guardar", 0.99)])
    runner, model, browser, audit = make([DONE], page=page, ocr=ocr)
    run(runner, "Pulsa 'Guardar'")
    assert not any(c[0] == "click" for c in page.mouse.calls)
    esc = [e for e in events(audit, "level3_escalated") if e.reason_code == "risk_above_r0"]
    assert esc and esc[0].data["risk"] == "R2"
    assert model.call_count >= 1  # la decisión pasa al modelo (Nivel 1/2)


def test_level3_disabled_without_ocr_falls_back_to_model(monkeypatch):
    monkeypatch.setattr(runner_mod, "create_ocr_engine", lambda **kw: NullOcrEngine("no instalado"))
    runner, model, browser, audit = make([DONE])
    result = run(runner, "Haz clic en 'Clientes'")
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    cfg = events(audit, "router_configured")[0].data
    assert cfg["nivel3"] is False and cfg["ocr"].startswith("no")


def test_ocr_not_loaded_when_objective_has_no_literal_targets(monkeypatch):
    def boom(**kw):
        raise AssertionError("no debía cargarse el OCR")
    monkeypatch.setattr(runner_mod, "create_ocr_engine", boom)
    runner, *_ = make([DONE])
    assert run(runner).final_state == TaskState.SUCCEEDED_VERIFIED


# ------------------------------------------------------------------ Nivel 2
def test_macro_burst_executes_form_with_a_single_model_call():
    page = RegionPage()
    runner, model, browser, audit = make([macro(FIELD, TYPE, TAB, CODE), DONE], page=page)
    result = run(runner)
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert ("click", 384, 240, "left", 1) in page.mouse.calls
    assert [c for c in page.keyboard.calls if c[0] in ("type", "press")] == [
        ("type", "ACME"), ("press", "Tab"), ("type", "0180012345")]
    assert model.call_count == 2  # macro + done (el bucle clásico habría usado 5)
    assert model.requests[0].allow_macro
    fin = events(audit, "level2_burst_finished")[0]
    assert fin.outcome == "ok" and fin.data["executed"] == 4
    assert result.steps_executed >= 5  # navegación de arranque + 4 pasos


def test_persistent_step_is_cut_from_the_burst():
    page = RegionPage()
    enter = {"action": "press_key", "key": "Enter", "target_description": "confirmar"}
    runner, model, browser, audit = make([macro(FIELD, TYPE, enter), DONE], page=page)
    run(runner)
    assert ("press", "Enter") not in page.keyboard.calls
    assert ("type", "ACME") in page.keyboard.calls
    fin = events(audit, "level2_burst_finished")[0]
    assert fin.data["executed"] == 2 and fin.reason_code == "paso_3_riesgo_R2"
    assert "Solo se ejecutaron 2" in (model.requests[1].feedback or "")


def test_destructive_step_in_burst_is_truncated_not_executed():
    page = RegionPage()
    delete = {"action": "click_xy", "x": 900, "y": 700, "target_description": "botón Eliminar cliente"}
    runner, model, browser, audit = make([macro(FIELD, delete, TYPE), DONE], page=page)
    run(runner)
    assert not any(c[0] == "click" and c[1] > 1000 for c in page.mouse.calls)
    assert events(audit, "level2_burst_finished")[0].reason_code == "paso_2_riesgo_R3"


def test_burst_aborts_when_screen_changes_globally_and_escalates_to_level1():
    page = RegionPage(modal_on_click=True)  # el primer clic abre una ventana
    runner, model, browser, audit = make([macro(FIELD, TYPE, TAB), DONE], page=page)
    run(runner)
    assert not any(c[0] == "type" for c in page.keyboard.calls)
    fin = events(audit, "level2_burst_finished")[0]
    assert fin.outcome == "aborted" and fin.reason_code == "pantalla_cambio_inesperadamente"
    assert fin.data["executed"] == 1
    assert model.requests[1].allow_macro is False  # paso siguiente forzado a Nivel 1
    assert "interrumpió" in (model.requests[1].feedback or "")


def test_tiered_engine_disabled_is_the_classic_loop():
    runner, model, browser, audit = make([macro(FIELD, TYPE), DONE], tiered_engine=False)
    run(runner)
    assert model.requests[0].allow_macro is False
    assert not any(c[0] == "type" for c in browser.page.keyboard.calls)  # solo el primer paso


def test_macro_first_step_keeps_level1_semantics_for_r3():
    delete = {"action": "click_xy", "x": 900, "y": 700, "target_description": "botón Eliminar cliente"}
    runner, model, browser, audit = make([macro(delete, TYPE)])
    result = run(runner)
    assert result.final_state == TaskState.NEEDS_HUMAN_REVIEW  # igual que en el bucle clásico
    assert not browser.page.mouse.calls or all(c[0] != "click" for c in browser.page.mouse.calls)


# ------------------------------------------------------------------ visión dual
def test_dual_vision_sends_thumbnail_and_roi_and_maps_roi_coordinates():
    page = RegionPage()
    roi_click = {"action": "click_xy", "x": 500, "y": 500, "coord_space": "roi",
                 "observation_id": "$OBS", "reasoning": "r", "target_description": "Campo Código"}
    first = {**FIELD, "observation_id": "$OBS", "reasoning": "r"}
    runner, model, browser, audit = make([first, roi_click, DONE], page=page, dual_vision_enabled=True)
    run(runner)
    req = model.requests[1]
    assert req.aux_images and req.image_mime == "image/jpeg"
    assert req.observation_meta["vista"] == "dual" and req.observation_meta["image_width_px"] == 400
    x0, y0, x1, y1 = req.observation_meta["roi_norm"]
    gx, gy = x0 + (x1 - x0) // 2, y0 + (y1 - y0) // 2
    clicks = [c for c in page.mouse.calls if c[0] == "click"]
    assert abs(clicks[1][1] - gx * 1279 / 1000) <= 2 and abs(clicks[1][2] - gy * 799 / 1000) <= 2


# ------------------------------------------------------------------ verificación (Mitigación 2)
def test_verification_waits_for_stable_canvas_and_uses_fresh_observation():
    page = RegionPage()
    runner, model, browser, audit = make([DONE], page=page, verify_stability_seconds=0.3)
    page.animate_frames = 0
    obs_ids = []

    original = model.verify_outcome

    async def spy(req):
        obs_ids.append(req.observation_meta["observation_id"])
        return await original(req)

    model.verify_outcome = spy

    def animate_on_done(req):
        page.animate_frames = 2  # el lienzo sigue pintándose tras el último paso
        return DONE

    model.actions = [animate_on_done]
    result = run(runner)
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert events(audit, "canvas_stability")[0].outcome == "stable"
    assert obs_ids[0] != model.requests[0].observation_meta["observation_id"]


def test_ocr_hints_reach_the_verifier_as_non_conclusive_signals():
    ocr = FakeOcr(lambda: [(400, 300, 760, 324, "Cliente guardado correctamente", 0.97)])
    runner, model, browser, audit = make([DONE], ocr=ocr)
    run(runner)
    hints = model.verify_requests[0].local_hints
    assert hints and "guardado" in hints[0]


def test_stability_wait_is_bounded_on_a_never_stable_canvas():
    page = RegionPage(animate_frames=10_000)
    runner, model, browser, audit = make([DONE], [VERIFIED], page=page, verify_stability_seconds=0.2)
    result = run(runner)
    assert events(audit, "canvas_stability")[0].outcome == "unstable"
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED  # el verificador sigue decidiendo


def test_existing_fake_page_still_works_with_router():
    runner, model, browser, audit = make([DONE], page=FakePage())
    assert run(runner).final_state == TaskState.SUCCEEDED_VERIFIED
