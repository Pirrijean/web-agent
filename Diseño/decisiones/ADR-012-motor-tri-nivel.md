# ADR-012 · Motor jerárquico tri-nivel (implementación de la propuesta)

**Estado:** aceptado · **Fuente:** `ARQUITECTURA_TRI_NIVEL_OPTIMIZACION.md`

## Decisión

Se incorpora un router adaptativo con tres niveles que **comparten** validador contextual, política
R0–R3, ejecutor y auditoría (ninguno tiene una vía propia hacia Playwright):

| Nivel | Qué hace | Límite de riesgo | Módulos |
|---|---|---|---|
| 3 · local | Clic sobre un texto literal del objetivo localizado por OCR dirigido o caché semántica, sin inferencia | **solo R0** | `core/adaptive_router.py`, `perception/ocr_engine.py`, `core/semantic_cache.py` |
| 2 · macro | Una llamada al modelo devuelve hasta 5 pasos sobre la misma observación; ráfaga con guardián de cambios | 1.er paso = N1; resto **≤ R1** | `core/macro_schemas.py`, `core/runner.py` (`_execute_burst`) |
| 1 · clásico | Una acción por llamada (bucle original) | política normal | `core/runner.py` |

Escalamiento ascendente sin ciclos: fallo en N3 → el paso siguiente como máximo N2; ráfaga
interrumpida o acción fallida → N1; obstáculo resuelto en N1 → como máximo N2; éxito en N2 →
máximo restaurado.

## Desviaciones respecto a la propuesta (medidas en este proyecto)

| Propuesta | Medido | Decisión |
|---|---|---|
| OCR 30–45 ms/captura, < 30 MB, < 80 MB RAM | OCR completo **3–4 s**, **341 MB** en disco, **~400–500 MB** RSS; arranque en frío ~5 s | **OCR dirigido**: detección (~0,65 s) + reconocimiento solo de cajas con forma compatible → **~0,7–1,2 s**. Calentamiento en la carga. Extra opcional `ocr`; se carga solo si el objetivo nombra textos a pulsar. |
| Límite de 80 ms para N3 | inalcanzable en CPU | `WEB_AGENT_LEVEL3_TIMEOUT_SECONDS=2.5`; cada objetivo se analiza como máximo 3 veces y nunca dos veces la misma pantalla. |
| Firma = SHA-256 de todos los textos OCR | leer todo cuesta 3–4 s | Firma de la **disposición** de cajas detectadas (rejilla 16 px, máscara de barra de tareas y esquina del reloj) + clave compuesta (objetivo, paso del flujo, última acción). La entrada se vuelve a reconocer antes de usarse. |
| SLM local 0,5B opcional | +350 MB y otro runtime | No se implementa: las decisiones de N3 son reglas deterministas auditables. |
| Ráfaga validada con una captura al final | un clic puede abrir un modal y desplazar el resto | Guardián entre pasos (fracción cambiada ≤ 15 %, ≤ 3 zonas) sobre la captura posterior al repintado; además misma página/geometría. |
| El ejemplo de macro incluye «Guardar» | contradice la regla N2 ≤ R1 | Los pasos persistentes se cortan de la ráfaga y se proponen como acción individual. |
| Visión dual siempre en N2 | la miniatura 400×250 reduce la precisión de clic | Implementada (miniatura + ROI, transformación determinista `coord_space: "roi"`, guardián de cambios dispersos) pero **desactivada por defecto**. |
| VNC: verificación por OCR de palabras de éxito | el texto en pantalla no es confiable | El OCR solo aporta **señales no concluyentes** al verificador visual independiente; además se exige lienzo estable (`verify_stability_seconds`). |

## Consecuencias

- La imagen Docker crece ~340 MB con `WITH_OCR=1` (por defecto). Con `WITH_OCR=0` el N3 se
  desactiva solo y N2/N1 siguen operativos.
- Cada nivel deja trazas (`level3_*`, `level2_*`, `router_configured`) para medir su efecto real.
- Pruebas: componentes, runner con dobles, mutaciones de cada defensa y escenario con Chromium y
  OCR reales (`tests/scenarios/test_tiered_real.py`).
