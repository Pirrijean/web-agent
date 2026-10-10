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
import json
import logging
import uuid
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


# Guardián de WebSocket en página: se ejecuta antes que cualquier script de la página.
WS_GUARD_JS = r"""(() => {
  const cfg = __CONFIG__;
  const allowed = new Set(cfg.origins);
  const Native = window.WebSocket;
  if (!Native || Native.__waGuarded) return;
  const norm = (raw) => {
    try {
      const u = new URL(String(raw), location.href);
      const scheme = u.protocol === 'wss:' ? 'https' : u.protocol === 'ws:' ? 'http' : null;
      if (!scheme) return null;
      const port = u.port || (scheme === 'https' ? '443' : '80');
      return { origin: `${scheme}://${u.hostname.toLowerCase()}:${port}`, path: u.pathname || '/' };
    } catch (e) { return null; }
  };
  const ok = (raw) => {
    const n = norm(raw);
    if (!n || !allowed.has(n.origin)) return false;
    return !cfg.prefixes.length || cfg.prefixes.some((p) => n.path.startsWith(p));
  };
  const Guarded = new Proxy(Native, {
    construct(target, args, newTarget) {
      if (!ok(args[0])) {
        try { window.__waWsBlocked && window.__waWsBlocked(String(args[0]).slice(0, 200)); } catch (e) {}
        return Reflect.construct(target, ['wss://blocked.invalid/'], newTarget);  // falla sin tráfico útil
      }
      return Reflect.construct(target, args, newTarget);
    },
    get(target, prop, receiver) { return prop === '__waGuarded' ? true : Reflect.get(target, prop, receiver); },
  });
  Object.defineProperty(window, 'WebSocket', { value: Guarded, writable: false, configurable: false });
})();"""

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
        websocket_mode: str = "native_guard",
    ):
        self.headless = headless
        self.chromium_sandbox = chromium_sandbox
        self.websocket_mode = websocket_mode  # "native_guard" (compatible con noVNC) | "route"
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
            launch_args = []
            hosts = self.network_validator.resolver_hosts() if hasattr(self.network_validator, "resolver_hosts") else []
            if hosts:
                # Egreso a nivel de navegador (todas las vías: páginas, iframes, workers): solo los
                # hosts autorizados resuelven DNS.
                excludes = ", ".join(f"EXCLUDE {h}" for h in hosts)
                launch_args.append(f"--host-resolver-rules=MAP * ~NOTFOUND, {excludes}")
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                chromium_sandbox=self.chromium_sandbox,
                args=launch_args,
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
            if self.websocket_mode == "route":
                await self._context.route_web_socket("**/*", ws_handler)
            else:
                # route_web_socket sustituye window.WebSocket por un objeto simulado que noVNC
                # rechaza ("Raw channel missing property: send"). El guardián en página conserva el
                # WebSocket nativo para el origen autorizado y desvía cualquier otro destino.
                async def report_blocked(source, url):
                    self.blocked_websockets += 1

                await self._context.expose_binding("__waWsBlocked", report_blocked)
                config = json.dumps(validator.websocket_guard_config())
                await self._context.add_init_script(WS_GUARD_JS.replace("__CONFIG__", config))
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

    def expect_password_prompt(self, secret) -> None:
        """Durante la conexión del visor (vnc_lite), el ``prompt()`` de contraseña se
        responde una vez con el secreto. Fuera de esa ventana, nada se acepta."""
        self._prompt_secret = secret or None

    def _on_dialog(self, dialog) -> None:
        # Diálogos JS nativos bloquean la página. Se descartan (nunca se aceptan)
        # y se registra el evento; aceptar podría confirmar una operación (§11.5).
        secret = getattr(self, "_prompt_secret", None)
        if (secret and dialog.type == "prompt" and "password" in (dialog.message or "").lower()
                and self.page is not None and self._is_desktop_page(self.page.url)):
            self._prompt_secret = None
            asyncio.ensure_future(dialog.accept(secret))
            return
        self.dialogs_dismissed += 1
        asyncio.ensure_future(dialog.dismiss())

    def _is_desktop_page(self, url: str) -> bool:
        try:
            return bool(self.network_validator) and self.network_validator.is_url_allowed(url)
        except Exception:
            return False

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
