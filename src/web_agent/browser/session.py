from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from typing import Optional

class SecureBrowserSession:
    """Gestiona el ciclo de vida de un navegador aislado (Fase 1)."""
    
    def __init__(self, headless: bool = True, network_validator=None):
        self.headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.network_validator = network_validator

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        
        # Aislamiento: Nuevo contexto, cero descargas por defecto para mitigar malware.
        # Desactivamos service_workers porque pueden saltarse intercepciones
        self._context = await self._browser.new_context(
            accept_downloads=False,
            ignore_https_errors=False,
            service_workers="block"
        )
        
        # Intercept and block unauthorized requests at the browser level
        if self.network_validator:
            async def route_handler(route):
                if self.network_validator.is_url_allowed(route.request.url):
                    await route.continue_()
                else:
                    await route.abort("blockedbyclient")
            await self._context.route("**/*", route_handler)
            
        self.page = await self._context.new_page()

    async def stop(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
