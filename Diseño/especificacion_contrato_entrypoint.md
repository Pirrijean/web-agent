# ESPECIFICACIÓN DEL CONTRATO DE ENTRYPOINT REST API
**Servicio:** `web-agent-server`  
**Protocolo:** HTTP/1.1 y HTTP/2 sobre TLS (HTTPS)  
**Ventana Operativa:** Desplegado y accesible desde Internet desde las 20:00 hasta el fin del Demo Day  
**Referencia:** Requisitos Oficiales del Reto y Separación de Supuestos de Diseño

---

## 1. Requisitos Confirmados vs Campos Pendientes de Confirmación

| Dimensión | Requisito Oficial Confirmado | Detalle / Supuesto Pendiente de Confirmar |
| :--- | :--- | :--- |
| **Autenticación** | `Authorization: Bearer <token>` en **todas** las llamadas | El token exacto del equipo se configurará en variables de entorno. |
| **Salud del Servicio** | `GET /health` responde `200` cuando esté listo | Estructura de cuerpo libre (ej. `{"status": "ok"}`). |
| **Creación de Tarea** | `POST /tasks` responde en **menos de 2 segundos** con `{"task_id": "…"}` | Recibe tarea en lenguaje natural y escritorio (URL + contraseña). Nombres exactos de campos en JSON pendientes de confirmar contra README oficial. |
| **Consulta de Tarea** | `GET /tasks/{task_id}` devuelve estado y resumen | Estados públicos confirmados: `en_curso`, `terminada`, `fallida`. Formato exacto del resumen según README oficial. |
| **Tiempo de Ejecución** | Cada tarea tiene un límite de **5 minutos (300 segundos)** | Se evaluará sobre apps no vistas en escritorio limpio de evaluación. |

---

## 2. Endpoints Oficiales del Contrato

### 2.1 `GET /health`
- **Cabecera Requerida:** `Authorization: Bearer <token>`
- **Respuesta (`200 OK`):**
  ```json
  {
    "status": "healthy",
    "service": "web-agent",
    "version": "2.0.0"
  }
  ```
- **Error (`401 Unauthorized`):** Si falta el token o no coincide.
- **Error (`503 Service Unavailable`):** Si el navegador o adaptadores presentan fallos no recuperables.

---

### 2.2 `POST /tasks`
- **Cabecera Requerida:** `Authorization: Bearer <token>`
- **Comportamiento Asíncrono Inmediato:** **No espera al inicio del navegador ni a la primera inferencia.** Autentica la solicitud, valida la estructura del payload, registra la tarea con un deadline monotónico de 300 segundos, despacha el supervisor asíncrono y responde en menos de 2 segundos.
- **Cuerpo Propuesto de Petición (`application/json`):**
  ```json
  {
    "task": "Generar reporte de clientes y exportar a Excel",
    "desktop": {
      "url": "https://eval.desktop.internal:6080/vnc.html",
      "password": "PasswordSecreta2026"
    }
  }
  ```
- **Respuesta Inmediata (`200 OK` / `202 Accepted`):**
  ```json
  {
    "task_id": "task_abc12345"
  }
  ```

---

### 2.3 `GET /tasks/{task_id}`
- **Cabecera Requerida:** `Authorization: Bearer <token>`
- **Mapeo de Estados Internos a Estados Públicos Oficiales:**

| Estado Interno del Runner | Estado Público Oficial | Justificación |
| :--- | :--- | :--- |
| En ejecución (`RUNNING`) | **`en_curso`** | El agente está interactuando activamente con el sistema. |
| `SUCCEEDED_VERIFIED` | **`terminada`** | Condición final verificada con evidencia observable. |
| `FAILED` | **`fallida`** | Error no recuperable, timeout de 300 s o fallo interno. |
| `BLOCKED` (Violación de Política) | **`fallida`** | Intento de acción no autorizada o fuera de política. |
| `NEEDS_HUMAN_REVIEW` | **`fallida`** | No existe canal de aprobación humana en evaluación autónoma. |

- **Ejemplo de Respuesta (`200 OK`):**
  ```json
  {
    "task_id": "task_abc12345",
    "status": "terminada",
    "resumen": "Reporte de clientes generado y exportado exitosamente tras validar el cierre de la ventana modal de procesamiento.",
    "steps_executed": 6,
    "elapsed_seconds": 118.4
  }
  ```

---

## 3. Manejo del Deadline de 5 Minutos y Cancelación de Recursos

1. **Deadline Monotónico:**
   $$\text{deadline} = \operatorname{time.monotonic}() + 300.0$$
   Compartido por el supervisor, el runner, las llamadas de red y el navegador. Los timeouts locales se calculan como $\min(\text{timeout\_local}, \text{deadline} - \operatorname{time.monotonic}())$.
2. **Cancelación Forzada y Limpieza:**
   Si expira el plazo de 300 segundos:
   - El supervisor cancela cooperativamente la tarea de asyncio.
   - Envía señal de cierre al contexto de Playwright con un límite estricto de 5 segundos.
   - Si el proceso del navegador no responde, se termina de forma forzada (*kill process*).
   - El estado público se actualiza inmediatamente a `"fallida"` con el resumen: `"Tiempo global de 5 minutos agotado."`.
