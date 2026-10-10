"""Visor noVNC con Chromium real: conexión con contraseña y guardián de WebSocket nativo."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from web_agent.browser.novnc import connect_viewer
from web_agent.browser.session import SecureBrowserSession
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.schemas import build_desktop_profile

pytestmark = pytest.mark.browser
PASSWORD = "Escr1torio-77"

# Imita la estructura de noVNC vnc.html: botón Conectar, diálogo de credenciales y la clase
# noVNC_connected en <html>. Comprueba el WebSocket igual que noVNC (Websock.attach).
PAGE = """<!doctype html><html class="noVNC_disconnected"><body>
<div id="noVNC_control_bar"></div>
<button id="noVNC_connect_button">Connect</button>
<div id="dlg" style="display:none"><input id="noVNC_password_input" type="password">
<button id="noVNC_credentials_button">Send</button></div>
<script>
window.attempts = 0;
document.getElementById('noVNC_connect_button').onclick = () => {
  document.getElementById('noVNC_connect_button').style.display = 'none';
  document.getElementById('dlg').style.display = 'block';
};
document.getElementById('noVNC_credentials_button').onclick = () => {
  window.attempts++;
  if (document.getElementById('noVNC_password_input').value !== %(pw)r) return;
  const ws = new WebSocket(`ws://${location.host}/websockify`, ['binary']);
  for (const p of ['send', 'close', 'binaryType', 'onerror', 'onmessage', 'onopen', 'protocol', 'readyState'])
    if (!(p in ws)) throw new Error('Raw channel missing property: ' + p);
  window.nativeWs = Object.getPrototypeOf(ws) === WebSocket.prototype && String(ws.url).includes('/websockify');
  document.getElementById('dlg').style.display = 'none';
  document.documentElement.className = 'noVNC_connected';
};
</script></body></html>"""


class _Server:
    def __init__(self, password):
        html = (PAGE % {"pw": password}).encode()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


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


async def _session_for(url):
    profile = build_desktop_profile(task_id="v", desktop_url=url, allow_private_desktop=False,
                                    allow_loopback_desktop=True, max_steps=3)
    s = SecureBrowserSession(network_validator=SecurityPolicyEnforcer(profile).network_validator)
    await s.start()
    await s.page.goto(url)
    return s


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible")
def test_connects_with_password_and_keeps_native_websocket():
    srv = _Server(PASSWORD)
    url = f"http://127.0.0.1:{srv.port}/"

    async def main():
        s = await _session_for(url)
        try:
            status = await connect_viewer(s.page, PASSWORD, timeout=20, session=s)
            native = await s.page.evaluate("() => window.nativeWs === true")
            exfil = await s.page.evaluate(
                "() => { const w = new WebSocket('ws://127.0.0.1:9/exfil'); return w.url; }")
            await asyncio.sleep(0.3)
            return status, native, exfil, s.blocked_websockets
        finally:
            await s.stop()

    status, native, exfil, blocked = asyncio.run(main())
    srv.httpd.shutdown()
    assert status == "connected"
    assert native  # noVNC necesita el objeto WebSocket nativo
    assert exfil.startswith("wss://blocked.invalid") and blocked >= 1


@pytest.mark.skipif(not _launchable(), reason="Chromium no disponible")
def test_wrong_password_is_reported_without_retrying_forever():
    srv = _Server("otra-clave")
    url = f"http://127.0.0.1:{srv.port}/"

    async def main():
        s = await _session_for(url)
        try:
            status = await connect_viewer(s.page, PASSWORD, timeout=20, session=s)
            return status, await s.page.evaluate("() => window.attempts")
        finally:
            await s.stop()

    status, attempts = asyncio.run(main())
    srv.httpd.shutdown()
    assert status == "auth_failed" and attempts == 2
