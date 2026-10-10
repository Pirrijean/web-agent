"""Entrypoint de evaluación (documento maestro §12).

``uvicorn web_agent.entrypoint:app`` expone ``GET /health``, ``POST /tasks`` y
``GET /tasks/{task_id}`` con autenticación Bearer. La aplicación se crea de
forma diferida al acceder a ``app`` para leer la configuración del entorno.

``python -m web_agent.entrypoint --health`` comprueba la configuración local
sin lanzar navegador ni consumir inferencia.
"""

from __future__ import annotations

import sys

from web_agent.config import Settings

_app = None


def get_app():
    global _app
    if _app is None:
        from web_agent.api.app import create_app

        _app = create_app(Settings.from_env())
    return _app


def __getattr__(name: str):
    if name == "app":
        return get_app()
    raise AttributeError(name)


def serve() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run("web_agent.entrypoint:app", host=settings.host, port=settings.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--health":
        s = Settings.from_env()
        ok = bool(s.bearer_token) and s.model_configured
        print(f"{'OK' if ok else 'NOT_READY'}: token={'sí' if s.bearer_token else 'no'} "
              f"modelo={'sí' if s.model_configured else 'no'} proveedor={s.model_provider}")
        sys.exit(0 if ok else 1)
    serve()


if __name__ == "__main__":
    main()
