# Resumen Ejecutivo de Arquitectura — WEB-AGENT
**Automatización Autónoma de Sistemas Heredados (Nivel 5 GUI Canvas) sin API ni Documentación**

---

### 1. Misión y Alcance Operativo
**WEB-AGENT** es un agente autónomo de software diseñado para operar sistemas legacy empresariales que carecen de API, documentación y soporte del proveedor. El agente percibe la interfaz visual a través de un canvas interactivo (noVNC / GUI Canvas) de la misma forma que un operador humano, e interpreta y ejecuta tareas complejas expresadas en lenguaje natural en aplicaciones nunca antes vistas.

* **Modo Principal:** Percepción visual directa (screenshots PNG normalizados en espacio de coordenadas CSS $0 \le X, Y \le 1000$) y despacho determinista de eventos de puntero y teclado en Playwright.
* **Cero Frameworks Externos:** Implementación nativa sin dependencias de frameworks de agentes de terceros (LangChain, AutoGen, CrewAI, Browser-Use, etc.), garantizando control absoluto de seguridad, auditoría y latencia.

---

### 2. Arquitectura en Tres Capas de Aislamiento

```
┌────────────────────────────────────────────────────────────────────────┐
│  Capa 1: Entrypoint REST ASGI (FastAPI + Bearer Token)                │
│  • GET /health (Listo en 200 sin consumo de LLM)                       │
│  • POST /tasks (Valida y despacha en < 2.0 s; retorna task_id)          │
│  • GET /tasks/{task_id} (Estados: en_curso | terminada | fallida)      │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Despacho asíncrono
┌──────────────────────────────────▼─────────────────────────────────────┐
│  Capa 2: Supervisor de Procesos & Aislamiento de Ejecución            │
│  • Worker en proceso hijo dedicado (multiprocessing spawn)             │
│  • Deadline monotónico estricto de 300 s (5 minutos máximo por tarea)  │
│  • Control presupuestario en tiempo real (límite USD, tokens y steps)  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ Bucle de control
┌──────────────────────────────────▼─────────────────────────────────────┐
│  Capa 3: Runtime del Núcleo & Muro de Políticas (Fail-Closed)          │
│  • Observación: Captura visual con hash SHA-256 y época de navegación   │
│  • Inferencia: Adaptador intercambiable (OpenRouter / Modelos Locales) │
│  • Políticas: Clasificación estricta de riesgo (R0 a R3)               │
│  • Ejecución: Playwright con allowlist WebSocket para VNC              │
│  • Verificación: Doble confirmación post-ejecución antes de DONE       │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 3. Mecanismos de Seguridad y Defensa en Profundidad

| Dominio | Mecanismo de Control | Garantía de Seguridad |
|---|---|---|
| **Seguridad de Red** | Filtro SSRF + Comprobación DNS + Egress Proxy | Bloqueo estricto de metadata (`169.254.169.254`), localhost y redes privadas. |
| **Tráfico noVNC** | Allowlist granular de WebSockets | Conexión `ws/wss` permitida exclusivamente hacia el `desktop_origin` autorizado. |
| **Gestión de Secretos** | SecretStore & DataRedactor en memoria | Las contraseñas del escritorio se inyectan a bajo nivel; **nunca** entran al prompt del LLM ni a los logs de auditoría. |
| **Muro de Políticas** | Clasificación de riesgo R0–R3 (Fail-Closed) | Operaciones destructivas (R3) son rechazadas de forma segura ante la ausencia de canal humano. |
| **Auditoría Inmutable** | JSONL con encadenamiento criptográfico | Cada evento, decisión y acción queda registrado con hash SHA-256 verificable. |
| **Supervisión de Vida** | Terminación forzada y limpieza de recursos | Cierre garantizado de navegador, subprocesos y memoria al vencer el plazo de 300 s. |

---

### 4. Ciclo de Ejecución de una Tarea

1. **Recepción & Autorización:** La API valida el Bearer Token y los parámetros de la tarea en `< 2` segundos, asignando un `task_id` criptográfico.
2. **Bootstrap del Escritorio:** El agente navega a la URL del escritorio remoto, desbloquea la pantalla mediante `DesktopAuthAndBootstrap` usando la credencial resguardada, y espera estabilidad del canvas.
3. **Bucle de Percepción-Acción:**
   * Captura visual etiquetada con `observation_id` y cálculo de resolución CSS.
   * Proposición estructurada en JSON estricto bajo esquema Pydantic.
   * Validación contextual y filtro de políticas de seguridad.
   * Emisión física de eventos de ratón (`click_xy`, coordenadas escaladas) o pulsaciones de teclado.
4. **Verificación Independiente:** Antes de emitir `done`, el agente evalúa evidencias visuales independientes. Si la meta no está verificada, el estado final es `fallida`.
