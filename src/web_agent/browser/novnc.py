"""Conexión del visor noVNC (cliente VNC que corre en el navegador del agente).

El escritorio del evaluador se entrega como ``wss://host/websockify`` y en la raíz del
mismo origen se sirve noVNC. Este módulo conecta el VISOR, no opera la aplicación
legada: solo toca selectores conocidos de noVNC (``vnc.html``) y, para ``vnc_lite``,
el ``prompt()`` de contraseña. La aplicación remota sigue operándose únicamente por
capturas y coordenadas a través de validador, política y ejecutor.

Si la página no es noVNC (otro visor), devuelve ``not_novnc`` y el bucle visual se
encarga (el modelo ve el diálogo y usa ``secret_ref``).
"""

from __future__ import annotations

import asyncio
from typing import Optional

CONNECTED_JS = """() => {
  const html = document.documentElement;
  if (html.classList.contains('noVNC_connected')) return 'connected';
  const st = document.getElementById('status') || document.getElementById('noVNC_status');
  if (st && /connected|conectado/i.test(st.textContent || '') && !/dis|fail|error/i.test(st.textContent || ''))
    return 'connected';
  if (html.classList.contains('noVNC_disconnected') || document.getElementById('noVNC_control_bar')) return 'novnc';
  if (st || window.RFB || document.querySelector('script[src*="novnc"], script[src*="rfb"]')) return 'novnc';
  return 'unknown';
}"""

CONNECT_BUTTON = "#noVNC_connect_button"
PASSWORD_INPUT = "#noVNC_password_input"
CREDENTIALS_BUTTON = "#noVNC_credentials_button"


async def _visible(page, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=200)
    except Exception:
        return False


async def connect_viewer(page, password: Optional[str], *, timeout: float = 15.0, session=None) -> str:
    """Conecta noVNC. Devuelve ``connected``, ``not_novnc``, ``auth_failed`` o ``timeout``."""
    if not hasattr(page, "evaluate") or not hasattr(page, "locator"):
        return "not_novnc"
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    if session is not None and password:
        session.expect_password_prompt(password)  # vnc_lite usa prompt()
    clicked_connect = False
    credential_attempts = 0
    unknown_since: Optional[float] = None
    try:
        while loop.time() < end:
            try:
                status = await page.evaluate(CONNECTED_JS)
            except Exception:
                status = "unknown"
            if status == "connected":
                return "connected"
            if await _visible(page, PASSWORD_INPUT):
                if not password or credential_attempts >= 2:
                    return "auth_failed"
                await page.locator(PASSWORD_INPUT).first.fill(password, timeout=2000)
                if await _visible(page, CREDENTIALS_BUTTON):
                    await page.locator(CREDENTIALS_BUTTON).first.click(timeout=2000)
                else:
                    await page.locator(PASSWORD_INPUT).first.press("Enter", timeout=2000)
                credential_attempts += 1
                await asyncio.sleep(0.8)
                continue
            if not clicked_connect and await _visible(page, CONNECT_BUTTON):
                await page.locator(CONNECT_BUTTON).first.click(timeout=2000)
                clicked_connect = True
                await asyncio.sleep(0.5)
                continue
            if status == "unknown":
                unknown_since = unknown_since or loop.time()
                if loop.time() - unknown_since > 1.0:
                    return "not_novnc"
            await asyncio.sleep(0.25)
        return "timeout"
    finally:
        password = None
        if session is not None:
            session.expect_password_prompt(None)
