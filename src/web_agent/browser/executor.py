"""Ejecutor controlado: única capa que emite eventos de interfaz (§8.10, §10.3).

Recibe acciones ya validadas y autorizadas. Cada manejador:
1. comprueba de nuevo el deadline;
2. emite solo los eventos definidos por código (sin ``eval`` ni código del modelo);
3. libera teclas modificadoras en ``finally``;
4. devuelve un resultado controlado o lanza un error tipado.

Los secretos se resuelven aquí, justo antes de escribir, y nunca se devuelven.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from web_agent.core.action_validator import LiveGeometry, ValidatedAction
from web_agent.core.context import SecretStore, TaskContext
from web_agent.core.errors import (
    ActionNotVerifiedError,
    AmbiguousTargetError,
    ExecutionError,
    PolicyViolationError,
    TargetNotFoundError,
)
from web_agent.core.schemas import ActionType, TaskProfile
from web_agent.security.network_policy import NetworkPolicyValidator

_MODIFIERS = ("Shift", "Control", "Alt", "Meta")


@dataclass(frozen=True)
class ExecutionOutcome:
    detail: str
    extracted: Optional[str] = None


class Executor:
    def __init__(
        self,
        session,
        profile: TaskProfile,
        network_validator: NetworkPolicyValidator,
        secrets: SecretStore,
        *,
        typing_delay_ms: int = 20,
        action_timeout_seconds: float = 15.0,
    ):
        self.session = session
        self.profile = profile
        self.network_validator = network_validator
        self.secrets = secrets
        self.typing_delay_ms = typing_delay_ms
        self.action_timeout_seconds = action_timeout_seconds

    # ----------------------------------------------------------- geometría
    def _page(self):
        page = self.session.page
        if page is None or page.is_closed():
            raise ExecutionError("No existe página activa.")
        return page

    async def live_geometry(self) -> Optional[LiveGeometry]:
        page = self.session.page
        if page is None or page.is_closed():
            return None
        vp = page.viewport_size or {}
        return LiveGeometry(
            page_url=page.url,
            page_id=self.session.page_id,
            navigation_epoch=self.session.navigation_epoch,
            viewport_width_css=int(vp.get("width", 0)),
            viewport_height_css=int(vp.get("height", 0)),
        )

    # ----------------------------------------------------------- ejecución
    async def execute(self, validated: ValidatedAction, ctx: TaskContext) -> ExecutionOutcome:
        ctx.deadline.check(margin=0.5)
        timeout = ctx.deadline.timeout_for(self.action_timeout_seconds, margin=0.5)
        a = validated.action
        t = a.action
        handler = {
            ActionType.NAVIGATE: self._navigate,
            ActionType.CLICK_XY: self._click,
            ActionType.DOUBLE_CLICK: self._click,
            ActionType.RIGHT_CLICK: self._click,
            ActionType.TYPE_TEXT: self._type_text,
            ActionType.PRESS_KEY: self._press_key,
            ActionType.SCROLL: self._scroll,
            ActionType.WAIT: self._wait,
            ActionType.CLICK_DOM: self._click_dom,
            ActionType.FILL_DOM: self._fill_dom,
            ActionType.EXTRACT: self._extract,
        }.get(t)
        if handler is None:
            raise PolicyViolationError(f"Acción no ejecutable: {t.value}")
        return await asyncio.wait_for(handler(validated, ctx, timeout), timeout=timeout + 0.25)

    @staticmethod
    async def _release_modifiers(page, modifiers) -> None:
        """Libera modificadores pulsados por esta acción si se interrumpió a medias."""
        for mod in modifiers:
            try:
                await page.keyboard.up(mod)
            except Exception:
                pass

    async def _navigate(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        page = self._page()
        if not self.network_validator.is_url_allowed(v.action.url):
            raise PolicyViolationError("Destino no autorizado.")
        response = await page.goto(v.action.url, wait_until="domcontentloaded", timeout=timeout * 1000)
        if not self.network_validator.is_url_allowed(page.url):
            raise PolicyViolationError("Destino final no autorizado.")
        if response is not None and not 200 <= response.status < 400:
            raise ActionNotVerifiedError("Respuesta HTTP no satisfactoria.")
        return ExecutionOutcome(detail="navegación completada")

    async def _click(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        page = self._page()
        t = v.action.action
        button = "right" if t == ActionType.RIGHT_CLICK else "left"
        count = 2 if t == ActionType.DOUBLE_CLICK else 1
        await page.mouse.move(v.x_css, v.y_css)
        await asyncio.sleep(0.05)
        await page.mouse.click(v.x_css, v.y_css, button=button, click_count=count)
        return ExecutionOutcome(detail=f"{t.value} en ({v.x_css},{v.y_css}) css")

    async def _type_text(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        page = self._page()
        a = v.action
        if a.secret_ref is not None:
            if a.secret_ref not in self.profile.security.allowed_secret_names:
                raise PolicyViolationError("Referencia de secreto no autorizada.")
            try:
                text = self.secrets.resolve(a.secret_ref)
            except KeyError:
                raise PolicyViolationError("Secreto no disponible.") from None
        else:
            text = a.text or ""
        # La cadencia no puede exceder el tiempo disponible.
        delay = self.typing_delay_ms
        if delay and len(text) * delay / 1000.0 > max(0.5, timeout - 1.0):
            delay = max(0, int((timeout - 1.0) * 1000 / max(1, len(text))))
        try:
            if v.x_css is not None:
                await page.mouse.click(v.x_css, v.y_css)
                await asyncio.sleep(0.1)
            if a.clear_before:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.05)
            await page.keyboard.type(text, delay=delay)
        except BaseException:
            if a.clear_before:
                await self._release_modifiers(page, ("Control",))
            raise
        finally:
            text = ""
        where = f" en ({v.x_css},{v.y_css})" if v.x_css is not None else ""
        what = f"secreto {a.secret_ref}" if a.secret_ref else f"{len(a.text or '')} caracteres"
        return ExecutionOutcome(detail=f"escritura de {what}{where}")

    async def _press_key(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        page = self._page()
        key = v.action.key
        mods = tuple(m for m in key.split("+")[:-1] if m in _MODIFIERS)
        try:
            await page.keyboard.press(key)
        except BaseException:
            await self._release_modifiers(page, mods)
            raise
        return ExecutionOutcome(detail=f"tecla {v.action.key}")

    async def _scroll(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        page = self._page()
        if v.x_css is not None:
            await page.mouse.move(v.x_css, v.y_css)
        await page.mouse.wheel(v.action.delta_x or 0, v.action.delta_y or 0)
        return ExecutionOutcome(detail=f"scroll ({v.action.delta_x or 0},{v.action.delta_y or 0})")

    async def _wait(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        duration = min(float(v.action.duration_seconds), max(0.0, timeout - 0.1))
        await asyncio.sleep(duration)
        return ExecutionOutcome(detail=f"espera de {duration:.1f}s")

    # ------------------------------------------------------------- DOM
    async def _locate(self, v: ValidatedAction, timeout: float):
        page = self._page()
        el = v.element
        if el is None:
            raise TargetNotFoundError("Elemento no disponible.")
        locator = page.get_by_role(el.role, name=el.name, exact=True)
        count = await locator.count()
        if count == 0 and el.role in ("textbox", "searchbox", "combobox", "spinbutton"):
            locator = page.get_by_label(el.name, exact=True)
            count = await locator.count()
        if count == 0:
            raise TargetNotFoundError("El elemento ya no existe.")
        if count != 1:
            raise AmbiguousTargetError("El elemento no es único.")
        if not await locator.is_visible():
            raise TargetNotFoundError("El elemento no es visible.")
        return locator

    async def _click_dom(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        locator = await self._locate(v, timeout)
        if await locator.is_disabled():
            raise ExecutionError("Elemento deshabilitado.")
        await locator.click(timeout=timeout * 1000)
        return ExecutionOutcome(detail=f"click_dom {v.element.role} '{v.element.name[:60]}'")

    async def _fill_dom(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        a = v.action
        locator = await self._locate(v, timeout)
        if await locator.is_disabled() or not await locator.is_editable():
            raise ExecutionError("Campo no editable.")
        if a.secret_ref is not None:
            if a.secret_ref not in self.profile.security.allowed_secret_names:
                raise PolicyViolationError("Referencia de secreto no autorizada.")
            value = self.secrets.resolve(a.secret_ref)
        else:
            value = a.text or ""
        try:
            await locator.fill(value, timeout=timeout * 1000)
            if await locator.input_value() != value:
                raise ActionNotVerifiedError("El valor introducido no coincide con el esperado.")
        finally:
            value = ""
        return ExecutionOutcome(detail=f"fill_dom {v.element.role} '{v.element.name[:60]}'")

    async def _extract(self, v: ValidatedAction, ctx: TaskContext, timeout: float) -> ExecutionOutcome:
        locator = await self._locate(v, timeout)
        text = (await locator.inner_text(timeout=timeout * 1000))[:2000]
        return ExecutionOutcome(detail="extracción completada", extracted=text)
