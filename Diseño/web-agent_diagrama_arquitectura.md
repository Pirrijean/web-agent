---
title: "WEB-AGENT — Diagrama de arquitectura"
document_type: architecture_diagram
language: es
version_date: 2026-10-10
architecture_status: "Arquitectura objetivo; las etiquetas de estado reflejan revisión estática, no pruebas ejecutadas"
---

# WEB-AGENT — Diagrama de arquitectura

## Propósito

Este documento contiene el diagrama de arquitectura objetivo de WEB-AGENT en formato Mermaid, más una descripción textual de los componentes y sus fronteras. Está pensado para lectura por agentes y herramientas que procesan Markdown.

**Principio central:** GUI/canvas es la modalidad principal; DOM es opcional. El modelo produce propuestas no confiables. Un núcleo propio valida, autoriza, ejecuta, verifica y audita cada acción.

> **Advertencia de estado:** este diagrama representa el diseño objetivo. Las etiquetas `EXISTENTE`, `PARCIAL` y `PENDIENTE` proceden de la revisión estática resumida en el material fuente; no constituyen evidencia de pruebas funcionales o despliegue.

## Leyenda de estados

- **Existente:** el material fuente identifica una base o módulo en el código compartido, sin certificación de calidad funcional.
- **Parcial:** la base existe, pero la integración o las garantías son incompletas.
- **Pendiente / diseñado:** forma parte de la arquitectura objetivo, pero su implementación no está demostrada.
- **Sistema externo:** fuera del núcleo de control de WEB-AGENT.

## Diagrama principal

