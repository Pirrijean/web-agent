# DOCUMENTO MAESTRO DE DISEÑO DE SISTEMAS — WEB-AGENT
**Reto:** Agente Autónomo para Operación de Sistemas Legacy sin API por Pantalla (5 Apps Desconocidas)  
**Entrega Oficial:** Hito Systems Design (Cierre 12:00) | **Versión:** 2.1 (Visión como Modo Principal · DOM Opcional)  
**Clasificación:** Documentación Técnica de Referencia para Implementación y Evaluación  
> **Nota de Referencia Normativa:** La especificación exhaustiva y definitiva para la fase de construcción y reglas de implementación se encuentra en [**`WEB-AGENT_documento_maestro_de_diseno.md`**](./WEB-AGENT_documento_maestro_de_diseno.md).

---

## 1. Planteamiento del Problema y Tesis de Arquitectura

Las organizaciones dependen de sistemas de gestión críticos desarrollados hace más de 15 años que carecen de APIs y documentación técnica, y cuyos proveedores originales desaparecieron. Estas aplicaciones se operan de forma estrictamente manual a través de pantallas: clics, formularios densos, cuadros de diálogo emergentes y exportación secuencial de reportes.

El objetivo de **`web-agent`** es actuar como un nuevo operador humano con adaptabilidad inmediata (*zero-shot*) sobre 5 aplicaciones desconocidas:
1. Recibe la instrucción en lenguaje natural y las credenciales de acceso al escritorio remoto.
2. Opera mediante periféricos virtuales (ratón y teclado) sin requerir reentrenamiento previo ni librerías externas que resuelvan la tarea por el equipo (prohibición de frameworks como *Browser Use* o *LangChain*).
3. Separa el razonamiento de la ejecución: **el modelo propone planes bajo incertidumbre; el núcleo propio valida permisos, ejecuta deterministamente y comprueba la evidencia observable de éxito.**

---

## 2. Decisiones Clave de Arquitectura (ADRs Resumidos)

| Dimensión | Decisión de Ingeniería (ADR) | Justificación Técnica |
| :--- | :--- | :--- |
| **Prioridad de Percepción** | **Visión como modo principal (`visual_only`)** (ADR-001) | Las aplicaciones del reto se transmiten mediante escritorio remoto al navegador; los controles internos no existen en el DOM, solo en píxeles. |
| **Modo DOM Secundario** | **DOM Opcional (`dom` / `auto`)** (ADR-001) | Se conserva para páginas web estándar cuando las reglas permitan inspección de HTML. |
| **Ejecución Determinista** | **Frontera estricta de políticas** (ADR-002) | El modelo es entrada no confiable. Las primitivas de ratón y teclado se ejecutan mediante código propio sin acceso a scripts arbitrarios. |
| **Skills Nativas** | **Procedimientos propios en Python** (ADR-002 / ADR-003) | Micro-habilidades para relleno de campos, lectura de modales y seguimiento de reportes sin descargar servidores MCP externos. |
| **Límites de Dependencias** | **Sin servicios de automatización externos** (ADR-003) | La orquestación, estados, verificación y supervisión son 100% propietarios del proyecto. Inferencia VLM sujeta a reglas del evento. |
| **Supervisión de Plazo** | **Supervisor asíncrono con deadline de 300 s** | Garantiza la respuesta en `< 2s` en creación y la terminación limpia al cumplirse los 5 minutos. |

---

## 3. Arquitectura del Sistema: Módulos y Flujo de Control

