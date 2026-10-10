# ADR-001 — Visión como modo principal de operación

- **Proyecto:** web-agent
- **Estado:** Aceptada para el diseño del reto; implementación pendiente
- **Ámbito:** Percepción, observaciones, adaptación a aplicaciones desconocidas y verificación
- **Decisión relacionada:** ADR-002 — Ejecución determinista
- **Decisión relacionada:** ADR-003 — Límites de dependencias y proveedores

## 1. Contexto

web-agent se construyó inicialmente como un agente de automatización web orientado a aplicaciones con estructura DOM utilizable.

El repositorio actual incluye:

- Extracción de controles HTML mediante `DOMObserver`.
- Identificadores temporales para elementos observados.
- Localización por roles, nombres y etiquetas.
- Acciones de navegación, clic, rellenado y extracción.
- Verificación de resultados mediante URL, texto visible y elementos DOM.

Esta aproximación es útil para aplicaciones web convencionales, pero no permite operar por sí sola los sistemas principales del reto.

El agente debe ejecutar tareas en aplicaciones legacy sin API, sin documentación y sin conocimiento previo de su interfaz. Estas aplicaciones se acceden mediante un escritorio transmitido al navegador.

En ese escenario, el navegador puede contener controles del cliente remoto, pero no necesariamente los controles de la aplicación que se muestra dentro del escritorio.

Por ejemplo, un botón visible de “Guardar” dentro de un ERP remoto puede existir únicamente como píxeles en una captura. No tiene por qué corresponder a un elemento HTML accesible mediante `get_by_role()`.

La arquitectura inicial describía la interpretación visual como Nivel 5 y estrategia complementaria de último recurso. Para este reto, esa prioridad deja de ser adecuada.

## 2. Problema que resuelve esta decisión

Debemos definir una estrategia de percepción que:

1. Permita operar aplicaciones sin controles DOM accesibles.
2. No dependa de selectores o perfiles específicos preparados para cada aplicación.
3. Mantenga el soporte DOM existente cuando esté permitido.
4. Evite que la percepción visual permita ejecutar acciones sin validación.
5. Distinga entre interpretar una pantalla y comprobar el resultado de una tarea.
6. Sea compatible con el límite de cinco minutos por ejecución.

## 3. Decisión

La percepción visual será el modo principal de operación para la evaluación del reto.

El nombre “Nivel 5” se conserva como referencia histórica del proyecto, pero no implica que deban agotarse los niveles DOM antes de utilizar visión.

El modo visual debe poder funcionar sin inspeccionar el DOM de la aplicación objetivo.

La arquitectura mantendrá tres modos explícitos:

### 3.1. `visual_only`

Modo principal para escritorios remotos, canvas y escenarios donde las reglas exijan operación por pantalla.

Características:

- Observación mediante capturas del viewport.
- Interpretación mediante un modelo visual compatible.
- Interacción mediante eventos de ratón y teclado.
- Ausencia de inspección del DOM de la aplicación para identificar controles.
- Verificación mediante evidencia observable en pantalla y otros canales expresamente permitidos.

La URL y la geometría de la sesión pueden utilizarse como metadatos operativos. No constituyen una inspección semántica de los controles de la aplicación.

Si las reglas prohíben también inspeccionar el HTML del cliente remoto, la autenticación debe realizarse visualmente.

### 3.2. `dom`

Modo para aplicaciones web convencionales cuando las reglas permitan utilizar estructura HTML.

Características:

- Observación estructurada de elementos.
- Localizadores semánticos y contextuales.
- Comprobación de unicidad y accionabilidad.
- Verificación mediante condiciones DOM cuando corresponda.

### 3.3. `auto`

Modo opcional para escenarios en los que se permita combinar ambas estrategias.

La selección puede considerar:

- Disponibilidad de controles semánticos útiles.
- Presencia de una superficie gráfica dominante.
- Evidencia de que el DOM representa realmente la aplicación objetivo.
- Fallos de localización o insuficiencia de la observación estructural.

Las heurísticas no deben tratarse como pruebas concluyentes.

Tener pocos elementos HTML, encontrar un canvas o identificar una palabra en la URL no demuestra por sí solo que una aplicación carezca de DOM utilizable.

En evaluación configurada como `visual_only`, no se ejecutará detección DOM para decidir el modo.

## 4. Contrato de observación visual

La observación visual será un objeto estructurado, no únicamente una cadena de texto.

Debe incluir como mínimo:

