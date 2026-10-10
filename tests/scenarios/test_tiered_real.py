"""Motor tri-nivel de extremo a extremo: Chromium real + OCR real (RapidOCR) sobre un lienzo.

La aplicación se dibuja íntegramente en <canvas> (como un escritorio remoto): no hay
DOM de controles que leer. El Nivel 3 tiene que localizar los textos con OCR sobre
la captura real; el Nivel 2 rellena el formulario en ráfaga. Se omite si no están
instalados el extra ``ocr`` o Chromium.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("rapidocr")
pytest.importorskip("onnxruntime")

from web_agent.audit.events import AuditLogger  # noqa: E402
from web_agent.browser.session import SecureBrowserSession  # noqa: E402
from web_agent.config import Settings  # noqa: E402
from web_agent.core.result import TaskState  # noqa: E402
from web_agent.core.runner import AgentRunner  # noqa: E402
from web_agent.core.schemas import build_desktop_profile  # noqa: E402
from web_agent.models.adapters.scripted import ScriptedModelAdapter  # noqa: E402
from web_agent.perception.ocr_engine import RapidOcrEngine  # noqa: E402

pytestmark = pytest.mark.browser
W, H = 1280, 800

APP = """<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;overflow:hidden}</style></head>
<body><canvas id="c"></canvas><script>
const DANGER = %(danger)s;
const c = document.getElementById('c'); c.width = innerWidth; c.height = innerHeight;
const g = c.getContext('2d');
const S = {screen:'home', name:'', focus:null, deleted:false};
const B = {clientes:[80,120,160,44], eliminar:[80,168,170,44], facturacion:[80,240,160,44],
           nuevo:[420,160,140,44], name:[420,260,360,40]};
const inR=(r,x,y)=>x>=r[0]&&x<r[0]+r[2]&&y>=r[1]&&y<r[1]+r[3];
function report(){ fetch('/state',{method:'POST',body:JSON.stringify(S)}).catch(()=>{}); }
function btn(r,label){ g.fillStyle='#e7e5e4'; g.fillRect(...r); g.strokeStyle='#57534e'; g.strokeRect(...r);
  g.fillStyle='#1c1917'; g.font='20px sans-serif'; g.fillText(label,r[0]+14,r[1]+29); }
function draw(){
  g.fillStyle='#fafaf9'; g.fillRect(0,0,c.width,c.height);
  g.fillStyle='#1c1917'; g.font='bold 22px sans-serif'; g.fillText('Sistema de Gestión', 80, 70);
  btn(B.clientes,'Clientes'); btn(B.facturacion,'Facturación');
  if (DANGER) btn(B.eliminar,'Eliminar todo');
  if (S.screen==='clientes'||S.screen==='alta'){ g.font='22px sans-serif'; g.fillStyle='#1c1917';
    g.fillText('Listado de clientes', 420, 130); btn(B.nuevo,'Nuevo'); }
  if (S.screen==='alta'){ g.font='20px sans-serif'; g.fillStyle='#1c1917'; g.fillText('Nombre', 420, 250);
    g.fillStyle='#fff'; g.fillRect(...B.name); g.strokeStyle=S.focus==='name'?'#1c1917':'#a8a29e';
    g.lineWidth=S.focus==='name'?2:1; g.strokeRect(...B.name); g.lineWidth=1;
    g.fillStyle='#1c1917'; g.fillText(S.name, B.name[0]+10, B.name[1]+27); }
  report();
}
c.addEventListener('mousedown', e=>{ const x=e.clientX, y=e.clientY;
  if (inR(B.clientes,x,y)) S.screen='clientes';
  else if (DANGER && inR(B.eliminar,x,y)) S.deleted=true;
  else if ((S.screen==='clientes'||S.screen==='alta') && inR(B.nuevo,x,y)) S.screen='alta';
  else if (S.screen==='alta' && inR(B.name,x,y)) S.focus='name';
  else S.focus=null;
  draw(); });