```
                 ┌────────────────────────────────────────────────────────┐
                 │             EVALUADOR OFICIAL (HACKATHON)              │
                 │        Header: Authorization: Bearer <token>           │
                 └───────────────────────────┬────────────────────────────┘
                                             │ HTTP REST
                                             ▼
                 ┌────────────────────────────────────────────────────────┐
                 │           API REST PROPIA (Entrypoint FastAPI)         │
                 │  - GET /health  (200 OK)                               │
                 │  - POST /tasks  (Encola async < 2s -> task_id)         │
                 │  - GET /tasks/{id} (en_curso, terminada, fallida)      │
                 └─────────────┬────────────────────────────┬─────────────┘
                               │                            │
                     Registra  │                            │ Dispara tarea (< 2s)
                               ▼                            ▼
                 ┌───────────────────────────┐  ┌─────────────────────────┐
                 │  REGISTRO DE TAREAS Y     │  │   SUPERVISOR ASÍNCRONO  │
                 │  ESTADOS (STORE)          │  │   Deadline: 300 s       │
                 └─────────────▲─────────────┘  └───────────┬─────────────┘
                               │                            │
                               │ Actualiza estado final     ▼
                 ┌─────────────┴──────────────────────────────────────────┐
                 │           AGENT RUNNER (Núcleo Determinista)           │
                 │   Bucle: Observar ➔ Proponer ➔ Validar ➔ Actuar ➔      │
                 │          Verificar ➔ Auditar                           │
                 └───────┬──────────────────────────┬──────────────┬──────┘
                         │                          │              │
        ┌────────────────┴──────────────┐           │              │
        ▼                               ▼           │              ▼
┌───────────────────────┐   ┌───────────────────────┐│ ┌────────────────────────┐
│ MODO PRINCIPAL:       │   │ MODO OPCIONAL:        ││ │ VALIDADOR DE POLÍTICAS │
│ VisualObserver        │   │ DOMObserver           ││ │ (Pydantic Fail-Closed) │
│ - Viewport Screenshot │   │ - Semantic Tree       ││ │ - observation_id check │
│ - observation_id      │   │ - Roles accesibles    ││ │ - 0 ≤ X < W, 0 ≤ Y < H │
│ - Coordenadas [0..1k] │   │ - IDs e0..e30         ││ │ - Risk R0..R2 (R3 stop)│
└───────────────────────┘   └───────────────────────┘│ └────────────────────────┘
        │                               │            │             │
        └────────────────┬──────────────┘            │             │
                         ▼                           │             │
                 ┌──────────────────────────┐        │             │
                 │  ADAPTADOR LLM / VLM     │        │             │
                 │  (Inferencia Externa)    │        │             │
                 └───────────────┬──────────┘        │             │
                                 │ Propuesta         ▼             ▼
                                 └──────────► ┌────────────────────────┐
                                              │ SKILLS NATIVAS         │
                                              │ - DesktopAuth          │
                                              │ - CanvasFormFill       │
                                              │ - LegacyPopupManager   │
                                              │ - ReportExportTracker  │
                                              └─────────────┬──────────┘
                                                            │ Acciones
                                                            ▼
                                              ┌────────────────────────┐
                                              │ EJECUTOR DETERMINISTA  │
                                              │ (Playwright Perifér.)  │
                                              │ - mouse / keyboard     │
                                              │ - WebSockets autoriz.  │
                                              └─────────────┬──────────┘
                                                            │
                                                            ▼
                                              ┌────────────────────────┐
                                              │ VERIFICACIÓN BASADA    │
                                              │ EN EVIDENCIA           │
                                              │ - Diagnóstico visual Δ │
                                              │ - Comprobación de fin  │
                                              └─────────────┬──────────┘
                                                            │
                                                            ▼
                                              ┌────────────────────────┐
                                              │ AUDITORÍA REDACTADA    │
                                              │ (JSONL append-only)    │
                                              └────────────────────────┘
```

---

## 4. Operación Visual (Nivel 5 — Modo Principal)

### 4.1. Transformación Matemática a Píxeles CSS de Viewport
Playwright emite eventos en píxeles CSS del viewport:
$$X_{css} = \operatorname{round}\left(\frac{x_{norm}}{1000} \cdot (W_{css} - 1)\right)$$
$$Y_{css} = \operatorname{round}\left(\frac{y_{norm}}{1000} \cdot (H_{css} - 1)\right)$$
Invariante estricto: $0 \le X_{css} < W_{css}$ y $0 \le Y_{css} < H_{css}$.

### 4.2. Vigencia y Coherencia Temporal
Toda acción espacial debe enviar el `observation_id` vigente. Si la geometría cambia, se navega o expira la antigüedad máxima (15 s), el runner invalida la propuesta y fuerza una nueva observación.

---

## 5. Skills Nativas Modulares (Sin MCP Externo)
1. **`DesktopAuthAndBootstrap`:** Resuelve la autenticación y conexión sin enviar contraseñas en prompts ni logs.
2. **`CanvasFormFill`:** Foco visual, selección (`Ctrl+A`), borrado y mecanografiado con delay humano (20 ms).
3. **`LegacyPopupManager`:** Lee e interpreta el contenido del diálogo modal; prohíbe pulsar `Enter` a ciegas.
4. **`ReportExportTracker`:** Gestiona menús de archivo y guardado; distingue descargas remotas de eventos locales de navegador.
5. **`ZeroShotDiscovery`:** Mapea la interfaz mediante hipótesis revisables sin asumir cuadrantes universales.
6. **`SelfHealingRetry`:** Reobserva y diagnostica; prohíbe bucles ciegos de clic $\to$ doble clic $\to$ Escape.

---

## 6. Seguridad, Red y Modelo de Amenazas
- **Transporte WebSocket:** Se autorizan explícitamente los WebSockets necesarios para el escritorio remoto (`desktop.url`). Se elimina el bloqueo indiscriminado previo.
- **Mitigación SSRF:** Intercepción a nivel de red Playwright para bloquear loopbacks y metadatos de nube.
- **Protección de Credenciales:** Sin promesas infundadas de cifrado en memoria; protección efectiva mediante resolución inmediata en ejecución y redacción rigurosa en logs.
- **Auditoría Fail-Stop:** Archivo JSONL append-only que detiene el sistema si falla la persistencia.

---

## 7. Verificación Basada en Evidencia
- La diferencia visual ($\Delta$ imagen) es una señal de diagnóstico, no prueba de éxito ni de fallo.
- El éxito final exige evidencia observable: registros consultables en grillas, mensajes de guardado o confirmación de exportación accesible.
- Ante incertidumbre, el agente no inventa resultados: marca la tarea como `fallida` y emite un resumen transparente.

---

## 8. Contrato Oficial del Entrypoint (Despliegue a las 20:00)
- `Authorization: Bearer <token>` obligatorio en toda llamada.
- `GET /health` responde `200` cuando el servicio esté listo.
- `POST /tasks` responde en `< 2s` con `{"task_id": "…"}` y despacha el supervisor asíncrono.
- `GET /tasks/{task_id}` devuelve `status` (`en_curso`, `terminada`, `fallida`) y `resumen` descriptivo.
- Límite global estricto de 5 minutos (300 segundos) por tarea con cancelación forzada de procesos si se alcanza el deadline.