```mermaid
flowchart TB
    CLIENTE["EVALUADOR / CLIENTE<br/>Tarea en lenguaje natural<br/>URL del escritorio y credenciales<br/>Authorization: Bearer"]

    subgraph CONTROL["PLANO DE CONTROL — PENDIENTE DE COMPLETAR"]
        API["API REST AUTENTICADA<br/>GET /health<br/>POST /tasks: task_id en menos de 2 s<br/>GET /tasks/{task_id}: estado y resumen<br/>Estado: PENDIENTE"]
        SUPERVISOR["REGISTRO DE TAREAS + SUPERVISOR<br/>Admisión, estado público, worker aislado<br/>Cancelación acotada<br/>Deadline monotónico de 300 s, incluida la espera<br/>Estado: PENDIENTE"]
    end

    RUNNER["AGENT RUNNER — MÁQUINA DE ESTADOS<br/>Observar → Proponer → Validar → Ejecutar → Verificar<br/>Continuar / recuperar / finalizar<br/>Límites de pasos y tiempo<br/>Estado: BASE EXISTENTE; AMPLIAR"]

    subgraph PERCEPCION["PERCEPCIÓN — GUI/CANVAS PRINCIPAL; DOM OPCIONAL"]
        VISUAL["OBSERVADOR VISUAL<br/>Captura del viewport (PNG)<br/>observation_id + geometría CSS<br/>Coordenadas normalizadas [0,1000]<br/>Sin inspección DOM de la app<br/>Estado: PENDIENTE"]
        DOM["OBSERVADOR DOM<br/>Roles/nombres y referencias temporales<br/>Snapshot, unicidad y vigencia<br/>Solo cuando el modo lo permite<br/>Estado: EXISTENTE; CORREGIR"]
        MODEL["ADAPTADOR LLM / VLM<br/>Objetivo + observación + historial acotado<br/>Propuesta estructurada no confiable<br/>Sin credenciales ni herramientas<br/>Texto: EXISTENTE / Visión: PENDIENTE"]
    end

    POLICY["POLÍTICA + VALIDACIÓN CONTEXTUAL<br/>Validación de esquema, observation_id,<br/>geometría y deadline<br/>Objetivo, ámbito, riesgo y autorización semántica<br/>Una coordenada válida no equivale a permiso de negocio<br/>Estado: BASE EXISTENTE; FORTALECER"]

    subgraph EXECUTION["EJECUCIÓN CONTROLADA"]
        SKILLS["SKILLS NATIVAS<br/>Auth/bootstrap · formularios canvas · popups<br/>Exportación · descubrimiento · recuperación<br/>Precondiciones y postcondiciones<br/>Sin bypass de políticas<br/>Estado: DISEÑADAS; INTEGRACIÓN PENDIENTE"]
        PLAYWRIGHT["EJECUTOR + SESIÓN PLAYWRIGHT<br/>DOM permitido + mouse + teclado + scroll<br/>Eventos acotados, cleanup y aislamiento<br/>HTTP(S)/WS(S) solo hacia destinos autorizados<br/>DOM: EXISTENTE / GUI: PENDIENTE"]
    end

    REMOTE["ESCRITORIO REMOTO / APLICACIÓN<br/>Sistema externo sin API<br/>Aplicación desconocida o canvas<br/>Red restringida al transporte autorizado"]

    subgraph CROSSCUTTING["CONTROLES TRANSVERSALES"]
        BUDGET["PRESUPUESTO DE INFERENCIA<br/>Llamadas · tokens · imágenes · tiempo · coste<br/>BudgetManager: integración incompleta"]
        AUDIT["AUDITORÍA + EVIDENCIA<br/>Propuesta · política · acción · verificación · estado final<br/>JSONL redactado; capturas/trazas protegidas<br/>Fallo de auditoría bloquea nuevas actuaciones<br/>JSONL: EXISTENTE / Evidencia visual: PENDIENTE"]
    end

    subgraph RESULT["VERIFICACIÓN Y RESULTADO"]
        VERIFY["VERIFICACIÓN + RECUPERACIÓN<br/>Postcondición tras operaciones relevantes<br/>Estados: verificado / no_verificado / incierto<br/>DONE y pixel-diff no prueban éxito<br/>No repetir guardados inciertos<br/>DOM: BASE EXISTENTE / GUI: PENDIENTE"]
        FINAL["ESTADO FINAL / RESULTADO<br/>Resumen y estado público con motivo seguro<br/>Éxito solo con evidencia suficiente<br/>Timeout puede dejar efecto remoto incierto<br/>REST + supervisor: PENDIENTES"]
    end

    CLIENTE -->|HTTPS| API
    API --> SUPERVISOR
    SUPERVISOR --> RUNNER

    RUNNER --> VISUAL
    RUNNER --> DOM
    VISUAL --> MODEL
    DOM --> MODEL
    MODEL --> POLICY
    POLICY --> SKILLS
    SKILLS --> PLAYWRIGHT
    PLAYWRIGHT <--> REMOTE
    REMOTE -->|captura posterior / estado observado| VISUAL
    REMOTE -->|estado DOM si está permitido| DOM

    PLAYWRIGHT --> VERIFY
    VERIFY -->|continuar / reobservar| RUNNER
    VERIFY -->|finalización con evidencia| FINAL
    FINAL --> SUPERVISOR
    SUPERVISOR -->|estado consultable| API
    API --> CLIENTE

    RUNNER -. consume y limita .-> BUDGET
    MODEL -. llamada sujeta a presupuesto .-> BUDGET
    POLICY -. decisión y rechazo .-> AUDIT
    PLAYWRIGHT -. acción y resultado .-> AUDIT
    VERIFY -. resultado y evidencia .-> AUDIT
    AUDIT -. autoriza continuidad de la ejecución .-> RUNNER

    classDef existing fill:#E8F5E9,stroke:#2E7D32,color:#123B1E;
    classDef partial fill:#FFF4E5,stroke:#B96B00,color:#5A3500;
    classDef pending fill:#F3E8FF,stroke:#7040A0,color:#32164D;
    classDef external fill:#EAF2F8,stroke:#516074,color:#14213D;
    class API,SUPERVISOR,VISUAL,FINAL pending;
    class RUNNER,DOM,PLAYWRIGHT,POLICY,VERIFY,BUDGET partial;
    class SKILLS,MODEL pending;
    class CLIENTE,REMOTE external;
    class AUDIT partial;
```

## Secuencia de ejecución

1. El evaluador envía la tarea y las credenciales o parámetros del escritorio al endpoint REST autenticado.
2. La API valida el token y el payload, registra la tarea, fija los límites y devuelve `task_id` sin esperar a que termine la automatización.
3. El supervisor administra el estado público, el worker y el deadline monotónico de 300 segundos.
4. El runner solicita una observación visual y/o DOM, según el modo permitido por la tarea y el entorno.
5. El adaptador de modelo recibe el objetivo y la observación; devuelve una propuesta estructurada no confiable. El proveedor no obtiene acceso directo a herramientas ni credenciales.
6. El núcleo valida esquema, identidad y vigencia de la observación, geometría, contexto, objetivo y política de autorización.
7. Una skill opcional organiza pasos reutilizables, pero toda acción se ejecuta mediante el mismo ejecutor y sin eludir las políticas.
8. Playwright emite eventos de interfaz limitados a la sesión y a los destinos de red autorizados.
9. El sistema vuelve a observar y verifica una postcondición observable. Si el resultado no puede determinarse, se informa `incierto` en lugar de asumir éxito o repetir ciegamente una operación persistente.
10. La auditoría registra los hitos. Si un evento obligatorio no puede escribirse, la ejecución no debe continuar como si nada hubiera pasado.
11. El supervisor publica el estado final y la API permite consultar un resumen que no expone secretos ni evidencia sensible.

