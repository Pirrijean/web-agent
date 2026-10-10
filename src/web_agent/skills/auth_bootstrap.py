"""SKILL-01 DesktopAuthAndBootstrap (documento maestro §8.11).

Conecta con el escritorio autorizado y prepara la superficie de trabajo:
1. ``navigate`` al ``desktop_url`` mediante el pipeline común (política de red).
2. Espera de estabilización visual acotada: capturas sucesivas hasta que dos
   consecutivas coinciden o se agota el máximo. La diferencia de píxeles es
   aquí solo una señal de que el render terminó, no evidencia de éxito.

La autenticación se hace después, en el bucle normal: el modelo localiza el
campo y propone ``type_text`` con ``secret_ref``; el ejecutor resuelve el
secreto. La contraseña nunca entra en el prompt ni en los logs.
"""

from __future__ import annotations

import asyncio

from web_agent.core.context import RunState, TaskContext
from web_agent.core.schemas import DESKTOP_PASSWORD_SECRET, ActionType, ProposedAction
from web_agent.skills.base import Skill, SkillRuntime, SkillSpec


class DesktopAuthAndBootstrap(Skill):
    spec = SkillSpec(
        skill_id="desktop_auth_bootstrap",
        version="1.0",
        purpose="Conectar al escritorio remoto autorizado y completar el acceso inicial.",
        preconditions=("desktop_url autorizado en el perfil", "sesión de navegador iniciada"),
        postcondition="Escritorio visible y estable; acceso completado por el bucle visual.",
        risks=("Credenciales en pantalla", "Transporte WebSocket bloqueado"),
        repetition_rule="La navegación inicial se ejecuta una vez; el acceso no se reintenta más de 2 veces.",
        evidence="Hash de capturas estables y eventos de navegación auditados.",
    )

    def __init__(self, *, stability_timeout: float = 8.0, interval: float = 0.8):
        self.stability_timeout = stability_timeout
        self.interval = interval

    def applies(self, ctx: TaskContext) -> bool:
        return ctx.desktop_url is not None

    async def bootstrap(self, runtime: SkillRuntime, ctx: TaskContext) -> bool:
        navigated = await runtime.run_primitive(
            ProposedAction(
                action=ActionType.NAVIGATE,
                url=ctx.desktop_url,
                reasoning="Conexión inicial al escritorio autorizado por la tarea.",
                reason_code="bootstrap_navigate",
            ),
            origin=self.spec.skill_id,
        )
        if not navigated:
            return False
        await self.wait_until_stable(runtime, ctx)
        return True

    async def wait_until_stable(self, runtime: SkillRuntime, ctx: TaskContext) -> bool:
        budget = min(self.stability_timeout, max(0.0, ctx.deadline.remaining() - 5.0))
        loop = asyncio.get_running_loop()
        end = loop.time() + budget
        previous = await runtime.observe_visual()
        while loop.time() < end:
            await asyncio.sleep(self.interval)
            current = await runtime.observe_visual()
            if current == previous:
                return True
            previous = current
        return False

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        if DESKTOP_PASSWORD_SECRET not in ctx.secret_names:
            return []
        return [
            "ACCESO: si ves un diálogo de conexión o un campo de contraseña del escritorio remoto, "
            f"haz clic en el campo y usa type_text con secret_ref=\"{DESKTOP_PASSWORD_SECRET}\" "
            "(nunca conocerás ni escribirás la contraseña literal). Después confirma con el botón "
            "de conexión o press_key Enter si el diálogo lo indica.",
            "Si el acceso falla dos veces, no insistas: usa done con la explicación o ask_human.",
        ]
