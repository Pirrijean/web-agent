import argparse
import asyncio
import os
import sys
from pathlib import Path

from web_agent.browser.session import SecureBrowserSession
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import load_profile
from web_agent.doctor import run_doctor
from web_agent.models.adapters.openrouter import OpenRouterModelAdapter
from web_agent.models.adapters.scripted import ScriptedModelAdapter


def parse_args():
    parser = argparse.ArgumentParser(
        description="CLI de desarrollo y ejecución local para web-agent.",
        prog="python -m web_agent.cli",
    )
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # doctor
    subparsers.add_parser("doctor", help="Comprueba el entorno local y dependencias")

    # inspect-profile
    inspect_parser = subparsers.add_parser("inspect-profile", help="Inspecciona y valida un archivo de perfil YAML")
    inspect_parser.add_argument("profile_path", type=str, help="Ruta al archivo YAML del perfil")

    # run
    run_parser = subparsers.add_parser("run", help="Ejecuta una tarea del agente con un perfil específico")
    run_parser.add_argument("--profile", "-p", type=str, default="profiles/examples/portal_demo.yaml", help="Ruta al perfil")
    run_parser.add_argument("--objective", "-o", type=str, default="Comprobar acceso y navegación autorizada", help="Objetivo")
    run_parser.add_argument("--adapter", "-a", choices=["scripted", "openrouter"], default="scripted", help="Adaptador de modelo (scripted o openrouter)")
    run_parser.add_argument("--model", "-m", type=str, default="google/gemini-2.0-flash-001", help="Nombre del modelo si se usa OpenRouter")
    run_parser.add_argument("--headless", action="store_true", default=True, help="Ejecutar en modo headless")
    run_parser.add_argument("--no-headless", action="store_false", dest="headless", help="Ejecutar mostrando la ventana del navegador")

    return parser.parse_args()


async def execute_run(profile_path: str, objective: str, headless: bool, adapter_name: str = "scripted", model_name: str = "google/gemini-2.0-flash-001"):
    p = Path(profile_path)
    if not p.exists():
        print(f"Error: Perfil '{profile_path}' no encontrado.")
        return 1

    profile = load_profile(p)
    print(f"[*] Perfil cargado: {profile.id} (v{profile.version})")
    print(f"[*] Orígenes permitidos: {', '.join(profile.security.allowed_origins)}")
    print(f"[*] Objetivo: {objective}")
    print(f"[*] Adaptador: {adapter_name}" + (f" ({model_name})" if adapter_name == "openrouter" else ""))

    browser = SecureBrowserSession(
        headless=headless,
        chromium_sandbox=profile.browser.chromium_sandbox,
    )

    if adapter_name == "openrouter":
        if not os.environ.get("OPENROUTER_API_KEY"):
            print("Error: OPENROUTER_API_KEY no configurada para el adaptador OpenRouter.")
            return 1
        model = OpenRouterModelAdapter(model_name=model_name)
    else:
        model = ScriptedModelAdapter()

    runner = AgentRunner(profile=profile, model=model, browser=browser)

    print("[*] Iniciando ciclo de ejecución...")
    result = await runner.run(objective)
    print("--------------------------------------------------")
    print(f"Resultado final: {result.final_state.value}")
    print(f"Pasos ejecutados: {result.steps_executed}")
    print(f"Razón: {result.reason}")
    print("--------------------------------------------------")
    return 0 if result.final_state.value == "SUCCEEDED_VERIFIED" else 1


def main():
    args = parse_args()
    if args.command == "doctor":
        success = run_doctor()
        sys.exit(0 if success else 1)
    elif args.command == "inspect-profile":
        p = Path(args.profile_path)
        if not p.exists():
            print(f"Error: No existe el archivo {p}")
            sys.exit(1)
        profile = load_profile(p)
        print(f"Perfil validado con éxito: {profile.id}")
        print(f"Orígenes: {list(profile.security.allowed_origins)}")
        print(f"Permisos: navegación={profile.permissions.navigation}, forms={profile.permissions.fill_forms}")
        sys.exit(0)
    elif args.command == "run":
        exit_code = asyncio.run(
            execute_run(
                args.profile,
                args.objective,
                args.headless,
                adapter_name=args.adapter,
                model_name=args.model,
            )
        )
        sys.exit(exit_code)
    else:
        # Por defecto muestra ayuda
        print("Uso: python -m web_agent.cli [doctor | inspect-profile | run] --help")
        sys.exit(0)


if __name__ == "__main__":
    main()
