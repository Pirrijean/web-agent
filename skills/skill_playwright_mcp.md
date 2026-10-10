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
17: ## 3. Configuración MCP (El Contrato Seguro)
18: El agente debe utilizar exclusivamente el servidor MCP restringido propio de `web-agent`:
19: 
20: ```json
21: {
22:   "mcpServers": {
23:     "restricted-browser": {
24:       "command": "uv",
25:       "args": ["run", "python", "-m", "web_agent.mcp_servers.browser_server"]
26:     }
27:   }
28: }
29: ```
