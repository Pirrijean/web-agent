# CATÁLOGO DE SKILLS NATIVAS PARA OPERACIÓN DE SISTEMAS LEGACY
**Herramienta:** `web-agent`  
**Ubicación en Código:** `src/web_agent/skills/` (Procedimientos Nativos)  
**Estado:** Especificación de Diseño Aprobada (Excluye Servidores MCP Externos)

---

## 1. Principio Fundamental: Procedimientos Nativos, no Herramientas Externas
Para dar cumplimiento estricto a las restricciones de la hackathon:
- **Exclusión de componentes MCP externos:** Se excluye formalmente del camino de ejecución `skills/filesystem_audit_mcp.json` (que proponía invocar un servidor externo vía `npx`). Toda la lógica es código propio en Python.
- **Sin bypass de políticas:** Una skill no es una excepción de seguridad. Cada paso de una skill se ejecuta mediante primitivas controladas, respeta el deadline monotónico de 300 s y se audita de forma individual.
- **Atajos no universales:** `Alt+Space`, `X`, `Control+A` o `Tab` varían según el sistema operativo, idioma y control activo. La skill debe diagnosticar el estado del foco antes de emitir combinaciones de teclas.

---

## 2. Catálogo Detallado de Skills

### SKILL-01: `DesktopAuthAndBootstrap` (Acceso y Conexión al Escritorio Remoto)
- **Propósito:** Iniciar la conexión en el escritorio remoto autorizado (`desktop.url`), ingresar la contraseña de acceso y preparar la superficie de trabajo.
- **Regla Fundamental:** El modelo VLM localiza visualmente el campo de acceso o botón de conexión, pero el ejecutor resuelve la contraseña **sin incluirla jamás en el prompt del modelo ni en logs de auditoría**.
- **Procedimiento:**
  1. Navega a `desktop.url` autorizando el endpoint WebSocket de transporte necesario.
  2. Obtiene la primera observación visual (`observation_id`).
  3. El modelo identifica el punto de foco del campo de contraseña.
  4. El ejecutor hace clic en las coordenadas del campo e introduce la contraseña de forma protegida.
  5. Envía la pulsación de confirmación (`Enter` o clic en el botón de conexión).
  6. Espera la estabilización visual del escritorio remoto (2 segundos o fin de transiciones).

---

### SKILL-02: `CanvasFormFill` (Relleno Confiable de Campos en GUI Gráfica)
- **Propósito:** Introducir valores en campos de texto sobre canvas o aplicaciones sin elementos DOM.
- **Regla Fundamental:** Comprobar visualmente que el foco corresponda al campo deseado antes de escribir. El reemplazo (`Control+A` + `Backspace`) es una opción explícita y solo se ejecuta cuando el campo contenga texto previo que deba sustituirse.
- **Procedimiento:**
  1. Clic en las coordenadas del control $(X_{css}, Y_{css})$.
  2. Si se solicita reemplazo: emite `Control+A` seguido de `Backspace`.
  3. Mecanografía el valor con una cadencia controlada (delay de 15-25 ms) para respetar los buffers de red de escritorios remotos.
  4. No asume `Tab` o `Enter` universalmente: solo emite navegación de foco si está explícitamente requerido por la tarea.

---

### SKILL-03: `LegacyPopupManager` (Gestión Contextual de Diálogos Modales)
- **Propósito:** Identificar y resolver ventanas emergentes y cuadros de diálogo típicos de software antiguo.
- **Regla Fundamental:** Leer el contenido del diálogo y sus posibles consecuencias de negocio; **no pulsar automáticamente `Enter`**. Pulsar `Enter` a ciegas podría confirmar un borrado irreversible o aceptar condiciones no deseadas.
- **Procedimiento:**
  1. Detecta la presencia del diálogo modal superpuesto.
  2. Envía la captura al VLM para interpretar el mensaje: ¿Es confirmación de guardado, error de validación o advertencia destructiva?
  3. Selecciona la opción coherente con el objetivo autorizado (ej. clic en "Aceptar" para confirmar guardado de cliente, o clic en "Cancelar" si el diálogo alerta de una operación no autorizada).
  4. Verifica que el diálogo haya desaparecido de la pantalla tras la acción.

---

### SKILL-04: `ReportExportTracker` (Seguimiento de Exportación de Reportes)
- **Propósito:** Ejecutar la secuencia de generación y exportación de informes en sistemas legacy.
- **Regla Fundamental:** Diferenciar una exportación que ocurre dentro del sistema de archivos del escritorio remoto de una descarga en el navegador local del agente. Una descarga en el escritorio remoto **no produce un evento `page.expect_download()` en Playwright** a menos que el cliente web transmita el archivo hacia el host.
- **Procedimiento:**
  1. Localiza el menú de reportes o icono de exportación.
  2. Clic para desplegar opciones y selección del formato requerido (Excel, CSV, PDF).
  3. Si aparece un diálogo del sistema operativo ("Guardar como..."), introduce el nombre o confirma la ubicación remota.
  4. Verifica la conclusión mediante indicadores visuales: cierre del diálogo de progreso, aparición del mensaje "Reporte generado" o visualización del archivo en la grilla remota.
  5. No afirma tamaños o contenidos del archivo a menos que exista evidencia accesible en la sesión.

---

### SKILL-05: `ZeroShotDiscovery` (Descubrimiento y Mapeo de Navegación)
- **Propósito:** Permitir al agente orientarse en aplicaciones que nunca ha visto antes sin depender de selectores predefinidos.
- **Regla Fundamental:** Construir hipótesis de navegación revisables; no asumir que todas las aplicaciones comparten exactamente la misma distribución física.
- **Procedimiento:**
  1. Explora visualmente las zonas activas de la interfaz (menús superiores, paneles laterales, barras de herramientas, botones con iconos clásicos).
  2. Mantiene una memoria de trabajo acotada en la sesión: qué módulos fueron visitados y qué rutas llevaron a formularios de interés.
  3. Invalida referencias espaciales recordadas cuando la ventana cambie de tamaño o se navegue a otra pantalla.

---

### SKILL-06: `SelfHealingRetry` (Diagnóstico y Recuperación Segura)
- **Propósito:** Recuperar la ejecución cuando una acción no produce el efecto esperado.
- **Regla Fundamental:** **Se prohíbe la secuencia automática de clic $\to$ doble clic $\to$ Escape.** El protocolo de recuperación es: *Reobservar $\to$ Clasificar $\to$ Comprobar efectos posibles $\to$ Elegir alternativa autorizada $\to$ Ejecutar $\to$ Verificar*.
- **Procedimiento:**
  1. Ante una acción sin efecto observable, captura una nueva imagen y reevalúa el estado.
  2. Si la acción era persistente (R2), consulta primero si el registro llegó a crearse antes de intentar enviarlo de nuevo.
  3. Si el control requería doble clic explícito (ej. abrir un registro en una grilla legacy), el modelo debe justificar la necesidad del doble clic antes de ejecutarlo.
  4. Si persiste la incertidumbre, aborta la tarea e informa el estado para evitar efectos secundarios imprevistos.
