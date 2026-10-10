import os
import sys
from pathlib import Path

from web_agent.core.schemas import load_profile


def run_doctor() -> bool:
    """Verifica el entorno de ejecución de web-agent sin gastar dinero ni lanzar tareas."""
    print("==================================================")
    print("[*] DIAGNOSTICO DEL ENTORNO: web-agent")
    print("==================================================")

    all_ok = True

    # 1. Versión de Python
    py_version = sys.version_info
    py_ok = py_version >= (3, 12)
    print(f"[{'OK' if py_ok else 'FAIL'}] Python >= 3.12: {sys.version.split()[0]}")
    if not py_ok:
        all_ok = False

    # 2. Dependencias críticas
    deps = ["playwright", "pydantic", "yaml", "pytest"]
    for dep in deps:
        try:
            __import__(dep)
            print(f"[OK] Dependencia '{dep}': instalada")
        except ImportError:
            print(f"[FAIL] Dependencia '{dep}': NO encontrada")
            all_ok = False

    # 3. Directorios de datos y auditoría
    data_dir = Path("./data/audit").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    dir_ok = os.access(data_dir, os.W_OK)
    print(f"[{'OK' if dir_ok else 'FAIL'}] Directorio de auditoria con permisos de escritura: {data_dir}")
    if not dir_ok:
        all_ok = False

    # 4. Verificación de variables de entorno (sin mostrar secretos)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key and len(api_key.strip()) > 5:
        masked = f"{api_key[:4]}...{api_key[-3:]}"
        print(f"[OK] Variable OPENROUTER_API_KEY: Configurada ({masked})")
    else:
        print("[INFO] Variable OPENROUTER_API_KEY: No configurada (Modo simulacion / offline disponible)")

    # 5. Carga de perfil de ejemplo
    profile_path = Path("profiles/examples/portal_demo.yaml")
    if profile_path.exists():
        try:
            profile = load_profile(profile_path)
            print(f"[OK] Perfil base '{profile.id}': Cargado y validado contra esquema Pydantic")
        except Exception as exc:
            print(f"[FAIL] Error al validar perfil '{profile_path}': {exc}")
            all_ok = False
    else:
        print(f"[INFO] Perfil '{profile_path}' no encontrado en raiz actual.")

    print("==================================================")
    if all_ok:
        print("[EXITO] Entorno de desarrollo verificado y listo para operar.")
    else:
        print("[ALERTA] Se encontraron problemas en el entorno. Revisa los mensajes anteriores.")
    print("==================================================")

    return all_ok



if __name__ == "__main__":
    success = run_doctor()
    sys.exit(0 if success else 1)