## Componentes y responsabilidades

| Componente | Responsabilidad | Estado resumido |
|---|---|---|
| Evaluador / cliente | Proporcionar tarea, acceso al escritorio y autorización Bearer. | Actor externo. |
| API REST | Autenticación, validación, admisión rápida y consulta de estado. | Pendiente según la fuente. |
| Registro + supervisor | Estado de tarea, deadline de 300 s, worker, cancelación y publicación del resultado. | Pendiente. |
| AgentRunner | Máquina de estados, límites, ciclo de propuesta/acción/verificación. | Base existente; debe ampliarse. |
| Observador visual | Captura del viewport, geometría CSS e ID de observación; opera sin inspección DOM en `visual_only`. | Pendiente. |
| Observador DOM | Roles, nombres y referencias temporales cuando la modalidad lo permite. | Existente; requiere correcciones. |
| Adaptador LLM/VLM | Convertir objetivo y observación en una propuesta estructurada. | Texto existente; visión pendiente según la fuente. |
| Política y validador | Rechazar propuestas inválidas, obsoletas o fuera del alcance autorizado. | Base existente; fortalecer vínculo contextual. |
| Skills nativas | Reutilizar procedimientos de autenticación, formularios, popups, exportación, descubrimiento y recuperación. | Diseñadas; integración pendiente. |
| Ejecutor + Playwright | Traducir acciones permitidas a eventos de interfaz acotados y limpiar recursos. | DOM existente; GUI visual pendiente. |
| Aplicación remota | Superficie externa que recibe mouse, teclado y otros eventos permitidos. | Sistema externo. |
| Presupuesto | Limitar llamadas, tokens, imágenes, tiempo y coste de inferencia. | Integración incompleta. |
| Auditoría | Registrar propuestas, decisiones, acciones, verificaciones y estado final con redacción. | JSONL existente; evidencia visual pendiente. |
| Verificador / recuperación | Comprobar postcondiciones y recuperarse sin duplicar operaciones persistentes. | Base DOM existente; verificación GUI pendiente. |
| Estado final | Resumir resultados y motivo de fallo/bloqueo sin exponer secretos. | Depende de REST/supervisor pendientes. |

## Límites y garantías que el diagrama no debe ocultar

- La arquitectura objetivo no es evidencia de que todos los componentes estén implementados.
- `DONE`, un click aceptado, una navegación completada o una diferencia de píxeles no prueban por sí solos el éxito de negocio.
- Una coordenada válida dentro del viewport no demuestra que la acción esté autorizada por el objetivo.
- Una observación obsoleta o una geometría incompatible debe provocar rechazo y nueva observación.
- BrowserContext aísla parte del estado del navegador, pero no sustituye aislamiento de proceso, filesystem ni red.
- El uso de WebSockets debe limitarse a destinos explícitamente autorizados; no se deben abrir destinos arbitrarios.
- El plazo de 300 segundos debe aplicarse en el supervisor, independientemente del perfil de tarea recibido.
- Un timeout después de una acción persistente puede dejar efectos remotos inciertos; detener el worker no revierte la acción.
- JSONL redactado no equivale a una auditoría criptográficamente inmutable.
- El proveedor de inferencia es una dependencia externa y no debe recibir credenciales ni capacidad de ejecución.
- El uso de proveedores externos queda condicionado a las reglas oficiales de la evaluación.

## Contratos e interfaces clave

- **REST:** `GET /health`, `POST /tasks`, `GET /tasks/{task_id}`; Bearer y esquema de respuesta sujetos al contrato oficial.
- **Tiempo:** deadline monotónico de máximo 300 s, incluida la espera de la tarea.
- **Observación:** `observation_id`, timestamps, sesión/página, geometría CSS, tamaño/transformación de imagen y referencia controlada.
- **Propuesta:** salida estructurada no confiable ligada a una observación y al contexto de tarea.
- **Autorización:** validación de esquema más política contextual y semántica; el esquema sintáctico por sí solo no concede permiso.
- **Resultado de verificación:** `verificado`, `no_verificado` o `incierto`.
- **Auditoría:** correlación por `task_id`, `run_id` y `observation_id`; redacción de secretos y protección de evidencias.

## Referencia al documento técnico complementario

Para los detalles de los defectos reportados, prioridades, fases, pruebas y criterios de aceptación, consultar `web-agent_arquitectura_implementacion.md`.
