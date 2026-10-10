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
            self._context = await self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers="block",
            )

            # Intercept and block unauthorized requests at the browser level
            async def route_handler(route):
                if self.network_validator.is_url_allowed(route.request.url):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")

            await self._context.route("**/*", route_handler)
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

        for resource, method in resources:
            if resource is None:
                continue

            try:
                async with asyncio.timeout(5):
                    await getattr(resource, method)()
            except Exception:
                logger.error("No se pudo cerrar completamente un recurso del navegador.")
