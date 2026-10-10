# PLAN DE VALIDACIÓN Y BATERÍA DE PRUEBAS
**Herramienta:** `web-agent`  
**Directorio de Pruebas:** `tests/`  
**Objetivo:** Garantizar fiabilidad, seguridad fail-closed y cumplimiento estricto del contrato de evaluación

---

## 1. Niveles de Validación

```
         ┌──────────────────────────────────────┐
         │  EVALUACIÓN REAL: 5 APPS DESCONOCIDAS│ ➔ Demo Day / Evaluación Oficial
         └──────────────────┬───────────────────┘
                            │
         ┌──────────────────▼───────────────────┐
         │  PRUEBAS DE INTEGRACIÓN ENTRYPOINT   │ ➔ Contrato REST, < 2s latencia, Bearer
         └──────────────────┬───────────────────┘
                            │
         ┌──────────────────▼───────────────────┐
         │  PRUEBAS DE ESCENARIO SINTÉTICO GUI  │ ➔ Canvas simulado, modales, formularios
         └──────────────────┬───────────────────┘
                            │
         ┌──────────────────▼───────────────────┐
         │  PRUEBAS UNITARIAS Y DE SEGURIDAD    │ ➔ Esquemas, límites CSS, SSRF, políticas
         └──────────────────────────────────────┘
```

---

## 2. Batería de Pruebas por Componente

### 2.1. Pruebas Unitarias de Geometría y Esquemas (`tests/unit/test_visual_geometry.py`)
- **Transformación de Coordenadas:** Validar que para cualquier $x_{norm} \in [0, 1000]$ y viewport $W$:
  $$0 \le \operatorname{round}\left(\frac{x_{norm}}{1000}(W - 1)\right) < W$$
  Comprobación de casos límite: $x_{norm}=0 \to 0$, $x_{norm}=1000 \to W-1$. Rechazo de $x_{norm}=-1$ o $x_{norm}=1001$.
- **Vigencia de Observación:** Probar que `ProposedAction` con un `observation_id` diferente al snapshot actual es rechazada de inmediato.
- **Validación Cruzada Pydantic:** Verificar rechazo de acciones espaciales sin coordenadas, acciones `wait` con duración negativa o excesiva, y acciones `navigate` con parámetros de teclado.

### 2.2. Pruebas de Seguridad y Red (`tests/security/test_network_and_secrets.py`)
- **Transporte WebSocket:** Validar que la sesión permita WebSockets exclusivamente hacia el host y puerto autorizado del escritorio (`desktop.url`) y cierre cualquier conexión WebSocket externa.
- **Mitigación SSRF:** Intentos de navegación hacia `169.254.169.254`, `file:///etc/passwd`, `http://localhost:22` deben ser bloqueados con `PolicyViolationError`.
- **Redacción de Credenciales:** Inyección de contraseñas de prueba en el runner; comprobar que el archivo de auditoría JSONL no contenga la contraseña en texto claro.

### 2.3. Pruebas de Escenarios GUI Sintéticos (`tests/scenarios/test_synthetic_canvas.py`)
- **Simulador de Escritorio Remoto:** Servidor local que expone una página con `<canvas>` interactivo simulando una aplicación Win32 (caja de texto, botón "Guardar", diálogo modal de confirmación).
- **Flujo de Relleno Visual:** Comprobar que `type_at_coordinates` ejecute foco, selección de texto, borrado y tipeo con cadencia controlada.
- **Flujo de Diálogo Modal:** Verificar que `LegacyPopupManager` resuelva la ventana emergente y no quede en bucle infinito.

### 2.4. Pruebas del Servicio REST Entrypoint (`tests/integration/test_entrypoint_api.py`)
- **Autenticación Bearer:**
  - Petición sin header `Authorization` $\to$ `401 Unauthorized`.
  - Petición con token incorrecto $\to$ `401 Unauthorized`.
  - Petición con token válido $\to$ `200 OK`.
- **Latencia de `POST /tasks`:**
  - El endpoint debe responder `{"task_id": "..."}` en menos de 2000 milisegundos (< 2 s).
- **Consulta de Estado `GET /tasks/{task_id}`:**
  - Inmediatamente después de crear: `status == "en_curso"`.
  - Al completar: `status == "terminada"` con resumen no vacío.
- **Temporizador de 5 Minutos (Timeout):**
  - Tarea simulada que tarda más de 300 s; comprobar que el supervisor cancele el worker y marque `status == "fallida"`.

---

## 3. Criterios de Aceptación para Despliegue (Cierre 20:00)
1. `uv run pytest` pasa al 100% de las pruebas unitarias y de seguridad.
2. El servidor responde a `GET /health` con `200 OK` en el entorno desplegado accesible desde Internet.
3. El token del equipo autentica correctamente las solicitudes del evaluador.
