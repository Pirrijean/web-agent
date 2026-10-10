# ADR-005: FastAPI y ASGI para la API REST del Evaluador

**Estado:** Aceptado (Objetivo de Implementación)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.api` (Servidor HTTP del Evaluador)  

---

## Contexto y Planteamiento del Problema
El evaluador del reto interactúa mediante llamadas HTTP externas con `Authorization: Bearer <token>`. Requiere:
1. `GET /health`: Respuesta rápida 200 sin invocar modelos facturados.
2. `POST /tasks`: Admisión inmediata en < 2 segundos devolviendo `task_id`.
3. `GET /tasks/{task_id}`: Consulta asíncrona del estado (`en_curso`, `terminada`, `fallida`) y resumen.

## Decisión
Implementar un servidor REST basado en **FastAPI sobre ASGI (Uvicorn)** que:
1. Desacopla la admisión HTTP de la ejecución del runner mediante un `TaskManager` y `Supervisor` asíncronos.
2. Utiliza los mismos esquemas Pydantic del core para la validación estricta de payloads.
3. Aplica autenticación Bearer mediante dependencia de inyección sin almacenar credenciales en logs.

## Consecuencias
### Positivas
- Cumplimiento estricto del requisito de respuesta en < 2 segundos sin bloquear el hilo HTTP.
- Integración nativa con Pydantic y generación de OpenAPI.
- Aislamiento entre el plano de control (HTTP) y el plano de ejecución (Playwright).

### Negativas / Límites
- Requiere dependencias web (FastAPI/Starlette, Uvicorn) en el entorno de despliegue.
- La gestión del ciclo de vida en segundo plano debe controlarse con un supervisor dedicado para no fugar tareas colgadas.
