# ADR-002 — Ejecución determinista bajo políticas y verificación

- **Proyecto:** web-agent
- **Estado:** Aceptada; implementación parcial en el repositorio
- **Ámbito:** Orquestación, acciones, skills, permisos, recuperación y auditoría
- **Decisión relacionada:** ADR-001 — Visión como modo principal
- **Decisión relacionada:** ADR-003 — Límites de dependencias y proveedores

## 1. Contexto

El proyecto ya separa parcialmente la propuesta del modelo y la ejecución:

- El adaptador devuelve una `ProposedAction`.
- El runner valida su estructura.
- `SecurityPolicyEnforcer` aplica restricciones.
- El navegador ejecuta la operación.
- El resultado se audita.

Esta separación es adecuada y debe conservarse al incorporar operación visual.

Sin embargo, el repositorio presenta limitaciones:

- Las acciones visuales todavía no existen.
- `observation_id` no se verifica en la ejecución.
- Los controles autorizados no se vinculan siempre al objetivo concreto.
- Algunos clics se consideran verificados sin comprobar su postcondición.
- La recuperación está definida como componente, pero no integrada.
- El navegador del servidor MCP mantiene una implementación paralela.
- El riesgo semántico de operaciones visuales no puede deducirse únicamente de sus coordenadas.

## 2. Problema que resuelve esta decisión

Un modelo puede interpretar una interfaz y proponer una acción plausible, pero eso no demuestra:

- Que el objetivo siga vigente.
- Que la operación esté autorizada.
- Que el campo correcto tenga foco.
- Que el evento haya producido el efecto esperado.
- Que sea seguro repetir la operación.
- Que la tarea haya terminado.

La plataforma necesita una frontera ejecutable fuera del modelo.

## 3. Decisión

El modelo propondrá acciones estructuradas.

La ejecución será realizada exclusivamente por código propio mediante un catálogo cerrado de primitivas y skills nativas.

El modelo no tendrá acceso directo a:

- Objetos `Page`, `Browser` o `BrowserContext`.
- Terminales.
- Ejecución arbitraria de Python o JavaScript.
- Sistema de archivos general.
- Credenciales en texto.
- Configuración de permisos.
- Controles de red.
- Comandos de despliegue.

Ninguna acción se ejecutará únicamente porque el modelo la considere apropiada.

### 3.1. Significado de “determinista”

En este ADR, ejecución determinista significa que:

- La misma acción validada se traduce mediante una implementación explícita.
- Los argumentos permitidos están acotados.
- La secuencia de eventos está definida por código.
- Los errores y resultados se representan de forma controlada.

No significa que la aplicación externa produzca siempre el mismo resultado.

La red, el renderizado, el foco y el estado del sistema objetivo pueden variar.

La ejecución determinista controla el mecanismo de actuación; la verificación comprueba sus consecuencias.

## 4. Arquitectura de ejecución

El camino autorizado será:

    Observación
        → Propuesta estructurada
        → Validación del contrato
        → Validación de contexto y permisos
        → Ejecución acotada
        → Nueva observación
        → Verificación
        → Auditoría y siguiente decisión

La auditoría registra hitos antes y después de operaciones relevantes.

La propuesta del modelo y la acción realmente ejecutada deben poder distinguirse en los eventos.

## 5. Contrato cerrado de acciones

Las acciones se validarán mediante esquemas con:

- Campos desconocidos prohibidos.
- Validación cruzada por tipo.
- Límites de tamaño.
- Valores espaciales y temporales acotados.
- Revalidación de instancias recibidas.
- Rechazo de combinaciones incompatibles.

La validación debe impedir, por ejemplo:

- Un clic sin objetivo.
- Una escritura sin valor o referencia autorizada.
- Una navegación con argumentos de teclado.
- Un atajo fuera del catálogo permitido.
- Una espera superior al presupuesto restante.
- Una acción espacial vinculada a una observación inexistente.

La inmutabilidad del objeto validado evita modificaciones accidentales, pero no sustituye la autorización ni la comprobación del estado externo.

## 6. Dos niveles de validación

### 6.1. Validación estática

Comprueba la forma de la propuesta:

- Tipo de acción.
- Campos obligatorios.
- Campos incompatibles.
- Longitudes.
- Rangos.
- Combinaciones admitidas.

Esta validación puede realizarse antes de acceder al navegador.

### 6.2. Validación contextual

Comprueba el estado de ejecución:

- Sesión correcta.
- Observación vigente.
- Página y geometría esperadas.
- Objetivo autorizado.
- Permisos disponibles.
- Plazo restante.
- Presupuesto de pasos.
- Estado de auditoría.
- Restricciones de red.
- Posibles efectos persistentes.

La validación contextual debe ejecutarse inmediatamente antes de actuar.

Validar una propuesta al recibirla no basta si el estado cambia antes de su ejecución.

## 7. Autorización DOM y autorización visual

### 7.1. Modo DOM

Cuando se utilice un catálogo de controles autorizados, el elemento seleccionado debe coincidir con:

