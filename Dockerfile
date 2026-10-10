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

# Instalar binarios del navegador Chromium para Playwright
RUN uv run playwright install chromium

COPY src/ src/
COPY profiles/ profiles/
COPY main.py ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "web_agent.entrypoint:app", "--host", "0.0.0.0", "--port", "8000"]
