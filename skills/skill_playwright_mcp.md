# Skill: Web Navigation & DOM Manipulation (Playwright MCP)

## 1. Descripción y Propósito
Esta skill proporciona el motor primario para navegación, interacción con elementos del DOM, formularios, pestañas, sesiones y capturas. Permite al agente observar la interfaz mediante accesibilidad y roles semánticos en lugar de selectores frágiles.

## 2. Requisitos de Instalación (Instrucciones para el Agente Constructor)
Para que esta skill funcione en la arquitectura, el agente constructor debe preparar el entorno ejecutando los siguientes comandos en la terminal, dentro de la raíz del proyecto `web-agent`:

1.  **Inicializar el entorno asíncrono seguro:**
    `uv venv`
    `source .venv/bin/activate`
2.  **Instalar dependencias del núcleo:**
    `uv pip install playwright pydantic pytest`
3.  **Instalar el motor del navegador aislado:**
    `playwright install chromium`
4.  **Requisito externo:** Asegurar que Node.js está instalado en el sistema (`npx` es requerido para el servidor MCP).

## 3. Configuración MCP (El Contrato)
El agente constructor debe inyectar la siguiente configuración JSON en el orquestador principal para habilitar la skill:

```json
{
  "mcpServers": {
    "playwright-web": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-playwright"]
    }
  }
}
