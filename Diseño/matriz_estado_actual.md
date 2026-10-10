# MATRIZ DE ESTADO ACTUAL — WEB-AGENT
**Clasificación:** Diagnóstico Estático del Repositorio y Brecha Operativa  
**Referencia:** Código fuente en `src/`, `tests/`, `skills/` y perfiles existentes

---

## 1. Declaración de Honestidad Técnica
Para evitar falsas expectativas ante los evaluadores y orientar la ejecución de las 20:00:
- **Implementado:** Código existente, observable y funcional en el repositorio.
- **Diseñado:** Especificado arquitectónicamente en esta documentación, pendiente de codificación.
- **Validado:** Comprobado empíricamente mediante pruebas automatizadas que pasan.
- **Desplegado:** Servicio accesible desde Internet bajo el contrato oficial.

---

## 2. Matriz de Capacidades y Brecha Técnica

| Capacidad | Evidencia en el Repositorio | Estado Actual | Brecha hacia la Entrega (20:00) |
| :--- | :--- | :--- | :--- |
| **Sesiones independientes de navegador** | `browser/session.py` (`SecureBrowserSession`) | **Implementada** (a nivel de contexto Playwright; no equivale a aislamiento de host/contenedor) | Requiere parametrización para permitir transporte WebSocket del escritorio remoto. |
| **Observación DOM acotada** | `browser/observe.py` (`DOMObserver`) | **Implementada con limitaciones** (roles no siempre estándar, IDs e0..e30) | Limpiar normalización de roles accesibles para modo secundario. |
| **Propuestas estructuradas (Contrato)** | `core/schemas.py` (`ProposedAction`) | **Implementada para DOM** | Extender con acciones espaciales de Nivel 5 (`click_coordinates`, etc.). |
| **Bucle del Runner** | `core/runner.py` (`AgentRunner._run_loop`) | **Implementado** | Enlazar `observation_id` obligatorio y soportar ramas visuales. |
| **Adaptador de Modelo (LLM)** | `models/adapters/openrouter.py` | **Implementado solo para texto** | Modificar para recibir imágenes vía canal multimodal (base64/URL). |
| **Validación de orígenes y red** | `security/` (`NetworkPolicyValidator`) | **Implementada parcialmente** | Unificar llamada de validación; no bloquear WebSockets autorizados. |
| **Auditoría redactada** | `audit/events.py` (`AuditLogger`) | **Implementada en JSONL** | Funcional como registro secuencial; no es criptográfica ni distribuida. |
| **Presupuesto de inferencia** | `execution/budgets.py` (`BudgetManager`) | **Componente existente, integración incompleta** | La CLI no lo pasa al runner; cálculo asume costo plano de tokens. |
| **Estrategias de recuperación** | `recovery/strategies.py` | **Existe, pero no se invoca en el runner** | Integrar en el bucle como paso de diagnóstico, sin reintentos ciegos. |
| **Captura visual para el VLM** | Ausente en `runner.py` | **Pendiente (Diseñada)** | Implementar `page.screenshot()` y formateo en observación visual. |
| **Clics y tipeo por coordenadas** | Ausente en `actions.py` | **Pendiente (Diseñada)** | Implementar emulación de coordenadas en píxeles CSS del viewport. |
| **Skills de operación legacy** | Descritas en documentación | **Pendiente (Diseñadas)** | Codificar procedimientos nativos en `src/web_agent/skills/`. |
| **Autenticación en escritorio remoto** | Ausente | **Pendiente (Diseñada)** | Implementar flujo de acceso con resolución de secreto en ejecución. |
| **Transporte noVNC / Guacamole** | Bloqueado en `session.py` (`route_web_socket`) | **Incompatible actualmente** | Permitir WS/WSS hacia el host y puerto autorizado del escritorio. |
| **API REST con Bearer Token** | `entrypoint.py` es un script CLI | **Pendiente (Diseñada)** | Implementar servidor HTTP (FastAPI/Uvicorn) con middleware de autenticación. |
| **Gestión asíncrona de tareas** | Ausente | **Pendiente (Diseñada)** | Implementar registro de tareas en memoria con supervisor y deadline de 300 s. |
| **Verificación de resultados visuales** | `_verify_success()` depende de DOM/URL | **Pendiente (Diseñada)** | Crear verificador basado en evidencia observable en pantalla. |
| **Despliegue público en Internet** | Sin infraestructura configurada | **Pendiente (No demostrado)** | Configurar Dockerfile / VPS con túnel o IP pública para las 20:00. |

---

## 3. Plan de Mitigación Inmediato
1. **P0 (Red):** Desbloquear WebSockets específicos para la URL de escritorio recibida en `session.py`.
2. **P0 (Percepción):** Construir `VisualObserver` que capture viewport y emita `observation_id`.
3. **P0 (Modelo):** Actualizar `OpenRouterModelAdapter` para enviar la imagen en el array de contenido multimodal.
4. **P0 (Ejecutor):** Añadir `click_coordinates` y `type_at_coordinates` calculados en píxeles CSS `[0, W-1]`, `[0, H-1]`.
5. **P0 (Servicio):** Levantar servidor REST con endpoints `/health`, `/tasks`, `/tasks/{id}` y Bearer auth.
