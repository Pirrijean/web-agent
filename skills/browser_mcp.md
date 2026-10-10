# Skill: Secure Browser MCP Server (Playwright)

## Nombre
`secure-browser-mcp`

## Descripción
Servidor Model Context Protocol (MCP) en Python que envuelve `Playwright`. Proporciona operaciones deterministas y aisladas sobre el navegador, evitando el control directo y la ejecución de JS arbitrario por parte de los modelos de lenguaje. 

## Comando de Ejecución
```bash
uv run python src/web_agent/mcp_servers/browser_server.py
```

## Reglas de Seguridad y Restricciones
1. **Aislamiento de Sesión:** El navegador corre siempre en un contexto aislado (Incógnito), sin acceso a la sesión principal del sistema operativo ni a cookies compartidas.
2. **Cero Ejecución Arbitraria:** Métodos críticos como `page.evaluate()` están estrictamente prohibidos y no se exponen al agente. 
3. **Aprobación Humana (Operaciones R3):** Las acciones con impacto (envíos, compras) requieren validación fuera del modelo.
4. **Protección de Red:** Descargas deshabilitadas por defecto. Control de orígenes y bloqueos hacia redes internas.
