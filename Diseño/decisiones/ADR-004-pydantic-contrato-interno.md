# ADR-004: Pydantic como Contrato Interno y Frontera Estricta de Validación

**Estado:** Aceptado (Alineado con el núcleo existente)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.core.schemas`  

---

## Contexto y Planteamiento del Problema
La interacción entre el modelo de inferencia (LLM/VLM), el runner, la política de seguridad y la API REST requiere objetos tipados, serializables y rigurosamente rechazables ante cualquier desviación de formato. El agente no puede permitir inyecciones de campos arbitrarios ni tipos inesperados que puedan eludir controles de seguridad.

## Decisión
Usar modelos Pydantic v2 configurados con:
1. `extra="forbid"`: Cualquier campo no reconocido genera un error inmediato.
2. `frozen=True`: Inmutabilidad una vez validado el objeto.
3. Validación de tipos, rangos, patrones regex y exclusiones mutuas antes de que cualquier objeto sea procesado por la máquina de estados.

## Consecuencias
### Positivas
- Esquemas tipados explícitos y errores sintácticos detectados en la frontera de entrada.
- Inmutabilidad de propuestas y observaciones durante su ciclo de vida.
- Documentación automática de contratos compatibles con OpenAPI.

### Negativas / Límites
- Pydantic solo valida la sintaxis y forma del objeto; no valida permisos ni intenciones de negocio. La validación semántica y contextual se mantiene en la política de seguridad.
