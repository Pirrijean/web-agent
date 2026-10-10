# ADR-009: Deadline Supervisado Absoluto (300 Segundos Monotónicos)

**Estado:** Aceptado (Decisión Obligatoria)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.service.supervisor`, `web_agent.core.runner`  

---

## Contexto y Planteamiento del Problema
El reto fija un límite duro de evaluación de 5 minutos (300 segundos) por tarea. Actualmente, el perfil de tarea puede permitir hasta 3600 segundos, y los timeouts internos del runner o llamadas de red pueden exceder el límite contractual si no se aplican desde una capa externa global.

## Decisión
1. El supervisor impone un deadline monotónico absoluto:
   $$\text{deadline} = \operatorname{time.monotonic}() + 300.0$$
   calculado en el instante en que la tarea es admitida en `POST /tasks`, incluyendo el tiempo de cola.
2. Ningún perfil o payload puede ampliar este límite.
3. El supervisor propaga el tiempo restante a cada componente (inferencia, navegación, espera, verificación).
4. Si se alcanza el plazo de 300 segundos, el supervisor cancela cooperativamente la tarea de asyncio y fuerza el cierre de la sesión de Playwright. Si no responde en 5 segundos, termina forzosamente el worker y actualiza el estado público a `"fallida"`.

## Consecuencias
### Positivas
- Cumplimiento contractual garantizado: la tarea nunca excede los 300 segundos.
- Terminación limpia de recursos y liberación de workers.

### Negativas / Límites
- Si la tarea se interrumpe durante una operación persistente, el estado resultante debe reportarse explícitamente como incierto.
