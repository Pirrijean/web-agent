import asyncio
import logging
from typing import Optional
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


class SecureBrowserSession:
    """Gestiona el ciclo de vida de un navegador aislado (Fase 1)."""

    def __init__(
        self,
        headless: bool = True,
        chromium_sandbox: bool = True,
        network_validator=None,
    ):
        self.headless = headless
        self.chromium_sandbox = chromium_sandbox
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.network_validator = network_validator

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

            # Aislamiento: Nuevo contexto, cero descargas por defecto para mitigar malware.
            # Desactivamos service_workers porque pueden saltarse intercepciones
            context_kwargs = {
                "accept_downloads": False,
                "ignore_https_errors": False,
                "service_workers": "block",
            }
            if self.network_validator.proxy_options:
                context_kwargs["proxy"] = self.network_validator.proxy_options

            self._context = await self._browser.new_context(**context_kwargs)

            # Intercept and block unauthorized requests at the browser level
            async def route_handler(route):
                req = route.request
                if req.resource_type == "websocket":
                    await route.abort("blockedbyclient")
                    return
                try:
                    if await self.network_validator.is_request_allowed(req.url):
                        await route.continue_()
                    else:
                        await route.abort("blockedbyclient")
                except Exception:
                    await route.abort("blockedbyclient")

            await self._context.route("**/*", route_handler)
            if hasattr(self._context, "route_web_socket"):
                try:
                    await self._context.route_web_socket("**/*", lambda ws: ws.close())
                except Exception:
                    pass
            self.page = await self._context.new_page()
        except BaseException:
            await self.stop()
            raise

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
        try:
            for resource, method in resources:
                if resource is None:
                    continue
                try:
                    await asyncio.wait_for(
                        getattr(resource, method)(),
                        timeout=2.0,
                    )
                except Exception:
                    failed = True
        finally:
            pass

        if failed:
            raise RuntimeError(
                "No se pudo confirmar el cierre completo de la sesión."
            )
