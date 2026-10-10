"""CLI de desarrollo y operación de web-agent."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from web_agent.config import Settings


def parse_args():
    parser = argparse.ArgumentParser(description="CLI de web-agent.", prog="python main.py")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Comprueba el entorno local y dependencias")
    sub.add_parser("serve", help="Levanta la API REST (GET /health, POST /tasks, GET /tasks/{id})")

    insp = sub.add_parser("inspect-profile", help="Valida un perfil YAML")
    insp.add_argument("profile_path")

    run = sub.add_parser("run", help="Ejecuta una tarea localmente")
    run.add_argument("--objective", "-o", required=True)
    run.add_argument("--desktop-url", help="URL del escritorio remoto (modo visual_only)")
    run.add_argument("--password-env", default="DESKTOP_PASSWORD",
                     help="Variable de entorno con la contraseña del escritorio (nunca por argumento)")
    run.add_argument("--profile", "-p", help="Perfil YAML (alternativa a --desktop-url)")
    run.add_argument("--no-headless", action="store_false", dest="headless", default=True)
    return parser.parse_args()


async def execute_run(args) -> int:
    from dataclasses import replace

    from web_agent.browser.session import SecureBrowserSession
    from web_agent.core.context import SecretStore
    from web_agent.core.runner import AgentRunner
    from web_agent.core.schemas import DESKTOP_PASSWORD_SECRET, build_desktop_profile, load_profile
    from web_agent.service.worker import build_model

    settings = replace(Settings.from_env(), headless=args.headless)
    if not settings.model_configured:
        print("Error: configure OPENROUTER_API_KEY o WEB_AGENT_MODEL_PROVIDER=scripted.")
        return 1
    if args.desktop_url:
        profile = build_desktop_profile(
            task_id="cli",
            desktop_url=args.desktop_url,
            allow_private_desktop=settings.allow_private_desktop,
            allow_loopback_desktop=settings.allow_loopback_desktop,
            max_steps=settings.max_actions_per_task,
            headless=settings.headless,
            chromium_sandbox=settings.chromium_sandbox,
        )
    elif args.profile:
        profile = load_profile(Path(args.profile))
    else:
        print("Error: indique --desktop-url o --profile.")
        return 1
    password = os.environ.get(args.password_env)
    secrets = SecretStore({DESKTOP_PASSWORD_SECRET: password} if password else {})
    browser = SecureBrowserSession(
        headless=settings.headless,
        chromium_sandbox=settings.chromium_sandbox,
        viewport_width=settings.viewport_width,
        viewport_height=settings.viewport_height,
    )
    model = build_model(settings)
    runner = AgentRunner(profile, model, browser, settings=settings)
    print(f"[*] Perfil: {profile.id} | modo: {profile.perception.mode} | modelo: {getattr(model, 'model_id', '?')}")
    try:
        result = await runner.run(args.objective, desktop_url=args.desktop_url, secrets=secrets)
    finally:
        await model.aclose()
    print("-" * 60)
    print(f"Estado: {result.final_state.value} ({result.reason_code})")
    print(f"Pasos: {result.steps_executed} | llamadas al modelo: {result.model_calls} | {result.elapsed_seconds}s")
    print(f"Resumen: {result.summary}")
    return 0 if result.final_state.value == "succeeded_verified" else 1


def main():
    args = parse_args()
    if args.command == "doctor":
        from web_agent.doctor import run_doctor

        sys.exit(0 if run_doctor() else 1)
    elif args.command == "serve":
        from web_agent.entrypoint import serve

        serve()
    elif args.command == "inspect-profile":
        from web_agent.core.schemas import load_profile

        p = Path(args.profile_path)
        if not p.exists():
            print(f"Error: No existe el archivo {p}")
            sys.exit(1)
        profile = load_profile(p)
        print(f"Perfil validado: {profile.id} (modo {profile.perception.mode})")
        print(f"Orígenes: {list(profile.security.allowed_origins)} | escritorio: {profile.security.desktop_origin}")
        sys.exit(0)
    elif args.command == "run":
        sys.exit(asyncio.run(execute_run(args)))
    else:
        print("Uso: python main.py [doctor | serve | inspect-profile | run] --help")
        sys.exit(0)


if __name__ == "__main__":
    main()
