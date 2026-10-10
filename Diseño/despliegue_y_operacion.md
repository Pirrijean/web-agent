# GUÍA DE DESPLIEGUE Y OPERACIÓN DEL SERVICIO
**Herramienta:** `web-agent`  
**Entregable:** Entrypoint REST Público (Cierre: 20:00 hasta fin del Demo Day)  
**Contrato:** `Authorization: Bearer <token>`, `GET /health`, `POST /tasks`, `GET /tasks/{id}`

---

## 1. Requisitos de Infraestructura
El evaluador accederá al agente a través de una URL pública HTTPS a partir de las 20:00.

| Componente | Requisito Mínimo |
| :--- | :--- |
| **Runtime** | Python 3.12 con `uv` |
| **Motor de Navegador** | Playwright Chromium (con dependencias de sistema `libnss3`, `libgbm`, etc.) |
| **Memoria RAM** | Mínimo 2 GB (Recomendado 4 GB para renderizado gráfico continuo) |
| **Conectividad** | Salida a Internet para el proveedor de VLM y entrada HTTP(S) pública en puerto 8000/443 |

---

## 2. Variables de Entorno de Producción
Configuradas de forma segura en el entorno de despliegue (nunca en el repositorio git):

```bash
# Token oficial del equipo exigido en el header 'Authorization: Bearer <token>'
EVALUATION_BEARER_TOKEN="token_oficial_suministrado_por_el_evento"

# Clave de API para el modelo de visión multimodal
OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxxxxx"
MODEL_NAME="google/gemini-2.0-flash-001"

# Configuración del servidor ASGI
HOST="0.0.0.0"
PORT="8000"

# Límites operativos del evaluador
TASK_TIMEOUT_SECONDS="300"
MAX_CONCURRENT_TASKS="2"
```

---

## 3. Estrategia de Contenerización (Dockerfile de Referencia)

```dockerfile
FROM python:3.12-slim

# Instalar dependencias del sistema operativo para Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar uv para gestión determinista de dependencias
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Instalar binarios de Playwright Chromium
RUN uv run playwright install chromium

COPY src/ src/
COPY profiles/ profiles/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "web_agent.entrypoint:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 4. Procedimiento de Verificación Previa al Despliegue (Checklist 20:00)

1. **Smoke Test de Salud (`GET /health`):**
   ```bash
   curl -i -H "Authorization: Bearer $EVALUATION_BEARER_TOKEN" https://<URL_PUBLICA>/health
   # Debe responder HTTP 200 OK
   ```
2. **Prueba de Autenticación Rechazada:**
   ```bash
   curl -i https://<URL_PUBLICA>/health
   # Debe responder HTTP 401 Unauthorized
   ```
3. **Prueba de Creación de Tarea Asíncrona (`POST /tasks`):**
   ```bash
   curl -i -X POST https://<URL_PUBLICA>/tasks \
     -H "Authorization: Bearer $EVALUATION_BEARER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"task": "Prueba de conectividad inicial", "desktop": {"url": "https://example.com", "password": "demo"}}'
   # Debe responder en < 2000 ms con {"task_id": "..."}
   ```
4. **Consulta de Estado (`GET /tasks/{task_id}`):**
   ```bash
   curl -i -H "Authorization: Bearer $EVALUATION_BEARER_TOKEN" https://<URL_PUBLICA>/tasks/<TASK_ID>
   # Debe responder status "en_curso", "terminada" o "fallida" y resumen.
   ```
