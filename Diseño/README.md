# CARPETA DE DISEÑO DE SISTEMAS — WEB-AGENT
**Entrega Oficial:** Hito Systems Design (Cierre 12:00)  
**Reto:** Agente Autónomo para Sistemas Legacy sin API por Pantalla (5 Apps Desconocidas)  
**Clasificación:** Fuente de Verdad Arquitectónica y Referencia de Implementación

---

## 1. Entregables Oficiales del Cierre 12:00

| Entregable | Archivo | Descripción |
| :--- | :--- | :--- |
| **Doc de Diseño (1 Página)** | [**`resumen_ejecutivo_unapagina.pdf`**](./resumen_ejecutivo_unapagina.pdf) | **PDF de 1 página real**. Resumen ejecutivo de alta densidad técnica listo para entrega directa. |
| **Diagrama de Arquitectura** | [**`diagrama_arquitectura.svg`**](./diagrama_arquitectura.svg) | **Diagrama vectorial completo entregado por separado** (sin recortes, 1200x960 px). |
| **Imagen Alternativa** | [**`diagrama_arquitectura.png`**](./diagrama_arquitectura.png) | Renderizado PNG en alta resolución del diagrama vectorial. |
| **Código del Diagrama** | [**`diagrama_arquitectura.mermaid`**](./diagrama_arquitectura.mermaid) | Definición formal del diagrama en código Mermaid. |

---

## 2. Documento Maestro y Especificaciones Técnicas

| Archivo | Contenido Principal |
| :--- | :--- |
| [**`WEB-AGENT_documento_maestro_de_diseno.md`**](./WEB-AGENT_documento_maestro_de_diseno.md) | **Documento Maestro de Diseño Técnico (Normativo)**: Especificación exhaustiva (22 secciones, principios, contratos, seguridad, fases y reglas para agentes). |
| [**`web-agent_arquitectura_implementacion.md`**](./web-agent_arquitectura_implementacion.md) | Informe técnico complementario de arquitectura, implementación observada y plan de fases ([PDF generado](./web-agent_arquitectura_implementacion.pdf)). |
| [**`web-agent_diagrama_arquitectura.md`**](./web-agent_diagrama_arquitectura.md) | Especificación del diagrama objetivo con leyenda de componentes ([PDF A3](./web-agent_diagrama_arquitectura.pdf)). |
| [**`resumen_ejecutivo_unapagina.md`**](./resumen_ejecutivo_unapagina.md) | Texto fuente del resumen de 1 página (Objetivo, Arquitectura, Operación Visual, Skills, Seguridad, Verificación, Contrato, Validación). |
| [**`resumen_ejecutivo_unapagina.html`**](./resumen_ejecutivo_unapagina.html) | Plantilla HTML con CSS de impresión A4 que compila el PDF de 1 página. |
| [**`doc_diseno_sistemas.md`**](./doc_diseno_sistemas.md) | Documento preliminar de diseño de sistemas (visión técnica sintetizada). |
| [**`matriz_estado_actual.md`**](./matriz_estado_actual.md) | Diagnóstico honesto del repositorio: Implementado vs. Diseñado vs. Validado vs. Desplegado. |
| [**`arquitectura_nivel5_gui_canvas.md`**](./arquitectura_nivel5_gui_canvas.md) | Especificación del Modo Principal (`visual_only`), geometría CSS, y periféricos virtuales. |
| [**`contrato_acciones_observaciones.md`**](./contrato_acciones_observaciones.md) | Contratos tipados de `VisualObservation` (`observation_id`) y `ProposedAction` (coordenadas $[0, W-1]$). |
| [**`catalogo_skills.md`**](./catalogo_skills.md) | Procedimientos nativos en código (sin MCP externo): Auth, FormFill, PopupManager, ExportTracker, Discovery, Retry. |
| [**`especificacion_contrato_entrypoint.md`**](./especificacion_contrato_entrypoint.md) | Especificación de la API REST para el evaluador: Bearer auth, `GET /health`, `POST /tasks` (< 2s), `GET /tasks/{id}` (300 s). |
| [**`seguridad_y_modelo_amenazas.md`**](./seguridad_y_modelo_amenazas.md) | Red, transporte WebSocket autorizado, mitigación SSRF, redacción de credenciales y auditoría append-only fail-stop. |
| [**`verificacion_y_recuperacion.md`**](./verificacion_y_recuperacion.md) | Verificación basada en evidencia observable (no en $\Delta$ visual ciego) y recuperación sin bucles repetitivos. |
| [**`plan_validacion.md`**](./plan_validacion.md) | Estrategia de pruebas unitarias, de seguridad, sintéticas y de integración del servicio REST. |
| [**`despliegue_y_operacion.md`**](./despliegue_y_operacion.md) | Guía de contenerización Docker, variables de entorno y supervisión operativa para el cierre de las 20:00. |

---

## 3. Registro de Decisiones de Arquitectura (ADRs)

Ubicados en la subcarpeta [`decisiones/`](./decisiones/) (conforme a la Sección 21 del Documento Maestro):
- [**`ADR-001 — Playwright como adaptador de interacción`**](./decisiones/ADR-001-vision-como-modo-principal.md)
- [**`ADR-002 — Modo visual principal; DOM opcional`**](./decisiones/ADR-002-ejecucion-determinista.md)
- [**`ADR-003 — Propuesta del modelo separada de la ejecución`**](./decisiones/ADR-003-limites-dependencias-y-proveedores.md)
- [**`ADR-004 — Pydantic como contrato interno`**](./decisiones/ADR-004-pydantic-contrato-interno.md)
- [**`ADR-005 — FastAPI/ASGI para la API REST`**](./decisiones/ADR-005-fastapi-asgi-rest.md)
- [**`ADR-006 — Proveedor de inferencia intercambiable`**](./decisiones/ADR-006-proveedor-inferencia-intercambiable.md)
- [**`ADR-007 — Observaciones con identidad y geometría`**](./decisiones/ADR-007-observaciones-identidad-geometria.md)
- [**`ADR-008 — Auditoría obligatoria en el flujo`**](./decisiones/ADR-008-auditoria-obligatoria.md)
- [**`ADR-009 — Deadline supervisado absoluto (300 s)`**](./decisiones/ADR-009-deadline-supervisado-absoluto.md)
- [**`ADR-010 — MCP fuera de la ruta crítica inicial`**](./decisiones/ADR-010-mcp-fuera-ruta-critica.md)
- [**`ADR-011 — Servicio modular antes que microservicios`**](./decisiones/ADR-011-servicio-modular.md)

---

## 4. Antecedentes
- [**`constructor_web-agent.md`**](./constructor_web-agent.md): Documento inicial de arranque del proyecto, conservado como antecedente histórico.
