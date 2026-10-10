# ADR-007: Observaciones con Identidad Inmutable y Geometría Explícita

**Estado:** Aceptado (Decisión Obligatoria)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** `web_agent.browser.observe`, `web_agent.core.schemas`  

---

## Contexto y Planteamiento del Problema
En interfaces gráficas y aplicaciones canvas, una interacción espacial (clic, escritura) calculada a partir de una captura previa puede pulsar sobre coordenadas equivocadas si la página sufrió un scroll, un resize o una animación entre el momento de observación y la ejecución de la acción.

## Decisión
1. Cada observación visual genera un `observation_id` criptográficamente único e inmutable, acompañado de timestamp, dimensiones del viewport en píxeles CSS ($W_{css}, H_{css}$) y dimensiones físicas de la imagen procesada.
2. Toda acción espacial propuesta por el modelo debe incluir obligatoriamente el `observation_id` correspondiente.
3. El runner y ejecutor verifican la vigencia del `observation_id` antes de actuar: si la observación es obsoleta, la acción se descarta inmediatamente y se fuerza una nueva observación.
4. Las coordenadas normalizadas $[0, 1000]$ se mapean a píxeles CSS mediante la fórmula matemática:
   $$X_{css} = \operatorname{round}\left(\frac{x_{norm}}{1000} \cdot (W_{css}-1)\right), \quad Y_{css} = \operatorname{round}\left(\frac{y_{norm}}{1000} \cdot (H_{css}-1)\right)$$
   con verificación estricta de frontera: $0 \le X_{css} < W_{css}$ y $0 \le Y_{css} < H_{css}$.

## Consecuencias
### Positivas
- Eliminación de carreras de interfaz y clics fuera de pantalla.
- Trazabilidad determinista entre propuesta, captura y evento de ratón.

### Negativas / Límites
- Requiere capturar y evaluar una nueva observación tras cada acción que pueda alterar la pantalla.
