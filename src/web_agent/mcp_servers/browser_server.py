import asyncio
import json
import os
import uuid

from mcp.server import MCPServer
from playwright.async_api import async_playwright, expect

from web_agent.core.errors import PolicyViolationError
from web_agent.core.policy import SecurityPolicyEnforcer
from web_agent.core.schemas import ActionType, ProposedAction, load_profile
from web_agent.security.redaction import DataRedactor

mcp = MCPServer("restricted-browser")
CONFIG = os.environ.get("FRONTIER_CONFIG", "profiles/examples/portal_demo.yaml")


class RestrictedBrowser:
    def __init__(self):
        self.profile = load_profile(CONFIG)
        self.policy_enforcer = SecurityPolicyEnforcer(self.profile)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.observation_id = None
        self.registry = {}
        self.blocked_network = False

    async def start(self):
        if self.page is not None:
            return

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.profile.browser.headless,
                chromium_sandbox=self.profile.browser.chromium_sandbox,
            )
            self.context = await self.browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers="block",
            )
            self.context.set_default_timeout(self.profile.execution.action_timeout_ms)

            async def route_handler(route):
                try:
                    if self.policy_enforcer.network_validator.is_url_allowed(route.request.url):
                        await route.continue_()
                    else:
                        self.blocked_network = True
                        await route.abort("blockedbyclient")
                except Exception:
                    self.blocked_network = True
                    await route.abort("blockedbyclient")

            await self.context.route("**/*", route_handler)

            self.page = await self.context.new_page()
            self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))

            async def close_extra(page):
                if page != self.page:
                    await page.close()

            self.context.on("page", lambda page: asyncio.create_task(close_extra(page)))

        except BaseException:
            await self.close()
            raise

    async def close(self):
        for obj, method in (
            (self.context, "close"),
            (self.browser, "close"),
            (self.playwright, "stop"),
        ):
            if obj is not None:
                try:
                    await asyncio.wait_for(getattr(obj, method)(), timeout=2.0)
                except Exception:
                    pass

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    async def verify_success(self) -> bool:
        condition = self.profile.verification.success_condition
        if condition is None or self.page is None or self.page.is_closed():
            return False

        if not self.policy_enforcer.network_validator.is_url_allowed(self.page.url):
            return False

        if condition.url_equals and self.page.url != condition.url_equals:
            return False

        if condition.visible_text:
            locator = self.page.get_by_text(condition.visible_text, exact=True)
            if await locator.count() != 1 or not await locator.is_visible():
                return False

        if condition.element_role:
            locator = self.page.get_by_role(
                condition.element_role,
                name=condition.element_name,
                exact=True,
            )
            if await locator.count() != 1 or not await locator.is_visible():
                return False

        return True

    async def observe(self) -> dict:
        await self.start()
        if self.blocked_network:
            raise PolicyViolationError("Se bloqueó tráfico no autorizado.")

        if not self.profile.permissions.read_visible_data:
            return {
                "observation_id": uuid.uuid4().hex,
                "url": "about:blank",
                "title": "Observación deshabilitada por permisos",
                "elements": [],
                "visible_text": "",
            }

        self.observation_id = uuid.uuid4().hex
        self.registry = {}

        title = await self.page.title()
        text = await self.page.locator("body").inner_text() if self.page.url != "about:blank" else ""

        return {
            "observation_id": self.observation_id,
            "url": DataRedactor.redact(self.page.url),
            "title": DataRedactor.redact(title[:300]),
            "elements": [],
            "visible_text": DataRedactor.redact(text[:10_000]),
            "truncated": len(text) > 10_000,
        }

    async def execute(self, proposed_dict: dict) -> dict:
        action = ProposedAction.model_validate(proposed_dict)
        self.policy_enforcer.validate_action(action)
        await self.start()

        if self.blocked_network:
            raise PolicyViolationError("Tráfico no autorizado.")

        if action.action_type == ActionType.DONE:
            verified = await self.verify_success()
            return {"status": "verified" if verified else "failed"}

        if action.action_type == ActionType.NAVIGATE:
            response = await self.page.goto(
                action.url,
                wait_until="domcontentloaded",
                timeout=self.profile.execution.action_timeout_ms,
            )
            self.observation_id = None

            if self.blocked_network:
                raise PolicyViolationError("Navegación bloqueada por política de red.")

            if response is None or response.status >= 400:
                return {"status": "failed", "reason": "HTTP status >= 400"}

            return {"status": "verified"}

        raise PolicyViolationError(f"Operación {action.action_type.value} no implementada.")


browser = RestrictedBrowser()


@mcp.tool()
async def observe() -> str:
    try:
        async with asyncio.timeout(30):
            res = await browser.observe()
            return json.dumps({"ok": True, "observation": res})
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "blocked": isinstance(exc, PolicyViolationError),
            "error_type": type(exc).__name__,
        })


@mcp.tool()
async def execute(action_json: str) -> str:
    try:
        async with asyncio.timeout(30):
            proposed = json.loads(action_json)
            result = await browser.execute(proposed)
        return json.dumps({"ok": True, "result": result})
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "blocked": isinstance(exc, PolicyViolationError),
            "error_type": type(exc).__name__,
        })


@mcp.tool()
async def close() -> str:
    await browser.close()
    return json.dumps({"ok": True})


if __name__ == "__main__":
    mcp.run(transport="stdio")
