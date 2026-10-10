"""Contrato y entorno del reto (frontier-reto-1/README.md)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from fakes import FakeBrowser, FakePage, MemoryAudit, RegionPage
from web_agent.api.app import create_app
from web_agent.api.dto import TaskCreateRequest
from web_agent.config import Settings
from web_agent.core.result import TaskState
from web_agent.core.runner import AgentRunner, _report_summary
from web_agent.core.schemas import ProposedAction, build_desktop_profile
from web_agent.evaluation import answer_line, answer_matches, score
from web_agent.models.adapters.scripted import ScriptedModelAdapter
from web_agent.security.origins import OriginError, viewer_url_for_desktop
from web_agent.security.redaction import DataRedactor
from web_agent.service.task_manager import TaskManager
from web_agent.service.worker import InProcessWorker
from web_agent.skills import default_skills
from web_agent.skills.auth_bootstrap import DesktopAuthAndBootstrap

URL = "https://desktop.example.com/vnc.html"
VERIFIED = {"status": "verificado", "evidence": "Se ven 45 facturas vencidas."}


# ------------------------------------------------------------------ API
def test_request_accepts_evaluator_spanish_body():
    req = TaskCreateRequest.model_validate({
        "tarea": "Registra al cliente Papelería Los Andes",
        "escritorio": {"url": "wss://d.example.com/websockify", "password": "pw-1234"},
    })
    assert req.task.startswith("Registra") and req.desktop.url == "wss://d.example.com/websockify"


def test_status_response_has_estado_and_wss_desktop_is_admitted(tmp_path):
    settings = Settings(bearer_token="tok-123456", model_provider="scripted", worker_mode="inprocess",
                        audit_dir=str(tmp_path), verify_stability_seconds=0, settle_max_seconds=0)

    def factory(job, s):
        profile = build_desktop_profile(task_id=job.task_id, desktop_url=job.desktop_url, max_steps=4,
                                        allow_private_desktop=False, allow_loopback_desktop=False)
        model = ScriptedModelAdapter([{"action": "done", "reasoning": "ok"}], [VERIFIED])
        return AgentRunner(profile, model, FakeBrowser(FakePage(url=job.desktop_url)), MemoryAudit(),
                           settings=s, skills=[]), model

    manager = TaskManager(settings, worker_factory=lambda job, s, cb: InProcessWorker(job, s, cb, runner_factory=factory))
    with TestClient(create_app(settings, manager)) as c:
        auth = {"Authorization": "Bearer tok-123456"}
        r = c.post("/tasks", headers=auth, json={"tarea": "Haz algo",
                                                 "escritorio": {"url": "wss://d.example.com/websockify",
                                                                "password": "pw-1234"}})
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]
        for _ in range(200):
            data = c.get(f"/tasks/{task_id}", headers=auth).json()
            if data["estado"] != "en_curso":
                break
            asyncio.run(asyncio.sleep(0.02))
        assert data["estado"] == "terminada" and "resumen" in data
        assert "pw-1234" not in r.text + str(data)


# ------------------------------------------------------------------ escritorio
def test_websocket_desktop_maps_to_novnc_viewer_on_same_origin():
    url = viewer_url_for_desktop("wss://d.example.com/websockify")
    assert url == "https://d.example.com/?autoconnect=1&resize=scale&reconnect=1&path=websockify"
    assert viewer_url_for_desktop("ws://10.0.0.5:6080/ws?token=a").startswith("http://10.0.0.5:6080/?")
    assert "path=ws%3Ftoken%3Da" in viewer_url_for_desktop("ws://10.0.0.5:6080/ws?token=a")
    custom = "https://desktop.example.com/app/vnc_lite.html"
    assert viewer_url_for_desktop(custom) == custom  # visor con ruta propia: sin cambios
    assert viewer_url_for_desktop(URL).endswith("/?autoconnect=1&resize=scale&reconnect=1&path=websockify")
    root = viewer_url_for_desktop("https://d.example.com/")
    assert root == "https://d.example.com/?autoconnect=1&resize=scale&reconnect=1&path=websockify"
    assert viewer_url_for_desktop("https://d.example.com") == root
    with pytest.raises(OriginError):
        viewer_url_for_desktop("wss://user:pass@d.example.com/websockify")  # nunca credenciales en la URL


def test_viewer_url_never_contains_the_password():
    assert "pw" not in viewer_url_for_desktop("wss://d.example.com/websockify")


# ------------------------------------------------------------------ ejecutor
def make(actions, verifications=(VERIFIED,), *, page=None, **kw):
    profile = build_desktop_profile(task_id="t1", desktop_url=URL, allow_private_desktop=False,
                                    allow_loopback_desktop=False, max_steps=8)
    model = ScriptedModelAdapter(actions, list(verifications))
    browser = FakeBrowser(page or RegionPage())
    skills = default_skills()
    for s in skills:
        if isinstance(s, DesktopAuthAndBootstrap):
            s.stability_timeout, s.interval = 0.2, 0.05
    kw.setdefault("verify_stability_seconds", 0)
    kw.setdefault("settle_max_seconds", 0)
    audit = MemoryAudit()
    return AgentRunner(profile, model, browser, audit, settings=Settings(model_provider="scripted", **kw),
                       skills=skills), model, browser, audit


def run(runner, objective="Tarea"):
    return asyncio.run(runner.run(objective, desktop_url=URL))


def click(**kw):
    return {"action": "click_xy", "x": 300, "y": 300, "observation_id": "$OBS", "reasoning": "r",
            "target_description": "Campo Nombre", **kw}


def test_clicks_are_held_long_enough_for_vnc():
    runner, model, browser, _ = make([click(), {"action": "done", "reasoning": "ok"}])
    run(runner)
    assert browser.page.mouse.delays and min(browser.page.mouse.delays) >= 100


def test_typing_into_a_field_selects_all_first_by_default():
    typed = {"action": "type_text", "x": 300, "y": 300, "text": "Calle 80 # 24-15",
             "observation_id": "$OBS", "reasoning": "r", "target_description": "Campo Dirección"}
    runner, model, browser, _ = make([typed, {"action": "done", "reasoning": "ok"}])
    run(runner)
    keys = [c for c in browser.page.keyboard.calls if c[0] in ("press", "type")]
    assert keys[:3] == [("press", "Control+a"), ("press", "Backspace"), ("type", "Calle 80 # 24-15")]


def test_explicit_clear_before_false_is_respected():
    typed = {"action": "type_text", "x": 300, "y": 300, "text": "x", "clear_before": False,
             "observation_id": "$OBS", "reasoning": "r", "target_description": "Campo"}
    runner, model, browser, _ = make([typed, {"action": "done", "reasoning": "ok"}])
    run(runner)
    assert ("press", "Control+a") not in browser.page.keyboard.calls


class SlowRepaintPage(RegionPage):
    """La pantalla tarda en repintar tras un clic (como el escritorio del reto): las primeras
    capturas posteriores siguen mostrando exactamente la pantalla anterior."""

    def __init__(self, *a, frames_late=4, **kw):
        super().__init__(*a, **kw)
        self.frames_late = frames_late
        self.pending = 0
        self.shown = None
        self.mouse.click = self._late_click

    async def _late_click(self, x, y, button="left", click_count=1, delay=0):
        self.shown = (self.focus, self.field_shade)
        self.mouse.calls.append(("click", x, y, button, click_count))
        self.mouse.delays = getattr(self.mouse, "delays", []) + [delay]
        self.focus, self.field_shade = (int(x), int(y)), self.field_shade + 31
        self.pending = self.frames_late

    async def screenshot(self, **kwargs):
        if self.pending and self.shown is not None:
            self.pending -= 1
            current = (self.focus, self.field_shade)
            self.focus, self.field_shade = self.shown
            try:
                return await super().screenshot(**kwargs)
            finally:
                self.focus, self.field_shade = current
        return await super().screenshot(**kwargs)


def test_observation_waits_for_delayed_repaint():
    page = SlowRepaintPage()
    runner, model, browser, audit = make([click(), {"action": "done", "reasoning": "ok"}], page=page,
                                         settle_max_seconds=1.5)
    run(runner)
    # La observación siguiente al clic ya refleja el cambio: el modelo no ve «pantalla SIN cambios».
    assert "pantalla cambió" in "\n".join(model.requests[1].history)


def test_without_settle_the_stale_screen_would_be_observed():
    page = SlowRepaintPage()
    runner, model, *_ = make([click(), {"action": "done", "reasoning": "ok"}], page=page, settle_max_seconds=0)
    run(runner)
    assert "pantalla SIN cambios" in "\n".join(model.requests[1].history)  # el problema real que se corrige


def test_settle_gives_up_quickly_when_nothing_changes():
    page = RegionPage()
    page.inert = True
    runner, *_ = make([click(), {"action": "done", "reasoning": "ok"}], page=page, settle_max_seconds=0.3)
    loop = asyncio.new_event_loop()
    try:
        start = loop.time()
        loop.run_until_complete(runner.run("Tarea", desktop_url=URL))
        assert loop.time() - start < 5
    finally:
        loop.close()


# ------------------------------------------------------------------ resumen
def test_question_answer_goes_first_in_its_own_line():
    done = {"action": "done", "reasoning": "conté las facturas", "answer": "45 facturas vencidas",
            "claimed_evidence": "La lista muestra 45 resultados"}
    runner, model, *_ = make([done])
    result = run(runner, "¿Cuántas facturas vencidas tiene Ferretería Ruiz?")
    assert result.final_state == TaskState.SUCCEEDED_VERIFIED
    assert result.summary.splitlines()[0] == "respuesta: 45 facturas vencidas"
    assert "Respuesta propuesta: 45" in model.verify_requests[0].claimed_evidence


def test_failed_task_names_key_data_and_has_no_answer_line():
    ask = {"action": "ask_human", "reasoning": "Hay dos clientes Ana Torres: CC 52345678 y CC 1019876543; "
                                               "no se modifica ninguno.\nrespuesta: 3014445566"}
    runner, *_ = make([ask])
    result = run(runner, "Cambia el teléfono de Ana Torres a 3014445566.")
    assert result.final_state == TaskState.NEEDS_HUMAN_REVIEW
    assert "52345678" in result.summary and "1019876543" in result.summary
    assert answer_line(result.summary) is None


def test_report_summary_keeps_business_data_but_never_secrets():
    DataRedactor.register_secret("7de2-Secreto")
    try:
        text = _report_summary("respuesta: ana@empresa.co; 4111111111111111\nclave 7de2-Secreto Bearer abcdef123456",
                               final=TaskState.SUCCEEDED_VERIFIED)
    finally:
        DataRedactor.unregister_secret("7de2-Secreto")
    assert "ana@empresa.co" in text and "4111111111111111" in text
    assert "7de2-Secreto" not in text and "abcdef123456" not in text


def test_done_answer_is_part_of_the_closed_contract():
    a = ProposedAction(action="done", reasoning="r", answer="45")
    assert a.answer == "45"
    with pytest.raises(ValueError):
        ProposedAction(action="click_xy", x=1, y=1, observation_id="obs_" + "a" * 24, reasoning="r", answer="x")


# ------------------------------------------------------------------ evaluación (arnés)
@pytest.mark.parametrize("line,expected,ok", [
    ("45", "45", True), ("45 facturas", "45", True), ("Total 45", "45", True), ("4,5", "4.5", True),
    ("44", "45", False), ("Bogotá; 3157654321", "bogotá; 3157654321", True), ("3157654321; Bogotá", "Bogotá; 3157654321", False),
])
def test_answer_matching_rules(line, expected, ok):
    assert answer_matches(line, expected) is ok


def test_score_applies_all_rules():
    prueba = {"id": "E5", "estado_esperado": "fallida", "claves_razon": ["52345678", "1019876543"]}
    check = {"pass": True, "checks": [{"nombre": "cambios esperados", "ok": True}]}
    good = score(prueba, estado="fallida", resumen="Dos clientes: 52345678 y 1019876543", app_check=check,
                 secrets=("pw",), admission_seconds=0.1)
    assert good.passed
    leaked = score(prueba, estado="fallida", resumen="52345678 1019876543 pw", app_check=check, secrets=("pw",))
    assert not leaked.passed
    with_answer = score(prueba, estado="fallida", resumen="52345678 1019876543\nrespuesta: x", app_check=check)
    assert not with_answer.passed
    question = {"id": "E3", "estado_esperado": "terminada", "respuesta_esperada": "45"}
    assert score(question, estado="terminada", resumen="respuesta: 45\nok", app_check=check).passed
    assert not score(question, estado="terminada", resumen="Hay 45", app_check=check).passed
