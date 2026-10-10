# ADR-010: Exclusión de Servidores MCP Externos de la Ruta Crítica del MVP

**Estado:** Aceptado (Simplificación Arquitectónica del MVP)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.execution`, `web_agent.mcp_servers`  

---

## Contexto y Planteamiento del Problema
El repositorio original incluía configuraciones de servidores MCP (Model Context Protocol), tales como `filesystem_audit_mcp.json` o `browser_server.py`. Sin embargo:
1. El reto prohíbe explícitamente herramientas open source externas que resuelvan la tarea por el agente (`npx`, servidores paralelos).
2. Los servidores MCP mantenían sesiones de Playwright paralelas y políticas parcialmente divergentes, creando riesgo de vulnerabilidades y bypasses de seguridad.

## Decisión
Excluir formalmente cualquier servidor MCP externo de la ruta crítica del agente:
1. Toda la automatización, validación y auditoría se ejecuta mediante código nativo en Python en un único ejecutor y una única política.
2. Si en el futuro se añade soporte MCP para interoperabilidad, actuará como un adaptador que delegará en las interfaces internas del núcleo sin duplicar lógica de seguridad.

## Consecuencias
### Positivas
- Código 100% propio y autocontenido en Python, cumpliendo estrictamente con las reglas de la hackathon.
- Eliminación de caminos de ejecución paralelos y estados inconsistentes.

### Negativas / Límites
- Se descarta la interoperabilidad directa con clientes MCP externos para el MVP.