addEventListener('keydown', e=>{ if (S.focus!=='name') return;
  if (e.key.length===1) S.name+=e.key; else if (e.key==='Backspace') S.name=S.name.slice(0,-1);
  e.preventDefault(); draw(); });
draw();
</script></body></html>"""


class _App:
    def __init__(self, danger: bool):
        self.state: dict = {}
        app = self
        html = (APP % {"danger": "true" if danger else "false"}).encode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    app.state = json.loads(body or b"{}")
                except ValueError:
                    pass
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ocr():  # motor propio por prueba: sin bloqueos heredados de un análisis anterior
    try:
        return RapidOcrEngine(threads=2)
    except Exception as exc:  # modelos no descargables en este entorno
        pytest.skip(f"OCR no disponible: {type(exc).__name__}")


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


def norm(px: int, size: int) -> int:
    return round(px * 1000 / size)


def _run(app, ocr, tmp_path, objective, actions):
    url = f"http://127.0.0.1:{app.port}/vnc.html"
    settings = Settings(model_provider="scripted", allow_loopback_desktop=True, audit_dir=str(tmp_path / "audit"),
                        typing_delay_ms=5, viewport_width=W, viewport_height=H, level3_timeout_seconds=30.0)  # funcionalidad, no rendimiento
    profile = build_desktop_profile(task_id="tri", desktop_url=url, allow_private_desktop=False,
                                    allow_loopback_desktop=True, max_steps=12)
    model = ScriptedModelAdapter(actions, [{"status": "verificado", "evidence": "Formulario rellenado."}])
    browser = SecureBrowserSession(headless=True, viewport_width=W, viewport_height=H)
    runner = AgentRunner(profile, model, browser, AuditLogger(str(tmp_path / "audit")), settings=settings,
                         ocr_engine=ocr)
    result = asyncio.run(runner.run(objective, desktop_url=url))
    audit = [json.loads(line) for p in (tmp_path / "audit").glob("*.jsonl") for line in p.read_text().splitlines()]
    return result, model, audit


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible en este entorno")
def test_level3_and_level2_on_real_canvas_with_real_ocr(ocr, tmp_path):
    app = _App(danger=False)
    try:
        form = {"macro_action": "rellenar_alta", "reasoning": "formulario de alta", "steps": [
            {"action": "click_xy", "x": norm(600, W), "y": norm(280, H), "target_description": "Campo Nombre"},
            {"action": "type_text", "text": "ACME"},
        ]}
        result, model, audit = _run(app, ocr, tmp_path, "Haz clic en 'Clientes' y luego en 'Nuevo', y escribe el "
                                    "nombre ACME", [form, {"action": "done", "reasoning": "hecho"}])
        trace = [(e["event_type"], e.get("reason_code"), (e.get("data") or {}).get("target"))
                 for e in audit if e["event_type"].startswith("level")]
        assert result.final_state == TaskState.SUCCEEDED_VERIFIED, (result, trace)
        assert app.state.get("screen") == "alta" and app.state.get("name") == "ACME", (app.state, trace)
        local = [e for e in audit if e["event_type"] == "level3_action_selected"]
        assert [e["data"]["target"] for e in local] == ["Clientes", "Nuevo"]
        assert model.call_count == 2  # macro + done: los dos clics de navegación no usaron inferencia
        burst = [e for e in audit if e["event_type"] == "level2_burst_finished"]
        assert burst and burst[0]["outcome"] == "ok" and burst[0]["data"]["executed"] == 2
    finally:
        app.close()


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible en este entorno")
def test_level3_refuses_target_next_to_destructive_control(ocr, tmp_path):
    app = _App(danger=True)
    try:
        result, model, audit = _run(app, ocr, tmp_path, "Haz clic en 'Clientes'",
                                    [{"action": "done", "reasoning": "fin"}])
        assert app.state.get("screen") == "home" and not app.state.get("deleted"), app.state
        reasons = [e["reason_code"] for e in audit if e["event_type"] == "level3_escalated"]
        assert "level3_danger_nearby" in reasons
        assert not [e for e in audit if e["event_type"] == "level3_action_selected"]
    finally:
        app.close()
