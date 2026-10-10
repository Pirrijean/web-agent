# ADR-008: Auditoría Obligatoria y Fail-Closed en el Flujo de Ejecución

**Estado:** Aceptado (Alineado con el núcleo existente)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.audit.events`  

---

## Contexto y Planteamiento del Problema
Toda acción realizada por el agente en sistemas remotos sin API debe quedar registrada con fines de depuración y evaluación, sin exponer secretos ni información sensible, y garantizando que el agente no opere a ciegas sin registro.

## Decisión
1. Mantener un registro estructurado JSONL donde cada evento se correlaciona con `task_id`, `run_id`, y `observation_id`.
2. Integrar redacción automática de datos sensibles (`DataRedactor`) en todas las cargas textuales.
3. Principio *Fail-Closed*: Si la persistencia del log de auditoría falla, la ejecución se detiene inmediatamente con estado `BLOCKED` / `FAILED`. No se permite que el agente continúe actuando sobre el entorno remoto si la auditoría no está operativa.

## Consecuencias
### Positivas
- Registro auditable completo del ciclo de vida de cada tarea.
- Protección garantizada contra fuga de credenciales en trazas y logs.

### Negativas / Límites
- Si el disco o sistema de archivos falla, la tarea se detiene. Un fallo de auditoría posterior a un clic persistente puede dejar efectos remotos inciertos.
