# VERIFICACIÓN Y RECUPERACIÓN BASADA EN EVIDENCIA
**Herramienta:** `web-agent`  
**Módulos:** `web_agent.recovery`, `web_agent.core.runner`  
**Estado:** Especificación de Diseño Oficial (Reemplaza verificaciones ciegas y reintentos automáticos)

---

## 1. El Principio de Verificación Independiente
Un agente no puede declarar éxito basándose únicamente en:
1. Que el navegador haya aceptado un evento de ratón o teclado.
2. Que el modelo de inteligencia artificial emita `action_type: "done"`.
3. Que exista una diferencia gráfica entre dos capturas de pantalla.

La verificación exige **evidencia observable de las consecuencias de negocio** de la tarea.

---

## 2. La Diferencia Visual ($\Delta$ Imagen): Señal de Diagnóstico, no Veredicto

Una diferencia entre capturas consecutivas ($S_{prev}$ vs $S_{post}$) se trata como una **señal de telemetría auxiliar**:

```text
Diferencia Visual Presente (Δ > umbral):
  ├── Transición correcta a la siguiente pantalla
  ├── Aparición de un spinner o indicador de carga
  ├── Aparición de un mensaje de error o diálogo modal
  └── Animación cosmética o reloj del sistema operativo

Diferencia Visual Ausente (Δ ≈ 0):
  ├── La acción no tuvo efecto (clic sobre control inerte o deshabilitado)
  ├── La operación ya estaba completa y la interfaz no requería refresco
  ├── La respuesta del servidor remoto todavía no ha llegado a la pantalla
  └── El cambio gráfico es microscópico y queda bajo el umbral de detección
```

Por tanto, el sistema **nunca considerará que un cambio de imagen demuestra éxito ni que su ausencia demuestra fallo**.

---

## 3. Verificación de Éxito por Evidencia de Objetivo

Para validar el cumplimiento de una tarea en un sistema legacy sin DOM:

### 3.1. Caso: "Crear un registro o cliente"
- **Evidencia Inválida:** Haber hecho clic sobre el botón "Guardar" o "Aceptar".
- **Evidencia Válida Requerida:**
  1. La ventana modal de creación se cierra o pasa a estado de solo lectura.
  2. El agente realiza una consulta o búsqueda dentro de la grilla de la aplicación.
  3. El registro aparece listado con el identificador asignado o los datos ingresados visibles en pantalla.

### 3.2. Caso: "Exportar un informe o reporte"
- **Corrección Técnica Crucial:** Si la aplicación corre dentro de un escritorio remoto (noVNC/Guacamole), guardar un archivo dentro del escritorio **no dispara `page.expect_download()` en el navegador local del agente**, a menos que el cliente web remoto tenga configurado un túnel de descarga hacia el navegador host.
- **Evidencia Válida Requerida:**
  1. Verificación visual del diálogo "Reporte generado con éxito" o de la barra de progreso al 100%.
  2. Confirmación visual del archivo presente en la carpeta remota del sistema gestor (ej. diálogo "Guardar como" cerrado o mensaje en la barra de estado).
  3. Si existe canal de descarga local efectivo, comprobación de tamaño no nulo de archivo. No se declaran contenidos específicos que no hayan sido inspeccionados.

---

## 4. Protocolo de Recuperación Segura (Eliminación de Bucles Ciegos)

### 4.1. Patrón Rechazado
Se elimina expresamente la heurística especulativa:
$$\text{Clic fallido} \longrightarrow \text{Doble clic automático} \longrightarrow \text{Pulsar Escape}$$
*Riesgo Crítico:* Un doble clic automático sobre un botón de pago o aprobación puede duplicar la transacción; pulsar `Escape` en un formulario puede cancelar y descartar 15 minutos de captura de datos sin guardar.

### 4.2. Protocolo de Recuperación Autorizada
Toda recuperación debe seguir el ciclo:

```
  ┌────────────────────────────────────────────────────────┐
  │ 1. RE-OBSERVAR (Nueva captura fresca del viewport)     │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │ 2. CLASIFICAR EL ESTADO ACTUAL DE LA PANTALLA          │
  │    ¿Apareció modal de error? ¿La app está congelada?   │
  │    ¿La operación persistente ya se completó?           │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │ 3. COMPROBAR EFECTOS COLATERALES                       │
  │    Si la acción era R2/R3, verificar si el registro se │
  │    creó antes de intentar enviarlo de nuevo.           │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │ 4. SELECCIONAR ALTERNATIVA DENTRO DE POLÍTICA          │
  │    (Ej. enfocar campo previo, esperar 2 s, o atajo)    │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │ 5. RE-PLANIFICAR, VALIDAR Y AUDITAR                    │
  └────────────────────────────────────────────────────────┘
```

Si tras el diagnóstico no existe certeza de que sea seguro continuar, el agente aborta la tarea, finaliza con estado público `"fallida"` y emite un resumen transparente explicando la incertidumbre.
