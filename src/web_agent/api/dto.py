"""Contratos REST públicos (documento maestro §12).

Contrato de evaluación: ``POST /tasks`` recibe la tarea en lenguaje natural y el
escritorio (URL + contraseña) y devuelve ``{"task_id": ...}``;
``GET /tasks/{task_id}`` devuelve ``status`` (``en_curso`` | ``terminada`` |
``fallida``) y ``resumen``.

Se aceptan alias razonables de campos y se ignoran campos desconocidos de
nivel superior para no rechazar al evaluador por metadatos adicionales; los
tamaños están acotados y la contraseña nunca se devuelve ni se registra.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from web_agent.core.schemas import MAX_OBJECTIVE_CHARS, MAX_URL_LENGTH


class DesktopSpec(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    url: str = Field(..., min_length=1, max_length=MAX_URL_LENGTH,
                     validation_alias=AliasChoices("url", "desktop_url", "href"))
    password: Optional[str] = Field(None, max_length=512, repr=False,
                                    validation_alias=AliasChoices("password", "pass", "contraseña", "contrasena"))

    @field_validator("url")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v or "\x00" in v:
            raise ValueError("URL vacía o inválida")
        return v


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    task: str = Field(..., min_length=1, max_length=MAX_OBJECTIVE_CHARS,
                      validation_alias=AliasChoices("task", "objective", "instruction", "tarea", "prompt"))
    desktop: DesktopSpec

    @model_validator(mode="before")
    @classmethod
    def _flat_desktop(cls, data: Any) -> Any:
        # Admite también {"task": ..., "url": ..., "password": ...} o "desktop" como URL.
        if isinstance(data, dict):
            data = dict(data)
            desktop = data.get("desktop")
            if isinstance(desktop, str):
                data["desktop"] = {"url": desktop, "password": data.get("password")}
            elif desktop is None and ("url" in data or "desktop_url" in data):
                data["desktop"] = {"url": data.get("url") or data.get("desktop_url"), "password": data.get("password")}
        return data

    @field_validator("task")
    @classmethod
    def _task(cls, v: str) -> str:
        v = v.strip()
        if not v or "\x00" in v:
            raise ValueError("Tarea vacía o inválida")
        return v


class TaskCreatedResponse(BaseModel):
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    resumen: str
    steps_executed: int
    elapsed_seconds: float
    estado_interno: str
    verificacion: Optional[str] = None
    posibles_efectos_sin_confirmar: bool = False
