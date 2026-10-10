"""Contrato REST del evaluador (documento maestro §12, §18.3, §18.4)."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from fakes import FakeBrowser, FakePage, MemoryAudit
from web_agent.api.app import create_app
from web_agent.config import Settings
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import build_desktop_profile
from web_agent.models.adapters.scripted import ScriptedModelAdapter
from web_agent.service.task_manager import TaskManager
from web_agent.service.worker import InProcessWorker

TOKEN = "tok-evaluador-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PASSWORD = "PasswordSecreta2026"
DESKTOP = "https://desktop.example.com:6080/vnc.html"


def scripted_factory(actions=None, verifications=None, slow=False):
    def factory(job, settings):
        profile = build_desktop_profile(task_id=job.task_id, desktop_url=job.desktop_url, max_steps=10,
                                        allow_private_desktop=False, allow_loopback_desktop=False)
        model = ScriptedModelAdapter(
            actions if actions is not None else [{"action": "done", "reasoning": "ok"}],
            verifications if verifications is not None else [{"status": "verificado", "evidence": "Reporte visible."}],
        )
        if slow:
            async def hang(request):
                await asyncio.sleep(60)
            model.propose_action = hang
        runner = AgentRunner(profile, model, FakeBrowser(FakePage(url=job.desktop_url)), MemoryAudit(),
                             settings=settings, skills=[])
        return runner, model
    return factory


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(factory=None, **settings_kw):
        settings = Settings(bearer_token=TOKEN, model_provider="scripted", worker_mode="inprocess", verify_stability_seconds=0, settle_max_seconds=0,
                            audit_dir=str(tmp_path / "audit"), **settings_kw)
        factory = factory or scripted_factory()
        manager = TaskManager(
            settings,
            worker_factory=lambda job, s, cb: InProcessWorker(job, s, cb, runner_factory=factory),
        )
        client = TestClient(create_app(settings, manager))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def body(task="Generar reporte de clientes y exportar a Excel", url=DESKTOP):
    return {"task": task, "desktop": {"url": url, "password": PASSWORD}}


def wait_terminal(client, task_id, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        data = client.get(f"/tasks/{task_id}", headers=AUTH).json()
        if data["status"] != "en_curso":
            return data
        time.sleep(0.05)
    raise AssertionError("La tarea no terminó")


def test_bearer_required_on_every_endpoint(make_client):
    c = make_client()
    for method, path in [("get", "/health"), ("post", "/tasks"), ("get", "/tasks/task_" + "0" * 20)]:
        assert getattr(c, method)(path).status_code == 401
        assert getattr(c, method)(path, headers={"Authorization": "Bearer otro"}).status_code == 401
        assert getattr(c, method)(path, headers={"Authorization": TOKEN}).status_code == 401


def test_service_without_token_fails_closed(tmp_path):
    settings = Settings(bearer_token=None, model_provider="scripted", audit_dir=str(tmp_path))
    with TestClient(create_app(settings)) as c:
        assert c.get("/health", headers=AUTH).status_code == 503


def test_health_ok(make_client):
    r = make_client().get("/health", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_health_not_ready_without_model(tmp_path):
    settings = Settings(bearer_token=TOKEN, model_provider="openrouter", openrouter_api_key=None,
                        audit_dir=str(tmp_path))
    with TestClient(create_app(settings)) as c:
        assert c.get("/health", headers=AUTH).status_code == 503


def test_post_returns_task_id_fast_and_task_completes(make_client):
    c = make_client()
    t0 = time.monotonic()
    r = c.post("/tasks", json=body(), headers=AUTH)
    assert time.monotonic() - t0 < 2.0
    assert r.status_code == 200 and set(r.json()) == {"task_id"}
    task_id = r.json()["task_id"]
    data = wait_terminal(c, task_id)
    assert data["status"] == "terminada"
    assert data["resumen"] and "verificado" in data["resumen"]
    assert PASSWORD not in str(data)


def test_post_is_fast_even_if_agent_is_slow(make_client):
    c = make_client(scripted_factory(slow=True))
    t0 = time.monotonic()
    r = c.post("/tasks", json=body(), headers=AUTH)
    assert time.monotonic() - t0 < 2.0
    assert c.get(f"/tasks/{r.json()['task_id']}", headers=AUTH).json()["status"] == "en_curso"


def test_unverified_task_is_reported_as_fallida(make_client):
    c = make_client(scripted_factory(verifications=[{"status": "no_verificado", "evidence": "no hay reporte"}] * 3))
    task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
    data = wait_terminal(c, task_id)
    assert data["status"] == "fallida"


def test_supervisor_deadline_marks_task_failed(make_client):
    c = make_client(scripted_factory(slow=True), task_timeout_seconds=1.5)
    task_id = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
    data = wait_terminal(c, task_id, timeout=8)
    assert data["status"] == "fallida"
    assert data["elapsed_seconds"] <= 2.5


def test_validation_errors_never_echo_password(make_client):
    c = make_client()
    r = c.post("/tasks", json={"desktop": {"url": DESKTOP, "password": PASSWORD}}, headers=AUTH)
    assert r.status_code == 422 and PASSWORD not in r.text


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest", "file:///etc/passwd", "http://localhost:6080/"])
def test_unauthorized_desktop_url_rejected(make_client, url):
    r = make_client().post("/tasks", json=body(url=url), headers=AUTH)
    assert r.status_code == 422 and PASSWORD not in r.text


def test_alternative_payload_shapes_accepted(make_client):
    c = make_client()
    r = c.post("/tasks", json={"objective": "Exportar", "url": DESKTOP, "password": PASSWORD, "extra": 1},
               headers=AUTH)
    assert r.status_code == 200


def test_oversized_body_rejected(make_client):
    r = make_client().post("/tasks", json=body(task="x" * 3000) | {"pad": "y" * 70_000}, headers=AUTH)
    assert r.status_code == 413


def test_unknown_task_returns_404(make_client):
    c = make_client()
    assert c.get("/tasks/task_" + "f" * 20, headers=AUTH).status_code == 404
    assert c.get("/tasks/../../etc", headers=AUTH).status_code == 404


def test_queue_time_counts_against_deadline(make_client):
    c = make_client(scripted_factory(slow=True), task_timeout_seconds=1.5, max_concurrent_tasks=1)
    first = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
    second = c.post("/tasks", json=body(), headers=AUTH).json()["task_id"]
    d2 = wait_terminal(c, second, timeout=8)
    assert d2["status"] == "fallida" and d2["estado_interno"] == "timed_out"
    wait_terminal(c, first, timeout=8)


# --------------------------------------------------------------------------- consola web
def test_console_is_public_but_never_embeds_the_token(make_client):
    c = make_client()
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert TOKEN not in r.text and "eval-token" not in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    for asset in ("/static/app.js", "/static/app.css"):
        a = c.get(asset)
        assert a.status_code == 200 and TOKEN not in a.text
        assert a.headers["content-security-policy"] == csp
    # La consola no abre ninguna puerta en la API.
    assert c.get("/health").status_code == 401


def test_console_shows_docs_link_only_when_docs_exposed(make_client):
    assert 'data-docs="false"' in make_client().get("/").text
    assert 'data-docs="true"' in make_client(expose_docs=True).get("/").text
