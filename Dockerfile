# syntax=docker/dockerfile:1
# Imagen de producción de web-agent: API + consola en el puerto 8000.

# --- Etapa 1: entorno virtual reproducible a partir de uv.lock -------------------
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencias primero (capa cacheable mientras no cambie el lock).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Proyecto como paquete instalado (no editable): incluye la consola estática.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


# --- Etapa 2: ejecución ------------------------------------------------------------
FROM python:3.12-slim-bookworm
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOST=0.0.0.0 \
    PORT=8000 \
    WEB_AGENT_HEADLESS=true \
    WEB_AGENT_CHROMIUM_SANDBOX=false \
    WEB_AGENT_AUDIT_DIR=/app/data/audit \
    WEB_AGENT_EVIDENCE_DIR=/app/data/evidence

COPY --from=build /app/.venv /app/.venv

# Chromium y sus librerías de sistema, en la versión exacta fijada por uv.lock.
# El contenedor (usuario sin privilegios, sin capacidades extra) es la frontera de
# aislamiento; por eso el sandbox interno de Chromium se desactiva por defecto.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app/data

WORKDIR /app
COPY profiles ./profiles
USER agent

EXPOSE 8000
# La consola (/) es pública y no expone datos: sirve como sonda de vida sin token.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/', timeout=4)"

CMD ["python", "-m", "web_agent.entrypoint"]
