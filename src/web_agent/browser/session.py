"""Sesión Playwright por tarea (documento maestro §7, §11.4, §11.6).

- Un ``BrowserContext`` nuevo por tarea, sin descargas ni service workers.
  No es aislamiento de proceso: el worker y el despliegue aportan el resto.
- Toda petición HTTP(S) pasa por la política de red única.
- Los WebSockets solo se conectan hacia el escritorio autorizado; el resto se
  cierra. Antes se cerraban todos, lo que impedía noVNC/Guacamole.
- Identidad de página y época de navegación para invalidar observaciones.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


class SecureBrowserSession:
    def __init__(
        self,
        headless: bool = True,
        chromium_sandbox: bool = True,
        network_validator=None,
        *,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        close_timeout: float = 2.0,
    ):
        self.headless = headless
        self.chromium_sandbox = chromium_sandbox
        self.network_validator = network_validator
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.close_timeout = close_timeout
        self.session_id = f"ses_{uuid.uuid4().hex[:16]}"
        self.page_id = ""
        self.navigation_epoch = 0
        self.blocked_requests = 0
        self.blocked_websockets = 0
        self.dialogs_dismissed = 0
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self):
        if self.network_validator is None:
            raise RuntimeError("La política de red es obligatoria.")
        if self._playwright is not None:
            raise RuntimeError("La sesión ya está iniciada.")
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                chromium_sandbox=self.chromium_sandbox,
            )
            context_kwargs = {
                "accept_downloads": False,
                "ignore_https_errors": False,
                "service_workers": "block",
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                "device_scale_factor": 1,
            }
            if self.network_validator.proxy_options:
                context_kwargs["proxy"] = self.network_validator.proxy_options
            self._context = await self._browser.new_context(**context_kwargs)

            validator = self.network_validator

            async def route_handler(route):
                try:
                    allowed = await validator.is_request_allowed(route.request.url)
                except Exception:
                    allowed = False
                if allowed:
                    await route.continue_()
                else:
                    self.blocked_requests += 1
                    await route.abort("blockedbyclient")

            async def ws_handler(ws):
                try:
                    allowed = await validator.is_websocket_request_allowed(ws.url)
                except Exception:
                    allowed = False
                if allowed:
                    ws.connect_to_server()
                else:
                    self.blocked_websockets += 1
                    await ws.close(code=1008, reason="blocked by policy")

            await self._context.route("**/*", route_handler)
            await self._context.route_web_socket("**/*", ws_handler)
            self.page = await self._context.new_page()
            self.page_id = f"pag_{uuid.uuid4().hex[:16]}"
            self.page.on("framenavigated", self._on_frame_navigated)
            self.page.on("dialog", self._on_dialog)
        except BaseException:
            try:
                await self.stop()
            except Exception:
                pass
            raise

    def _on_frame_navigated(self, frame) -> None:
        if self.page is not None and frame == self.page.main_frame:
            self.navigation_epoch += 1

    def _on_dialog(self, dialog) -> None:
        # Diálogos JS nativos bloquean la página. Se descartan (nunca se aceptan)
        # y se registra el evento; aceptar podría confirmar una operación (§11.5).
        self.dialogs_dismissed += 1
        asyncio.ensure_future(dialog.dismiss())

    async def stop(self):
        resources = (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        )
        self.page = None
        self._context = None
        self._browser = None
        self._playwright = None
        failed = False
        for resource, method in resources:
            if resource is None:
                continue
            try:
                await asyncio.wait_for(getattr(resource, method)(), timeout=self.close_timeout)
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("No se pudo confirmar el cierre completo de la sesión.")