- Identificador de observación.
- Identificador de sesión o página activa.
- Momento de captura.
- Ancho y alto del viewport en píxeles CSS.
- Ancho y alto de la imagen capturada.
- Referencia interna a la imagen.
- Versión de geometría.
- Información de recorte y escala, si se utiliza.
- Indicación de si la imagen ha sido redactada o transformada.

La referencia interna a la imagen no debe permitir al modelo acceder libremente al sistema de archivos.

El adaptador de inferencia recibirá la imagen a través del canal multimodal permitido por su implementación.

### 4.1. Imagen inicial

Para el MVP se utilizarán capturas completas del viewport.

No se utilizarán capturas de página completa para proponer clics sobre contenido que no esté actualmente visible.

El formato inicial será PNG, salvo que las pruebas demuestren que otro formato conserva suficiente legibilidad y mejora la latencia o el costo.

### 4.2. Legibilidad

La estrategia debe preservar:

- Etiquetas de campos.
- Texto de botones.
- Mensajes de validación.
- Filas y columnas relevantes.
- Diálogos de confirmación.

La reducción de resolución no puede aplicarse únicamente por ahorro de tokens si elimina información necesaria para operar.

## 5. Coordenadas y geometría

El modelo propondrá puntos normalizados en el rango entero `[0, 1000]`.

Las acciones de Playwright utilizarán coordenadas del viewport en píxeles CSS, no coordenadas físicas asumidas a partir de la imagen.

Para una captura completa del viewport:

    X_css = round((x_norm / 1000) * (W_css - 1))
    Y_css = round((y_norm / 1000) * (H_css - 1))

Se comprobará:

    0 <= X_css < W_css
    0 <= Y_css < H_css

El valor normalizado 1000 corresponde al último punto válido de la dimensión, no a una coordenada exterior igual al ancho o alto.

Si se utilizan recortes o imágenes reescaladas, la observación debe conservar la transformación necesaria para recuperar el punto en el viewport original.

No se debe aplicar una transformación implícita basada en una resolución predeterminada cuando la geometría real sea desconocida.

### 5.1. Superficie autorizada

El viewport válido constituye una frontera geométrica mínima.

Cuando sea posible definir una superficie de interacción más estrecha, como el área del escritorio remoto, se validará también que el punto pertenezca a esa superficie.

Un punto dentro del viewport no está automáticamente autorizado para cualquier operación.

## 6. Vigencia de observaciones

Las acciones contextuales deben referenciar el identificador de observación utilizado para proponerlas.

El ejecutor rechazará una acción cuando:

- El identificador no coincida con la observación vigente.
- La página o sesión activa haya cambiado.
- La geometría haya cambiado.
- Se haya producido una navegación o transición relevante.
- La observación supere el límite de antigüedad configurado.

Después de una acción que pueda modificar la interfaz, se obtendrá una nueva observación antes de proponer la siguiente acción contextual.

Estas comprobaciones reducen el riesgo de actuar sobre coordenadas obsoletas, pero no eliminan todas las condiciones de carrera en una interfaz dinámica.

Ante evidencia de cambio entre propuesta y ejecución, la respuesta será reobservar, no corregir coordenadas de forma especulativa.

## 7. Generalización y aprendizaje durante la tarea

La adaptación a una aplicación desconocida se basará en:

- Interpretación visual de la pantalla.
- Exploración acotada y orientada al objetivo.
- Memoria de navegación de la sesión.
- Resultados de acciones anteriores.
- Skills procedimentales generales.

El agente podrá mantener un mapa temporal de:

- Módulos observados.
- Menús y rutas de navegación.
- Campos relevantes.
- Diálogos encontrados.
- Operaciones confirmadas.
- Intentos fallidos.

Este mapa debe considerarse una hipótesis revisable, no una descripción permanente de la aplicación.

Las coordenadas recordadas deben invalidarse cuando cambie el estado visual o la geometría.

No se requiere reentrenamiento del modelo para cada aplicación.

La adaptabilidad zero-shot es un objetivo de diseño que debe evaluarse; no constituye una garantía de éxito en cualquier sistema.

La persistencia de conocimiento entre tareas no forma parte del MVP. Si se añade, requerirá una política explícita de aislamiento, retención y validación para evitar contaminación entre escritorios de evaluación.

## 8. Verificación visual

La diferencia entre imágenes se utilizará como señal de diagnóstico.

No se utilizará como criterio suficiente de éxito.

Una diferencia puede reflejar:

- Una transición correcta.
- Un error.
- Un spinner.
- Un diálogo inesperado.
- Una animación sin relación con la tarea.

