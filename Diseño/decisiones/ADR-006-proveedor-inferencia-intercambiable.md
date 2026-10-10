# ADR-006: Proveedor de Inferencia Intercambiable y Aislado

**Estado:** Aceptado (Dirección de Diseño)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.models`  

---

## Contexto y Planteamiento del Problema
El modelo de inteligencia artificial (LLM/VLM) es una dependencia externa no confiable. Las reglas oficiales de evaluación pueden restringir proveedores específicos, exigir modelos de visión particulares o imponer límites de coste y privacidad. El runner no debe estar acoplado a la API propietaria de ningún proveedor.

## Decisión
Definir una interfaz abstracta `BaseModelAdapter` con el método:
```python
async def propose_action(self, context: TaskContext, observation: VisualObservation | DOMObservation) -> ProposedAction:
    ...
```
1. El modelo solo recibe el objetivo y la observación esterilizada (captura visual o controles reducidos).
2. El modelo jamás recibe contraseñas, tokens ni credenciales de acceso.
3. Se admite OpenRouter como candidato con soporte multimodal (VLM), manteniendo un adaptador `ScriptedModelAdapter` determinista para pruebas automatizadas sin coste de inferencia.

## Consecuencias
### Positivas
- Sustitución de proveedor sin modificar el runner, la política ni el ejecutor.
- Pruebas deterministas de CI usando adaptadores simulados.
- Control presupuestario y de timeout encapsulado en el adaptador.

### Negativas / Límites
- Requiere mapear las diferencias en formato de imágenes y respuestas estructuradas entre proveedores.