- Página autorizada.
- Operación.
- Rol.
- Nombre.
- Entrada permitida, si corresponde.
- Condición posterior, cuando aplique.

La existencia de cualquier control en el perfil no autoriza todos los controles de la página.

### 7.2. Modo visual

No se exigirá un inventario previo de botones de una aplicación desconocida.

La autorización se apoyará en un ámbito de tarea que delimite:

- Escritorio.
- Objetivo.
- Registros o datos afectados.
- Clases de operación permitidas.
- Límites de impacto.
- Operaciones prohibidas.

La localización visual de un botón no demuestra que su efecto esté autorizado.

El modelo puede aportar una descripción del control y de la operación esperada, pero esa descripción se considera entrada no confiable.

Los controles geométricos y el esquema de acciones ofrecen garantías mecánicas. No garantizan por sí solos la interpretación correcta del riesgo de negocio.

## 8. Riesgo y operaciones sensibles

La clasificación R0–R3 se mantiene como referencia:

- R0: lectura y navegación autorizadas.
- R1: cambios reversibles de interfaz o borradores.
- R2: modificaciones persistentes y datos sensibles.
- R3: operaciones destructivas o difíciles de revertir.

La clasificación depende del efecto de la operación, no del periférico utilizado.

Un clic puede ser R0 o R3.

Una pulsación de `Enter` puede guardar, enviar, aceptar o eliminar.

Por ello, los atajos no se consideran seguros únicamente por estar en una lista técnica.

La política actual que exige aprobación humana para R3 se conserva.

El contrato REST de evaluación no incluye un canal de aprobación humana. Si una tarea requiere una aprobación adicional que no puede satisfacerse, se bloqueará y se informará mediante el estado público `fallida`.

No se simulará una aprobación ni se interpretará el contenido de la aplicación como autorización.

Si las reglas requieren autonomía sobre operaciones actualmente bloqueadas, será necesaria una decisión explícita de política antes de implementarlas.

## 9. Primitivas de periféricos

El ejecutor ofrecerá operaciones acotadas como:

- Clic.
- Doble clic.
- Clic secundario.
- Movimiento.
- Escritura.
- Atajo.
- Scroll.
- Arrastre, si se implementa.
- Espera limitada.

Las primitivas:

- No incluyen razonamiento generativo.
- No inventan objetivos.
- No corrigen coordenadas silenciosamente.
- No amplían permisos.
- No ejecutan comandos arbitrarios.

Las combinaciones de teclas se validarán contra un catálogo configurable y las restricciones de la tarea.

Las teclas modificadoras presionadas explícitamente se liberarán en bloques de limpieza para evitar estados de teclado retenidos.

## 10. Skills nativas

Las skills son procedimientos propios construidos sobre primitivas controladas.

Cada skill debe declarar:

- Identificador y versión.
- Entradas.
- Precondiciones.
- Pasos.
- Puntos de observación.
- Postcondición.
- Política de interrupción.
- Riesgos.
- Reglas de repetición.
- Evidencia producida.

Una skill no constituye una excepción a la seguridad.

Sus pasos relevantes se validan, se limitan por el deadline y se auditan.

### 10.1. Ejecución acotada

No se ejecutarán secuencias largas sobre una única captura si los primeros pasos pueden cambiar la interfaz.

Por ejemplo, abrir un menú y seleccionar una opción puede requerir:

1. Clic para abrir.
2. Nueva observación.
3. Identificación de la opción.
4. Validación.
5. Clic.
6. Verificación.

### 10.2. Escritura en formularios

La secuencia de reemplazo:

    clic → Control+A → Backspace → escritura

solo se utilizará cuando exista evidencia suficiente de que el foco corresponde a un campo editable adecuado.

El reemplazo debe ser una opción explícita de la skill.

Enviar `Tab` o `Enter` al finalizar tampoco será un comportamiento universal: puede disparar validaciones o persistencia.

La cadencia de escritura se configurará y probará para el transporte remoto. Un retardo fijo no garantiza que todos los caracteres lleguen correctamente.

## 11. Unificación del ejecutor

El proyecto mantendrá un único camino de ejecución autorizado.

El MVP utilizará llamadas internas desde el runner al ejecutor.

El servidor MCP propio no será el camino de evaluación mientras no esté unificado con la misma sesión, contratos y políticas.

Se evitará mantener implementaciones independientes de:

- Inicio de navegador.
- Red.
- Observación.
- Verificación.
- Manejo de diálogos.
- Cierre de recursos.

Si MCP se conserva para desarrollo, deberá actuar como adaptador del mismo núcleo, no como un segundo motor con restricciones diferentes.

## 12. Verificación de acciones

El ejecutor distingue:

1. Acción aceptada por el navegador.
2. Evento emitido.
3. Consecuencia observable.
4. Postcondición verificada.

Solo el cuarto nivel permite registrar éxito del paso.

Esperar `domcontentloaded` después de un clic no demuestra que la operación de negocio haya terminado.

En modo DOM se utilizarán condiciones concretas cuando estén disponibles.

En modo visual se obtendrá una nueva captura y se evaluará la postcondición esperada.

