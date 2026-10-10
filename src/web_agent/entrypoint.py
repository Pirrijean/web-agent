"""Punto de entrada desacoplado para evaluación oficial (Fase Entrypoint).

Este módulo actúa como adaptador del contrato oficial del juez de la hackathon.
Cuando se publique el brief a las 10:10 con el formato de entrada/salida requerido,
este archivo traduce los parámetros al AgentRunner sin tocar la lógica interna.
"""

import asyncio
import sys
from typing import Any, Dict

from web_agent.browser.session import SecureBrowserSession
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import TaskProfile, load_profile
from web_agent.models.adapters.scripted import ScriptedModelAdapter


async def evaluate_task(task_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Adaptador de evaluación que recibe el diccionario de tarea y devuelve el dict de resultado."""
    profile_path = task_payload.get("profile_path", "profiles/examples/portal_demo.yaml")
    objective = task_payload.get("objective", "Navegar y comprobar estado")

    profile: TaskProfile = load_profile(profile_path)
    browser = SecureBrowserSession(
        headless=task_payload.get("headless", True),
        chromium_sandbox=profile.browser.chromium_sandbox,
    )
    model = ScriptedModelAdapter()
    runner = AgentRunner(profile=profile, model=model, browser=browser)

    result = await runner.run(objective)
    return {
        "run_id": result.run_id,
        "status": result.final_state.value,
        "steps": result.steps_executed,
        "reason": result.reason,
        "blocked_requests": result.blocked_requests,
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--health":
        print("OK: web-agent entrypoint ready")
        sys.exit(0)

    # Ejecución por defecto para pruebas
    sample_payload = {
        "profile_path": "profiles/examples/portal_demo.yaml",
        "objective": "Comprobar inicialización del entrypoint",
    }
    res = asyncio.run(evaluate_task(sample_payload))
    print(res)


if __name__ == "__main__":
    main()
