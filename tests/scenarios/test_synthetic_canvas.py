"""Escenario GUI sintético con Chromium real (plan de validación §2.3; maestro §17 fases 2–3).

Un "escritorio remoto" dibujado íntegramente en <canvas>: no existe DOM de
controles. El agente opera solo con capturas, coordenadas y teclado. El
servidor de prueba expone HTTP y WebSocket en el mismo origen (como noVNC) y
otro WebSocket en un puerto distinto que debe quedar bloqueado.

El guion del modelo conoce la disposición (un VLM real la inferiría de la
captura); lo que se prueba es la cadena geometría → validación → política →
ejecutor → transporte → auditoría.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

websockets = pytest.importorskip("websockets")
from websockets.asyncio.server import serve  # noqa: E402
from websockets.datastructures import Headers  # noqa: E402
from websockets.http11 import Response  # noqa: E402

from web_agent.audit.events import AuditLogger  # noqa: E402
from web_agent.browser.session import SecureBrowserSession  # noqa: E402
from web_agent.config import Settings  # noqa: E402
from web_agent.core.context import SecretStore  # noqa: E402
from web_agent.core.result import TaskState  # noqa: E402
from web_agent.core.runner import AgentRunner  # noqa: E402
from web_agent.core.schemas import build_desktop_profile  # noqa: E402
from web_agent.models.adapters.scripted import OBS_PLACEHOLDER, ScriptedModelAdapter  # noqa: E402

pytestmark = pytest.mark.browser

PASSWORD = "Sup3r-Pass!2026"
W, H = 1280, 800

APP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;overflow:hidden}</style></head>
<body><canvas id="c"></canvas><script>
const PASSWORD = %(password)s, OTHER = %(other)d;
const c = document.getElementById('c'); c.width = innerWidth; c.height = innerHeight;
const g = c.getContext('2d');
const S = {screen:'login', pwd:'', focus:null, name:'', records:[], modal:false, ws_ok:false, ws_other:'pending', login_attempts:0};
const R = {pwd:[440,360,400,40], name:[440,300,400,40], save:[540,380,200,50], ok:[520,430,110,40], cancel:[650,430,110,40]};
const inR = (r,x,y) => x>=r[0] && x<r[0]+r[2] && y>=r[1] && y<r[1]+r[3];
function report(){ const s = Object.assign({}, S); delete s.pwd; s.pwd_len = S.pwd.length;
  fetch('/state', {method:'POST', body: JSON.stringify(s)}).catch(()=>{}); }
function box(r, fill, label, focus){ g.fillStyle=fill; g.fillRect(...r); g.strokeStyle=focus?'#0050ef':'#555';
  g.lineWidth=focus?3:1; g.strokeRect(...r); g.fillStyle='#000'; g.font='18px sans-serif'; g.fillText(label, r[0]+10, r[1]+r[3]/2+6); }
function draw(){
  g.fillStyle='#008080'; g.fillRect(0,0,c.width,c.height);
  g.fillStyle='#c0c0c0'; g.fillRect(340,200,600,360); g.fillStyle='#000080'; g.fillRect(340,200,600,30);
  g.fillStyle='#fff'; g.font='16px sans-serif';
  if (S.screen==='login'){ g.fillText('Conexión al escritorio remoto', 350, 220);
    g.fillStyle='#000'; g.fillText('Contraseña:', 440, 350); box(R.pwd,'#fff','•'.repeat(S.pwd.length), S.focus==='pwd'); }
  else { g.fillText('Clientes - Alta', 350, 220); g.fillStyle='#000'; g.fillText('Nombre:', 440, 290);
    box(R.name,'#fff',S.name,S.focus==='name'); box(R.save,'#d4d0c8','Guardar',false);
    g.fillText('Registros: '+S.records.join(', '), 360, 540);
    if (S.modal){ g.fillStyle='#d4d0c8'; g.fillRect(480,330,320,160); g.fillStyle='#000';
      g.fillText('¿Guardar cliente?', 500, 380); box(R.ok,'#d4d0c8','Aceptar',false); box(R.cancel,'#d4d0c8','Cancelar',false); } }
}
c.addEventListener('mousedown', e => { const x=e.clientX, y=e.clientY;
  if (S.screen==='login'){ S.focus = inR(R.pwd,x,y) ? 'pwd' : null; }
  else if (S.modal){ if (inR(R.ok,x,y)){ S.records.push(S.name); S.name=''; S.modal=false; }
                     else if (inR(R.cancel,x,y)){ S.modal=false; } }
  else { if (inR(R.name,x,y)) S.focus='name'; else if (inR(R.save,x,y)) { S.modal = !!S.name; } else S.focus=null; }
  draw(); report(); });
document.addEventListener('keydown', e => {
  if (e.key==='Enter' && S.screen==='login'){ S.login_attempts++; if (S.pwd===PASSWORD){ S.screen='form'; S.focus=null; } else S.pwd=''; }
  else if (e.key==='Backspace'){ if (S.focus==='pwd') S.pwd=S.pwd.slice(0,-1); if (S.focus==='name') S.name=S.name.slice(0,-1); }
  else if (e.key.length===1){ if (S.focus==='pwd') S.pwd+=e.key; else if (S.focus==='name') S.name+=e.key; }
  draw(); report(); });
const ws = new WebSocket(`ws://${location.host}/websockify`);
ws.onopen = () => { ws.send('hello'); };
ws.onmessage = () => { S.ws_ok = true; report(); };
const ws2 = new WebSocket(`ws://127.0.0.1:${OTHER}/exfil`);
ws2.onopen = () => { S.ws_other = 'open'; report(); };
ws2.onclose = () => { if (S.ws_other !== 'open') { S.ws_other = 'blocked'; report(); } };
draw(); report();
</script></body></html>"""