La ausencia de diferencia puede reflejar:

- Una acción sin efecto.
- Una operación realizada sin actualización visible.
- Una respuesta todavía pendiente.
- Un cambio demasiado pequeño para el método de comparación.

La verificación debe buscar evidencia vinculada a la postcondición del paso y al objetivo final.

Cuando la comprobación dependa de interpretación mediante VLM, el resultado se documentará como evidencia visual interpretada, no como comprobación determinista absoluta.

Una evaluación visual separada de la propuesta inicial puede reducir la dependencia de la misma decisión, pero no elimina errores correlacionados del modelo.

## 9. Alternativas consideradas

### 9.1. DOM primero y visión como último recurso

Ventajas:

- Aprovecha las capacidades actuales.
- Ofrece objetivos semánticos precisos en páginas convencionales.

Motivo de rechazo como estrategia principal:

- No resuelve el escenario central del reto.
- Puede dedicar parte del presupuesto de tiempo a inspecciones que no representan la aplicación remota.

Se conserva como opción en modo `auto`, fuera de evaluaciones exclusivamente visuales.

### 9.2. Visión obligatoria para todos los escenarios

Ventajas:

- Unifica la percepción.
- Reduce dependencias de estructura HTML.

Motivo de rechazo como única capacidad:

- Desaprovecha el soporte DOM existente.
- Puede ser menos preciso y más costoso en aplicaciones web semánticas.

### 9.3. OCR como mecanismo principal

Motivo de no adopción inicial:

- Reconocer texto no basta para interpretar foco, modales, iconos, disposición y consecuencias.
- Añade otra dependencia y otro proceso de calibración.

Podrá evaluarse como componente auxiliar si las pruebas muestran una necesidad concreta.

### 9.4. Automatización específica por aplicación

Motivo de rechazo:

- Contradice la evaluación sobre aplicaciones no vistas.
- Genera dependencia de coordenadas o rutas preparadas previamente.

## 10. Consecuencias

### Positivas

- Alineación con el escenario principal del reto.
- Capacidad de operar controles representados únicamente como píxeles.
- Conservación del soporte DOM.
- Contrato explícito de geometría y vigencia.
- Menor dependencia de perfiles específicos por aplicación.

### Negativas

- Mayor costo y latencia de inferencia.
- Incertidumbre en la localización de controles pequeños o ambiguos.
- Mayor exposición potencial de información sensible en capturas.
- Verificación menos determinista que en algunos escenarios DOM.
- Necesidad de probar transporte remoto, escalado y teclado.

## 11. Impacto sobre el repositorio

Se requiere:

- Añadir un observador visual.
- Extender el contrato de observaciones.
- Extender `ProposedAction` con acciones espaciales.
- Modificar la interfaz del adaptador de modelos para soportar imágenes.
- Incorporar ejecución por coordenadas.
- Actualizar la verificación final.
- Configurar el modo de percepción en el perfil.
- Corregir la vinculación de `observation_id`.
- Mantener las mejoras pendientes del observador DOM.
- Permitir el transporte remoto autorizado sin abrir WebSockets indiscriminadamente.

La documentación actual no debe afirmar que estas capacidades ya están implementadas.

## 12. Criterios de aceptación

La implementación de esta decisión se considerará validada cuando:

1. Una tarea visual pueda observarse y ejecutarse sin inspeccionar el DOM de la aplicación.
2. El modelo reciba realmente la captura mediante un canal multimodal.
3. Las coordenadas se transformen correctamente en distintas resoluciones y densidades de píxel.
4. Las acciones con observaciones obsoletas sean rechazadas.
5. El cambio de geometría obligue a reobservar.
6. El agente opere formularios y diálogos sobre una superficie gráfica.
7. La verificación no dependa únicamente de una diferencia de imágenes.
8. Las capturas sensibles se manejen según la política de evidencia.
9. El modo DOM siga disponible cuando esté permitido.
10. Las pruebas se ejecuten sobre interfaces distintas de las utilizadas para preparar la implementación.

## 13. Revisión de la decisión

Esta decisión se revisará si:

- Cambian las reglas sobre uso del DOM.
- El entorno de evaluación ofrece otro mecanismo de acceso.
- Las pruebas muestran que la estrategia visual no cumple el presupuesto temporal.
- Se introduce procesamiento de imágenes que cambie la geometría de referencia.
- Se requiere persistencia de conocimiento entre tareas.

Cualquier revisión debe mantener explícita la separación entre percepción, autorización, ejecución y verificación.