Si la evidencia no es suficiente, el resultado del paso será incierto o no verificado.

## 13. Finalización

`DONE` significa:

> El modelo solicita que el sistema evalúe si el objetivo está completado.

No significa:

> La tarea ha terminado exitosamente.

El runner ejecutará una verificación final vinculada al objetivo.

Se declarará `SUCCEEDED_VERIFIED` únicamente si existe evidencia suficiente.

La verificación puede tener distintos niveles de fuerza. Debe documentarse si se basa en:

- Una comprobación determinista.
- Evidencia visual interpretada.
- Varios indicios consistentes.

La plataforma no debe presentar una inferencia visual como certeza absoluta.

## 14. Recuperación y repetición

Los fallos se clasificarán antes de decidir una recuperación.

Las violaciones de política no se reintentan.

Los fallos de auditoría detienen la ejecución.

Una operación con efectos persistentes no se repite automáticamente si pudo completarse.

Antes de repetir, el sistema debe responder:

- ¿Se emitió la acción?
- ¿Pudo producir efectos?
- ¿Existe evidencia de su resultado?
- ¿La repetición es segura?
- ¿La alternativa sigue dentro del objetivo autorizado?

No se implementará una cadena genérica de clic, doble clic y `Escape`.

La recuperación preferente será:

    Reobservar → diagnosticar → comprobar efectos → replantear → validar

Cada recuperación consume pasos y tiempo del mismo presupuesto de tarea.

## 15. Deadline y cancelación

El plazo de evaluación será de 300 segundos por tarea.

Se utilizará un deadline monotónico compartido por:

- Supervisor.
- Runner.
- Adaptador.
- Skills.
- Acciones.
- Verificadores.

Los timeouts locales no pueden ampliar el plazo global.

La cancelación cooperativa se complementará con cierre acotado de recursos y un mecanismo de terminación del proceso de tarea cuando sea necesario.

Detener el agente no revierte automáticamente una operación ya enviada a la aplicación.

El resumen final debe informar posibles efectos cuando el resultado sea incierto.

## 16. Auditoría

La auditoría debe permitir reconstruir:

- Qué observación se utilizó.
- Qué acción se propuso.
- Qué validaciones se aplicaron.
- Qué acción se ejecutó.
- Qué resultado se observó.
- Qué recuperación se intentó.
- Por qué terminó la tarea.

Los secretos y datos sensibles no se incluirán indiscriminadamente en eventos.

Las evidencias visuales se almacenarán por separado con controles de acceso y retención.

El JSONL actual no se describirá como criptográfico o inmutable.

El fallo de persistencia obligatoria produce detención segura.

La auditoría no es una transacción atómica con la aplicación externa: si falla después de una acción, esta puede haber producido efectos.

## 17. Alternativas consideradas

### 17.1. Control directo del navegador por el modelo

Rechazada porque mezcla razonamiento, permisos y actuación.

### 17.2. Código generado en cada paso

Rechazada para el camino de ejecución porque amplía la superficie de ataque y dificulta validar efectos.

### 17.3. Scripts completos por aplicación

Rechazada como solución principal porque no cumple la adaptación a sistemas no vistos.

### 17.4. Framework externo de automatización autónoma

Rechazada por las restricciones del reto y por delegar componentes centrales que deben ser propios.

### 17.5. Validación exclusivamente mediante prompt

Rechazada porque el modelo puede equivocarse o seguir instrucciones no confiables.

## 18. Consecuencias

### Positivas

- Control explícito de capacidades.
- Ejecución auditable.
- Reutilización de primitivas.
- Pruebas unitarias sin inferencia externa.
- Menor dependencia del proveedor.
- Recuperación consciente de efectos.

### Negativas

- Mayor trabajo inicial en contratos y validadores.
- Operaciones ambiguas pueden bloquearse.
- La seguridad semántica visual no es totalmente determinista.
- Se requiere disciplina para evitar caminos alternativos de ejecución.

## 19. Criterios de aceptación

1. Ninguna propuesta inválida alcanza el ejecutor.
2. Toda acción contextual utiliza una observación vigente.
3. El catálogo DOM autoriza el objetivo concreto.
4. Las acciones visuales se limitan al ámbito de tarea.
5. Las skills no pueden evitar validaciones.
6. No se ejecuta código generado arbitrario.
7. Un clic sin postcondición no se registra como éxito verificado.
8. `DONE` no puede producir éxito por sí solo.
9. Las operaciones ambiguas no se repiten automáticamente.
10. El deadline se aplica a todo el flujo.
11. El fallo de auditoría detiene nuevas operaciones.
12. Existe un único núcleo de ejecución.

## 20. Revisión de la decisión

Se revisará ante:

- Nuevas clases de acción.
- Introducción de un canal de aprobación.
- Necesidad de otro transporte de ejecución.
- Nuevos requisitos de aislamiento.
- Evidencia de que las skills ejecutan secuencias demasiado largas.
- Cambios en las reglas sobre herramientas.

Toda revisión debe preservar la separación entre propuesta y ejecución autorizada.