class DesktopServer:
    def __init__(self):
        self.state: dict = {}
        self.port = 0
        self.other_port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Future | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        async def ws_handler(conn):
            async for msg in conn:
                await conn.send("echo:" + msg)

        def process_request(conn, request):
            path = request.path.split("?")[0]
            if path == "/websockify" or path == "/exfil":
                return None  # handshake WebSocket
            if path == "/vnc.html":
                html = APP_HTML % {"password": json.dumps(PASSWORD), "other": self.other_port}
                return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), html.encode())
            return Response(404, "Not Found", Headers({"Content-Type": "text/plain"}), b"not found")

        async def other_handler(conn):
            async for msg in conn:
                await conn.send(msg)

        async with serve(ws_handler, "127.0.0.1", 0, process_request=process_request) as srv, \
                serve(other_handler, "127.0.0.1", 0) as other:
            self.port = srv.sockets[0].getsockname()[1]
            self.other_port = other.sockets[0].getsockname()[1]
            self._stop = self._loop.create_future()
            self._ready.set()
            await self._stop

    def start(self):
        self.thread.start()
        self._ready.wait(10)

    def stop(self):
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self.thread.join(5)


def _state_collector(server: DesktopServer):
    """El estado de la app se recoge por POST /state (vía route de prueba en el contexto)."""


def norm(px: int, size: int) -> int:
    return round(px * 1000 / (size - 1))


@pytest.fixture
def desktop():
    server = DesktopServer()
    server.start()
    yield server
    server.stop()


