"""Aplicación FastAPI/ASGI (documento maestro §8.1, §12).

Capa delgada: autentica, valida, delega en ``TaskManager`` y responde.
Nunca abre el navegador ni llama al modelo dentro de la petición HTTP.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from web_agent.api.auth import require_bearer
from web_agent.api.dto import TaskCreatedResponse, TaskCreateRequest, TaskStatusResponse
from web_agent.config import Settings
from web_agent.core.errors import AuditUnavailableError
from web_agent.service.task_manager import InvalidTaskError, TaskManager

logger = logging.getLogger(__name__)
TASK_ID_RE = re.compile(r"^task_[a-f0-9]{20}$")
STATIC_DIR = Path(__file__).parent / "static"

# La consola es una página estática sin secretos: el token lo introduce el usuario.
CONSOLE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-cache",
}


def create_app(settings: Optional[Settings] = None, task_manager: Optional[TaskManager] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    manager = task_manager or TaskManager(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await manager.shutdown()

    app = FastAPI(
        title="web-agent",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
    )
    app.state.settings = settings
    app.state.task_manager = manager

    @app.middleware("http")
    async def console_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers.update(CONSOLE_HEADERS)
        return response

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > settings.max_request_body_bytes:
                        return JSONResponse({"detail": "Cuerpo demasiado grande."}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length inválido."}, status_code=400)
            body = await request.body()
            if len(body) > settings.max_request_body_bytes:
                return JSONResponse({"detail": "Cuerpo demasiado grande."}, status_code=413)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Nunca devolver la entrada (podría contener la contraseña del escritorio).
        errors = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200]}
            for e in exc.errors()[:10]
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception):
        logger.error("Error interno en %s: %s", request.url.path, type(exc).__name__)
        return JSONResponse({"detail": "Error interno."}, status_code=500)

    console_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace(
        "__DOCS_ENABLED__", "true" if settings.expose_docs else "false"
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def console():
        return HTMLResponse(console_html)

    @app.get("/health", dependencies=[Depends(require_bearer)])
    async def health():
        info = manager.health()
        return JSONResponse(info, status_code=200 if info["ready"] else 503)

    @app.post("/tasks", response_model=TaskCreatedResponse, dependencies=[Depends(require_bearer)])
    async def create_task(payload: TaskCreateRequest):
        try:
            record = manager.create_task(payload.task, payload.desktop.url, payload.desktop.password)
        except InvalidTaskError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        except AuditUnavailableError:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auditoría no disponible.") from None
        return TaskCreatedResponse(task_id=record.task_id)

    @app.get("/tasks/{task_id}", response_model=TaskStatusResponse, dependencies=[Depends(require_bearer)])
    async def get_task(task_id: str):
        record = manager.get(task_id) if TASK_ID_RE.fullmatch(task_id) else None
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tarea no encontrada.")
        return TaskStatusResponse(
            estado=record.public_status,
            task_id=record.task_id,
            status=record.public_status,
            resumen=record.summary,
            steps_executed=record.steps_executed,
            elapsed_seconds=record.elapsed_seconds(),
            estado_interno=record.state.value,
            verificacion=record.verification_status,
            posibles_efectos_sin_confirmar=record.possible_side_effects,
        )

    return app
