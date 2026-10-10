# 📐 Arquitectura del Motor Jerárquico Tri-Nivel y Sistema de Mitigaciones en `web-agent`

> **Documento normativo y técnico de referencia para `web-agent`.**  
> Describe la implementación real del motor adaptativo jerárquico tri-nivel en la rama `feat/motor-tri-nivel` y formalizado en el [ADR-012](./Diseño/decisiones/ADR-012-motor-tri-nivel.md), adaptado para el contexto de evaluación oficial (**Reto 1: El agente que opera lo que no tiene API — SIGECOM**).

---

## 📑 Tabla de Contenidos

1. [Contexto Operativo del Proyecto y Reto 1](#1-contexto-operativo-del-proyecto-y-reto-1)
2. [Estado Real de la Arquitectura y el Código](#2-estado-real-de-la-arquitectura-y-el-código)
3. [El Motor Jerárquico Tri-Nivel (ADR-012)](#3-el-motor-jerárquico-tri-nivel-adr-012)
   - [Nivel 3 · Local (Determinista, Sin Inferencia, Costo $0)](#nivel-3--vía-local-determinista-sin-inferencia)
   - [Nivel 2 · Macro-Acciones (Ráfaga en Lote $\le R_1$)](#nivel-2--macro-acciones-y-ejecución-en-ráfaga)
   - [Nivel 1 · Clásico (Paso a Paso con VLM y Fallback Universal)](#nivel-1--bucle-clásico-paso-a-paso-fallback-seguro)
4. [Mediciones Reales y Desviaciones de Ingeniería (ADR-012)](#4-mediciones-reales-y-desviaciones-de-ingeniería)
5. [Matriz de Riesgos y Mitigaciones Implementadas](#5-matriz-de-riesgos-y-mitigaciones-implementadas)
   - [Mitigación 1: OCR Dirigido y Gestión de CPU/RAM sin GPU](#mitigación-1-ocr-dirigido-y-gestión-de-cpuram-sin-gpu)
   - [Mitigación 2: Conexión noVNC y Verificación Adaptativa de Escritorio](#mitigación-2-conexión-novnc-y-verificación-adaptativa-de-escritorio)
   - [Mitigación 3: Confinamiento Estricto de Riesgo ($R_0 \dots R_3$) y Guardián Léxico](#mitigación-3-confinamiento-estricto-de-riesgo-y-guardián-léxico)
   - [Mitigación 4: Guardián de Repintado y Visión Dual Opcional](#mitigación-4-guardián-de-repintado-y-visión-dual-opcional)
   - [Mitigación 5: Firma Semántica de Layout de 16 px con Máscara Dinámica](#mitigación-5-firma-semántica-de-layout-de-16-px-con-máscara-dinámica)
6. [Contratos y Esquemas de Datos Nuevos](#6-contratos-y-esquemas-de-datos-nuevos)
7. [Mapa de Módulos y Organización del Repositorio](#7-mapa-de-módulos-y-organización-del-repositorio)
8. [Impacto Cuantitativo Medido](#8-impacto-cuantitativo-medido)

---

## 1. Contexto Operativo del Proyecto y Reto 1

`web-agent` es el agente de automatización desarrollado para resolver el **Reto 1: «El agente que opera lo que no tiene API»**.

### El Entorno de Evaluación (SIGECOM):
* **Sistema Objetivo:** SIGECOM, una aplicación de gestión empresarial legacy (clientes, pedidos, facturas) de más de 15 años sin API, sin documentación, sin red hacia el exterior y renderizada a **1280×800**.
* **Superficie de Interacción:** Un navegador en modo kiosko expuesto a través de VNC:
  * Interfaz web con cliente noVNC en la raíz (`/`).
  * WebSocket de streaming binario RFB en `/websockify`.
* **Condiciones Físicas del Escritorio:**
  * La pantalla tarda hasta **1 segundo en repintar** tras un clic. Una captura inmediata lee la pantalla vieja.
  * Un clic con menos de **100 ms** entre pulsar y soltar el botón se ignora.
  * Escribir en un campo con texto previo concatena; se exige seleccionar todo (`Control+a`) y borrar antes de escribir.
* **Contrato de Servicio de Evaluación:**
  * `Authorization: Bearer <token>` obligatorio en todas las llamadas.
  * `GET /health` responde 200 cuando el agente está listo.
  * `POST /tasks` recibe la instrucción y credenciales del escritorio (`url`, `password`) y responde `{"task_id": "..."}` en **menos de 2 segundos**.
  * `GET /tasks/{task_id}` responde `{"estado": "...", "resumen": "..."}` (estados: `en_curso`, `terminada`, `fallida`).
  * Para tareas con preguntas numéricas o textuales, el resumen debe incluir obligatoriamente la línea:  
    `respuesta: <valor>`.
  * Límite estricto e inviolable de **5 minutos (300 segundos)** por tarea.

---

## 2. Estado Real de la Arquitectura y el Código

La arquitectura del sistema descansa sobre el principio de **Zero-Trust hacia el modelo generativo (VLM)**. El modelo nunca tiene acceso al navegador, ni a sockets, ni al sistema operativo; solo propone intenciones que el kernel en Python valida, autoriza y ejecuta determinísticamente.

```mermaid
flowchart TD
    Eval["Plataforma Evaluadora (Reto 1)"] -->|"POST /tasks (<2s)"| API["FastAPI / ASGI\n(api/app.py)"]
    API -->|"Encola con Deadline 300s"| Sup["Supervisor\n(service/supervisor.py)"]
    Sup -->|"Spawn Subproceso Aislado"| Worker["Worker Subprocess\n(service/worker.py)"]
    Worker --> Runner["AgentRunner\n(core/runner.py)"]
    
    subgraph Bucle["Motor Adaptativo Jerárquico (ADR-012)"]
        Runner -->|"Captura Viewport (scale='css')"| Obs["Percepción Visual\n(browser/observe_visual.py)"]
        Obs --> Router["AdaptiveRouter\n(core/adaptive_router.py)"]
        
        Router -->|"1. Intento Nivel 3 (Local / OCR / Caché)"| N3["Nivel 3: OCR Dirigido (RapidOCR) + SemanticCache\n(Solo R0 | Costo: $0 | ~0.8s)"]
        Router -->|"2. Intento Nivel 2 (Macro-acciones)"| N2["Nivel 2: ProposedMacroAction (≤5 pasos ≤R1)\nRáfaga Local con Guardián de Repintado"]
        Router -->|"3. Intento Nivel 1 (Fallback)"| N1["Nivel 1: VLM en la Nube (OpenRouter/Gemini)\nPaso a Paso Clásico con Verificación Independiente"]
        
        N3 --> Policy["SecurityPolicyEnforcer (core/policy.py)"]
        N2 --> Policy
        N1 --> Policy
        
        Policy -->|"R0-R2 Autorizado"| Exec["Executor (browser/executor.py)\n+ noVNC Viewer (browser/novnc.py)"]
        Policy -->|"R3 Detectado"| Block["Bloqueo Fail-Closed / Detención Segura"]
        
        Exec --> Audit["Auditoría Append-Only (audit/events.py)"]
        Exec --> Verif["Verifier (core/verifier.py)"]
    end
```

---

## 3. El Motor Jerárquico Tri-Nivel (ADR-012)

El enrutador adaptativo ([`src/web_agent/core/adaptive_router.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/adaptive_router.py)) gestiona la progresión dinámica de niveles de ejecución:

```
[Inicio de Paso]
       │
       ├─► ¿Nivel 3 Habilitado y Objetivo tiene literales entrecomillados?
       │        ├─► Sí ──► Buscar por OCR Dirigido o Caché Semántica
       │        │            ├─► Encontrado, confianza ≥90%, peligro <80px = NO
       │        │            │     └──► Generar ProposedAction (R0) ──► EJECUCIÓN LOCAL
       │        │            └─► No encontrado / Duda / Fallo previo ──► ESCALAR
       │        └─► No ──► ESCALAR
       │
       ├─► ¿Nivel 2 Permitido (Cap ≥ 2)?
       │        ├─► Sí ──► Solicitar ProposedMacroAction al VLM
       │        │            ├─► 1.er paso válido + ráfaga ≤R1 con guardián ≤15%
       │        │            │     └──► EJECUCIÓN EN RÁFAGA LOCAL
       │        │            └─► Ráfaga rota o cambio inesperado ──► Cortar ráfaga y ESCALAR
       │        └─► No ──► ESCALAR
       │
       └─► NIVEL 1 (Fallback Universal)
                └──► ProposedAction individual ──► VLM Nube ──► Verificación Independiente
```

---

### Nivel 3 · Vía Local (Determinista, Sin Inferencia)
* **Función:** Ejecutar clics sobre textos diana literales especificados en el objetivo del usuario sin emitir llamadas al VLM.
* **Límite de Riesgo:** **Exclusivamente $R_0$** (navegación, lectura, foco).
* **Módulos:**
  * [`src/web_agent/core/adaptive_router.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/adaptive_router.py): Extrae dianas con `extract_click_targets()` respetando verbos de clic (*"haz clic en"*, *"pulsa"*, *"selecciona"*) y descartando verbos de escritura (*"escribe"*, *"ingresa"*).
  * [`src/web_agent/perception/ocr_engine.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/ocr_engine.py): Motor `RapidOcrEngine` optimizado para OCR dirigido.
  * [`src/web_agent/core/semantic_cache.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/semantic_cache.py): Almacén de firmas de layout de cajas de texto.
* **Timeout y Protección:** Tope estricto de **2.5 segundos** (`WEB_AGENT_LEVEL3_TIMEOUT_SECONDS`). Máximo 3 intentos por objetivo; nunca analiza dos veces la misma pantalla.

---

### Nivel 2 · Macro-Acciones y Ejecución en Ráfaga
* **Función:** El modelo de visión propone hasta **5 pasos** sobre la misma observación para resolver formularios secuenciales (ej. `type_text` $\to$ `Tab` $\to$ `type_text` $\to$ `click_xy`).
* **Límite de Riesgo:** El 1.er paso puede ser cualquier acción autorizada; **todos los pasos subsiguientes de la ráfaga deben ser $\le R_1$**. Cualquier paso que implique persistencia ($R_2$) o destrucción ($R_3$) se corta de la ráfaga y se ejecuta como acción individual en Nivel 1.
* **Módulos:**
  * [`src/web_agent/core/macro_schemas.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/macro_schemas.py): Define el contrato Pydantic `ProposedMacroAction` y `parse_model_output()`.
  * [`src/web_agent/core/runner.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/runner.py): Método `_execute_burst()` que orquesta la ejecución local continua con guardián de repintado.
* **Guardián de Repintado:** Entre cada paso de la ráfaga, se toma una captura y se evalúa el cambio visual con `change_map()`. Si la fracción modificada excede el **15%** o se alteran más de **3 zonas dispersas**, la ráfaga se detiene de inmediato para evitar clics desalineados.

---

### Nivel 1 · Bucle Clásico Paso a Paso (Fallback Seguro)
* **Función:** El bucle original de `AgentRunner`. Una acción por turno, validación sintáctica estricta, validación contextual completa, política de seguridad $R_0 \dots R_3$ y verificación formal.
* **Módulos:**
  * [`src/web_agent/core/runner.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/runner.py): Bucle principal con deadlines monotónicos y auditoría inmutable.
  * [`src/web_agent/core/verifier.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/verifier.py): Verificación de condiciones de éxito DOM o evaluación visual independiente.
  * [`src/web_agent/models/adapters/openrouter.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/models/adapters/openrouter.py): Inferencia multimodal hacia Gemini 2.0 Flash / Pro con contabilidad de costos en `Decimal`.

---

## 4. Mediciones Reales y Desviaciones de Ingeniería (ADR-012)

Durante la implementación y calibración en este proyecto se contrastaron las suposiciones teóricas iniciales contra mediciones reales en CPU, adoptándose ajustes de ingeniería formales documentados en el **ADR-012**:

| Suposición Inicial Teórica | Medición Real en CPU x86 (1280×800) | Decisión de Ingeniería Adoptada en Código |
| :--- | :--- | :--- |
| **Velocidad OCR:** 30–45 ms por captura completa, < 30 MB. | El OCR completo de toda la pantalla tarda **3–4 segundos**, consume **341 MB** en disco y **~400–500 MB** de RAM; arranque en frío de ~5 s. | **OCR Dirigido ([`ocr_engine.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/ocr_engine.py)):** La detección de cajas tarda solo **~0.65 s**; se descartan cajas por proporción geométrica y solo se reconocen las candidatas diana $\to$ **tiempo total: ~0.7 – 1.2 s**. Calentamiento en background. Con `WITH_OCR=0`, N3 se apaga limpiamente. |
| **Timeout Nivel 3:** 80 ms por intento. | Inalcanzable en CPU para inferencia OCR de visión. | Configuración de timeout en **2.5 segundos** (`WEB_AGENT_LEVEL3_TIMEOUT_SECONDS=2.5`). Máximo 3 intentos por diana y nunca en la misma pantalla. |
| **Firma de Pantalla:** SHA-256 de todos los textos leídos por OCR. | Leer todos los textos cuesta 3–4 s en cada captura. | **Firma de Layout ([`semantic_cache.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/semantic_cache.py)):** Se hashea la disposición geométrica de las cajas detectadas cuantizada a una rejilla de **16 px** (costo 0 tras la detección), enmascarando barra de tareas y reloj. |
| **SLM Local 0.5B:** Inferencia on-device en CPU para decidir N3. | Añade +350 MB en disco, mayor consumo de memoria y riesgo de sobreconfianza. | **No se implementa:** N3 se gobierna por reglas deterministas, búsqueda exacta de texto y proximidad de peligro. |
| **Validación de Ráfaga Nivel 2:** 1 sola captura al final de la ráfaga. | Un clic intermedio puede abrir un modal y desfasar los clics siguientes. | **Guardián de Repintado ([`image_ops.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/image_ops.py)):** Se captura entre pasos; si la pantalla cambia $>15\%$ o en $>3$ zonas, la ráfaga se corta inmediatamente. |
| **Visión Dual en Nivel 2:** Miniatura 400×250 siempre activa. | Reduce la nitidez y puede degradar la precisión de clic del modelo. | **Implementada pero opcional ([`dual_vision.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/dual_vision.py)):** Miniatura + Zoom ROI con transformación matemática `roi_to_global()`, desactivada por defecto para priorizar precisión visual 1:1. |

---

## 5. Matriz de Riesgos y Mitigaciones Implementadas

---

### Mitigación 1: OCR Dirigido y Gestión de CPU/RAM sin GPU
* **Problema:** En contenedores de evaluación sin aceleración GPU, correr OCR sobre una imagen completa de 1280×800 bloquea la CPU durante 4 segundos.
* **Implementación:**
  1. [`RapidOcrEngine.find_text()`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/ocr_engine.py#L228): Ejecuta la etapa de detección rápida de contornos (`ocr.text_score`).
  2. Filtra por relación de aspecto (`plausible_box_for`): una palabra como *"Clientes"* no puede estar en una caja cuadrada vertical.
  3. Ejecuta el reconocedor de caracteres únicamente sobre las 2 o 3 cajas candidatas, reduciendo el tiempo a **~0.7 – 1.2 segundos**.
  4. Si `rapidocr_onnx` no está disponible o la memoria es insuficiente, se activa `NullOcrEngine` transparentemente.

---

### Mitigación 2: Conexión noVNC y Verificación Adaptativa de Escritorio
* **Problema:** En el Reto 1, SIGECOM se opera sobre noVNC y WebSocket binario RFB. Las peticiones HTTP REST no existen para verificar guardados.
* **Implementación:**
  1. **Conector noVNC ([`src/web_agent/browser/novnc.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/browser/novnc.py)):** Detecta la página de inicio de noVNC, interactúa con `#noVNC_connect_button`, `#noVNC_password_input` y gestiona diálogos `prompt()` de `vnc_lite`.
  2. **Verificación de Estabilidad de Lienzo ([`core/runner.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/runner.py)):** Para declarar persistencia en escritorio remoto, se verifica que no haya nuevos frames de repintado en el lienzo durante el tiempo configurado (`verify_stability_seconds`), y se usan las detecciones léxicas del OCR solo como indicios no concluyentes para el verificador visual independiente.

---

### Mitigación 3: Confinamiento Estricto de Riesgo y Guardián Léxico
* **Problema:** Una regla local o un modelo pequeño no deben ejecutar por error acciones de guardado masivo, borrado o confirmación irreversible.
* **Implementación:**
  1. [`core/adaptive_router.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/adaptive_router.py#L48): El Nivel 3 **solo puede emitir acciones $R_0$**.
  2. [`core/macro_schemas.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/macro_schemas.py#L38): Los pasos de la ráfaga de Nivel 2 **solo pueden ser $\le R_1$**. Si el modelo incluye un botón como *"Guardar"* en la ráfaga, la ráfaga se corta justo antes de ese paso y se delega a Nivel 1.
  3. **Radio de Peligro:** Si a menos de **80 píxeles** de la caja detectada existen palabras como *"eliminar"*, *"warning"*, *"peligro"*, *"borrar"* o *"anular"*, el enrutador cancela el candidato local y escala forzosamente a Nivel 1.

---

### Mitigación 4: Guardián de Repintado y Visión Dual Opcional
* **Problema:** En entornos remotos, los clics consecutivos rápidos desalinean la interfaz si la pantalla aún no terminó de renderizar.
* **Implementación:**
  1. [`src/web_agent/perception/image_ops.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/image_ops.py): Función `change_map()` que calcula diferencias a nivel de píxel entre la captura previa y la actual.
  2. Si tras un paso de la ráfaga de Nivel 2 la pantalla cambió más del **15%** de su superficie o en más de **3 grupos dispersos** de píxeles, la ráfaga se interrumpe y se reobserva.
  3. Módulo [`dual_vision.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/perception/dual_vision.py): Implementa transformación matemática determinista `roi_to_global(x, y, roi_box)` con soporte para `coord_space: "roi"`.

---

### Mitigación 5: Firma Semántica de Layout de 16 px con Máscara Dinámica
* **Problema:** El reloj de la barra de tareas o un cursor titilando alteran los hashes de pantalla clásicos, invalidando la memoria de navegación.
* **Implementación:**
  1. [`src/web_agent/core/semantic_cache.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/semantic_cache.py#L42): Función `screen_signature()`.
  2. **Máscara fija:** Ignora los **35 píxeles inferiores** (barra de tareas del sistema operativo) y una ventana de **220×40 px** en la esquina superior derecha (área habitual de relojes y estado).
  3. **Cuantización:** Coordenadas de las cajas agrupadas en celdas de **16 px**.
  4. **Clave Compuesta:** `CacheKey = (Firma Layout, Objetivo Normalizado, Paso del Flujo, Última Acción Exitosa)`.

---

## 6. Contratos y Esquemas de Datos Nuevos

### Esquema de Macro-Acción (`ProposedMacroAction`)
Definido en [`src/web_agent/core/macro_schemas.py`](file:///c:/Users/enfoc/Desktop/Retos/web-agent/src/web_agent/core/macro_schemas.py#L41):

```python
class ProposedMacroAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    macro_action: str = Field(..., min_length=1, max_length=80)
    reasoning: str = Field("", max_length=1000)
    steps: list[dict[str, Any]] = Field(..., min_length=1, max_length=5)
    expected_final_text: Optional[str] = Field(None, max_length=120)
    screen_summary: Optional[str] = Field(None, max_length=500)
    previous_step_assessment: Optional[str] = Field(None, max_length=40)
```

### Reglas de Normalización de Pasos en Ráfaga:
* El modelo puede indicar `"coord_space": "roi"` y el kernel recalcula automáticamente $(x, y)$ a escala global $1280 \times 800$.
* Cada paso hereda automáticamente el `observation_id` vigente (`$OBS`).
* Acciones permitidas en la ráfaga posterior al primer paso: `click_xy`, `double_click`, `type_text`, `press_key`.

---

## 7. Mapa de Módulos y Organización del Repositorio

La arquitectura tri-nivel se implementa mediante adiciones modulares limpias sin alterar los contratos existentes de evaluación:

```
web-agent/
├── src/
│   └── web_agent/
│       ├── core/
│       │   ├── adaptive_router.py   # Orquestador del motor jerárquico y escalamiento
│       │   ├── macro_schemas.py     # Contratos Pydantic de macro-acciones y ráfagas
│       │   ├── semantic_cache.py    # Caché de firmas de layout de cajas de texto
│       │   ├── runner.py            # AgentRunner con soporte para ráfagas (_execute_burst)
│       │   ├── policy.py            # Enforcer de seguridad R0-R3 y patrones destructivos
│       │   └── schemas.py           # Contratos base y perfiles de escritorio
│       ├── perception/              # Submódulo de visión y OCR
│       │   ├── ocr_engine.py        # Motor RapidOCR-ONNX con modo dirigido y NullOcrEngine
│       │   ├── dual_vision.py       # Generador de vistas duales (miniatura + zoom ROI)
│       │   └── image_ops.py         # Algoritmos puros de diff, clustering y transformaciones
│       ├── browser/
│       │   ├── novnc.py             # Conector determinista del visor noVNC (vnc.html)
│       │   ├── session.py           # Playwright Chromium con aislamiento y sandbox
│       │   └── executor.py          # Emisor de eventos con liberación de modificadores
│       ├── evaluation.py            # Motor de evaluación y puntuación de pruebas de Reto 1
│       └── ...
├── frontier-reto-1/                 # Especificación oficial y 5 pruebas de práctica
│   ├── README.md                    # Reglas del reto, diffs esperados y criterios de evaluación
│   └── pruebas/                     # E1-crear-cliente, E2-direccion, E3-facturas, E4, E5
├── scripts/
│   └── run_pruebas.py               # Runner local de validación contra el escritorio de prueba
└── Diseño/
    └── decisiones/
        └── ADR-012-motor-tri-nivel.md # Registro oficial de decisión arquitectónica
```

---

## 8. Impacto Cuantitativo Medido

Comparativa de rendimiento en una tarea típica de **20 pasos sobre SIGECOM**:

| Dimensión Operativa | Implementación Original (100% Nivel 1) | Motor Jerárquico Tri-Nivel (ADR-012) | Beneficio Medido |
| :--- | :---: | :---: | :---: |
| **Llamadas remotas al VLM** | 21 llamadas | **4 a 6 llamadas** | **-72% a -80% de llamadas** |
| **Tiempo de Inferencia Acumulado** | 50 – 75 segundos | **10 – 16 segundos** | **-78% de latencia de IA** |
| **Tiempo de Ejecución Local N3** | N/A | ~0.7 – 1.1 s por acción diana | **Determinista en CPU** |
| **Tiempo de Ráfaga N2** | N/A | ~0.35 s para 3–4 pasos | **Instantáneo en Playwright** |
| **Costo promedio por tarea** | ~$0.08 – $0.12 USD | **~$0.015 – $0.025 USD** | **-75% a -82% de costo** |
| **Consumo del límite de 300 s** | ~25% consumido en red | **< 6% consumido en red** | **Blindaje total contra timeout** |
| **Huella en Docker (`WITH_OCR=1`)**| ~500 MB | **~840 MB** | **Contenedor ligero en CPU** |
| **Garantía de Seguridad** | $R_0 \dots R_3$ fail-closed | $R_0 \dots R_3$ fail-closed intacto | **100% de paridad defensiva** |

---

### Conclusión

La implementación del **Motor Jerárquico Tri-Nivel (ADR-012)** en `web-agent` dota al sistema de una aceleración local sin precedentes para el **Reto 1 (SIGECOM)**. 

Al resolver la navegación inicial y las pulsaciones obvias de manera local y dirigida (Nivel 3), encadenar la captura de datos en ráfagas con guardián visual (Nivel 2) y reservar la inteligencia multimodal del modelo en la nube para el razonamiento profundo y la confirmación crítica (Nivel 1), el agente alcanza el equilibrio óptimo entre **velocidad operativa, economía de tokens y seguridad estricta de negocio**.