def _launchable() -> bool:
    async def probe():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            await b.close()
    try:
        asyncio.run(probe())
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible en este entorno")
def test_canvas_desktop_end_to_end(desktop, tmp_path):
    url = f"http://127.0.0.1:{desktop.port}/vnc.html"
    settings = Settings(model_provider="scripted", allow_loopback_desktop=True, audit_dir=str(tmp_path / "audit"),
                        typing_delay_ms=5, viewport_width=W, viewport_height=H)
    profile = build_desktop_profile(task_id="canvas", desktop_url=url, allow_private_desktop=False,
                                    allow_loopback_desktop=True, max_steps=15)

    def step(action, **kw):
        return {"action": action, "observation_id": OBS_PLACEHOLDER, "reasoning": "guion", **kw}

    actions = [
        step("type_text", x=norm(640, W), y=norm(380, H), secret_ref="DESKTOP_PASSWORD",
             target_description="Campo Contraseña del diálogo de conexión"),
        step("press_key", key="Enter", target_description="Confirmar conexión"),
        step("type_text", x=norm(640, W), y=norm(320, H), text="ACME S.A.", target_description="Campo Nombre"),
        step("click_xy", x=norm(640, W), y=norm(405, H), target_description="Botón Guardar", effect_class="persist"),
        step("click_xy", x=norm(575, W), y=norm(450, H), target_description="Botón Aceptar del diálogo",
             effect_class="persist"),
        {"action": "done", "reasoning": "El registro aparece", "claimed_evidence": "Registros: ACME S.A."},
    ]
    model = ScriptedModelAdapter(actions, [{"status": "verificado", "evidence": "Registros: ACME S.A. visible"}])
    browser = SecureBrowserSession(headless=True, viewport_width=W, viewport_height=H)
    runner = AgentRunner(profile, model, browser, AuditLogger(str(tmp_path / "audit")), settings=settings)

    # Captura del estado que la app publica por POST /state (canal de prueba, mismo origen).
    original_start = browser.start

    async def start_with_state_hook():
        await original_start()

        async def capture(route):
            try:
                desktop.state = json.loads(route.request.post_data or "{}")
            except ValueError:
                pass
            await route.fulfill(status=204, body="")

        await browser.page.route("**/state", capture)

    browser.start = start_with_state_hook

    result = asyncio.run(runner.run("Dar de alta el cliente ACME S.A. y guardarlo", desktop_url=url,
                                    secrets=SecretStore({"DESKTOP_PASSWORD": PASSWORD})))

    assert result.final_state == TaskState.SUCCEEDED_VERIFIED, result
    assert desktop.state.get("screen") == "form", desktop.state       # login por coordenadas + secreto
    assert desktop.state.get("records") == ["ACME S.A."], desktop.state  # formulario + modal operados
    assert desktop.state.get("ws_ok") is True                        # WS del escritorio autorizado
    assert desktop.state.get("ws_other") == "blocked"                # WS a otro puerto bloqueado
    assert browser.blocked_websockets >= 1
    assert all(req.image_bytes and req.image_bytes[:4] == b"\x89PNG" for req in model.requests)
    audit_text = "".join(p.read_text() for p in (tmp_path / "audit").glob("*.jsonl"))
    assert PASSWORD not in audit_text
    assert '"event_type": "verification_result"' in audit_text


# --------------------------------------------------------------------------- worker en subproceso
class HangingServer:
    """Acepta conexiones TCP y nunca responde: simula un escritorio bloqueado."""

    def __init__(self):
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self._loop)

        async def handler(reader, writer):
            await asyncio.sleep(3600)

        async def main():
            srv = await asyncio.start_server(handler, "127.0.0.1", 0)
            self.port = srv.sockets[0].getsockname()[1]
            self._ready.set()
            async with srv:
                await srv.serve_forever()

        try:
            self._loop.run_until_complete(main())
        except Exception:
            pass

    def start(self):
        self.thread.start()
        self._ready.wait(5)


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible en este entorno")
def test_subprocess_worker_is_killed_at_deadline(tmp_path):
    import os
    import time

    from web_agent.core.result import TaskState
    from web_agent.service.supervisor import Supervisor
    from web_agent.service.task_store import InMemoryTaskStore, TaskRecord
    from web_agent.service.worker import SubprocessWorker, TaskJob
    from web_agent.core.deadline import Deadline

    hang = HangingServer()
    hang.start()
    settings = Settings(model_provider="scripted", allow_loopback_desktop=True, worker_mode="subprocess",
                        audit_dir=str(tmp_path / "audit"), task_timeout_seconds=6, cleanup_timeout_seconds=2)
    store = InMemoryTaskStore()
    workers = []

    def factory(job, s, cb):
        w = SubprocessWorker(job, s, cb)
        workers.append(w)
        return w

    async def scenario():
        sup = Supervisor(store, settings, factory)
        deadline = Deadline.start(6)
        store.create(TaskRecord(task_id="task_" + "1" * 20, state=TaskState.QUEUED, deadline=deadline))
        sup.submit(TaskJob(task_id="task_" + "1" * 20, objective="x", desktop_url=f"http://127.0.0.1:{hang.port}/",
                           deadline_started=deadline.started_at, deadline_expires=deadline.expires_at,
                           password="pw-secret-1"))
        t0 = time.monotonic()
        while not store.get("task_" + "1" * 20).terminal and time.monotonic() - t0 < 20:
            await asyncio.sleep(0.1)
        published = time.monotonic() - t0
        await asyncio.gather(*sup._tasks, return_exceptions=True)
        return published

    published_after = asyncio.run(scenario())
    record = store.get("task_" + "1" * 20)
    assert record.state == TaskState.TIMED_OUT and record.public_status == "fallida"
    assert published_after < 7.5  # el estado se publica al vencer el plazo, no tras la limpieza
    proc = workers[0]._proc
    assert not proc.is_alive()
    if os.name == "posix" and hasattr(os, "killpg"):
        with pytest.raises(ProcessLookupError):
            os.killpg(proc.pid, 0)  # no quedan procesos del grupo (driver/Chromium)
