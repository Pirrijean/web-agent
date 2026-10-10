# ADR-003 — Límites de dependencias, herramientas y proveedores

- **Proyecto:** web-agent
- **Estado:** Aceptada; uso de inferencia externa condicionado a las reglas oficiales
- **Ámbito:** Dependencias, proveedores de IA, MCP, infraestructura y reproducibilidad
- **Decisión relacionada:** ADR-001 — Visión como modo principal
- **Decisión relacionada:** ADR-002 — Ejecución determinista

## 1. Contexto

El reto exige construir el agente con capacidades propias y no utilizar herramientas o proveedores externos que resuelvan la automatización por el equipo.

La expresión “sin herramientas externas” necesita traducirse a límites técnicos claros.

No es equivalente utilizar:

- Una biblioteca de navegador.
- Un servidor HTTP.
- Un modelo para interpretar una imagen.
- Un agente externo que recibe la tarea y opera el escritorio.

El repositorio actual incluye:

- Playwright.
- Pydantic.
- HTTPX.
- PyYAML.
- SDK MCP.
- Un adaptador OpenRouter.
- Un servidor MCP de navegador propio.
- Una configuración que propone ejecutar un servidor de archivos externo mediante `npx`.

La documentación debe distinguir estos componentes y evitar afirmar un cumplimiento que todavía dependa de aclaraciones de las reglas.

## 2. Problema que resuelve esta decisión

Debemos establecer:

1. Qué partes del agente deben ser propias.
2. Qué dependencias de bajo nivel se proponen.
3. Qué componentes quedan excluidos.
4. Bajo qué condiciones puede utilizarse inferencia externa.
5. Cómo evitar dependencia de un proveedor.
6. Cómo proteger los datos enviados a modelos.
7. Cómo controlar costo, latencia y reproducibilidad.
8. Cómo revisar cualquier incorporación futura.

## 3. Decisión general

web-agent será propietario de:

- Orquestación.
- Máquina de estados.
- Contratos.
- Políticas.
- Observadores.
- Transformación geométrica.
- Ejecutor.
- Skills.
- Recuperación.
- Verificación.
- Auditoría.
- Gestión de tareas.
- Integración del contrato REST.

No se utilizará un agente externo que reciba el objetivo y opere la aplicación por cuenta del proyecto.

Las dependencias se limitarán a componentes de infraestructura o primitivas técnicas que no resuelvan de forma autónoma el problema central.

El uso de estas dependencias se documentará y se confirmará contra las reglas oficiales cuando exista ambigüedad.

## 4. Clasificación de componentes

### 4.1. Componentes propios obligatorios

Deben implementarse y mantenerse en el repositorio:

- Selección de acciones.
- Validación contextual.
- Política de permisos.
- Ejecución de ratón y teclado.
- Skills procedimentales.
- Gestión de observaciones.
- Verificación del objetivo.
- Decisión de repetición.
- Registro de resultados.

La responsabilidad de estos componentes no se delegará a un servicio de automatización.

### 4.2. Dependencias de bajo nivel propuestas

Se consideran compatibles con el diseño, sujetas a las reglas oficiales:

- Python como runtime.
- Playwright como interfaz de navegador y periféricos.
- Pydantic como validación.
- HTTPX como transporte HTTP.
- PyYAML para perfiles.
- pytest para pruebas.
- Un servidor HTTP/ASGI para el entrypoint.
- Una biblioteca básica de imágenes, si resulta necesaria para recortes o redacción.

Estas dependencias no toman el objetivo de negocio ni deciden por sí solas cómo completar la tarea.

### 4.3. Componentes excluidos del camino de evaluación

No se utilizarán:

- Agentes de navegación ya preparados.
- Servicios de computer use que ejecuten la tarea completa.
- Frameworks externos que sustituyan la orquestación y ejecución propias.
- Servidores externos de navegador autónomo.
- Servicios que exploren y operen el sistema objetivo por el equipo.
- Herramientas de ejecución arbitraria expuestas al modelo.

LangChain, LangGraph y Browser Use quedan fuera del MVP de evaluación.

Esta exclusión es una decisión de simplicidad y cumplimiento del reto; no implica que todas esas tecnologías tengan las mismas capacidades o riesgos.

### 4.4. Inferencia de modelos

Un modelo puede actuar como componente de inferencia:

    Observación + objetivo → propuesta estructurada

No debe actuar como servicio de automatización:

    Tarea + credenciales → ejecución remota completa

La inferencia externa solo se habilitará si las reglas la permiten.

Si las reglas no están confirmadas, la arquitectura conservará el adaptador, pero la documentación marcará esa dependencia como pendiente de autorización.

## 5. Límites del proveedor de inferencia

Cuando se permita un proveedor externo, su acceso estará limitado a:

- Contexto mínimo de la tarea.
- Observación necesaria.
- Historial acotado.
- Esquema de salida.
- Restricciones expresadas para orientar la propuesta.

No recibirá:

- Contraseña del escritorio.
- Token del equipo.
- Cookies de sesión.
- Estado autenticado completo.
- Acceso al navegador.
- Acceso general al sistema de archivos.
- Permisos para ejecutar herramientas.
- Credenciales de infraestructura.

El proveedor devuelve una propuesta. La plataforma la trata como entrada no confiable.

No se habilitarán llamadas a herramientas del proveedor como camino alternativo para operar el escritorio.

## 6. Independencia del proveedor

La interfaz del núcleo no utilizará tipos exclusivos de OpenRouter ni de otro proveedor.

El contrato del adaptador deberá representar:

- Entrada textual y visual.
- Propuesta estructurada.
- Identificador de modelo.
- Uso reportado.
- Latencia.
- Errores clasificados.
- Información de costo cuando esté disponible.

La validación de acciones se realizará en el núcleo incluso si el proveedor ofrece salida estructurada.

La compatibilidad con JSON o un esquema remoto no constituye una frontera de seguridad suficiente.

OpenRouter será una implementación opcional del adaptador.

No es una pieza obligatoria de la arquitectura.

## 7. Selección de modelo

No se fijará el diseño a un identificador histórico sin comprobar:

- Disponibilidad.
- Capacidad de visión.
- Calidad de localización.
- Soporte de salida estructurada.
- Límites de imagen.
- Latencia.
- Precio.
- Política de tratamiento de datos.

Los nombres actuales del repositorio se consideran valores configurables, no decisiones permanentes.

La selección se hará mediante pruebas representativas del entorno:

- Formularios densos.
- Texto pequeño.
- Menús.
- Modales.
- Tablas.
- Interfaces no vistas.
- Resoluciones distintas.

El modelo más rápido no se elegirá únicamente por velocidad si produce clics incorrectos o falsos éxitos.

## 8. Minimización de datos

Antes de enviar información al proveedor se evaluará si es necesaria para el siguiente paso.

Medidas:

- Excluir secretos.
- Limitar historial.
- Evitar capturas irrelevantes.
- Redactar regiones sensibles cuando sea posible sin impedir la tarea.
- No enviar trazas completas.
- No enviar respuestas de red indiscriminadas.
- No reutilizar datos entre tareas.

La redacción textual no modifica automáticamente una imagen.

Las capturas requieren un mecanismo propio de protección.

Si no puede protegerse una imagen sin perder información imprescindible, el envío debe ajustarse a la autorización y política de datos aplicable. No debe enviarse silenciosamente.

Los datos operativos autorizados y su versión redactada para auditoría deben mantenerse separados para evitar alterar la tarea.

## 9. Credenciales

Los secretos deben resolverse en la capa de ejecución.

El modelo puede proponer una referencia autorizada, pero no inventar nombres de secretos ni solicitar acceso general al entorno.

La contraseña del escritorio:

- Se recibe por el contrato autenticado.
- Se utiliza únicamente en el flujo de acceso.
- No se incluye en prompts.
- No se incorpora al resumen.
- No se persiste en auditoría.
- Se registra para redacción literal durante su uso, según la implementación.

No se afirmará que el secreto está cifrado en memoria si no existe un mecanismo concreto que lo garantice.

## 10. Presupuesto y costo

Toda llamada de inferencia debe estar asociada a un presupuesto de tarea.

La política debe limitar:

- Número de llamadas.
- Tokens de salida.
- Tamaño y cantidad de imágenes.
- Tiempo por llamada.
- Tiempo global.
- Reserva monetaria estimada.

El cálculo debe distinguir, cuando corresponda:

- Modelo.
- Tokens de entrada.
- Tokens de salida.
- Procesamiento de imágenes.
- Otros cargos del proveedor.

La fórmula fija actual basada en `total_tokens` no se considerará contabilidad real del proveedor.

Una reserva local no garantiza por sí sola el techo de facturación externa.

Si el proveedor ofrece límites de gasto adicionales, podrán utilizarse como defensa complementaria.

### 10.1. Llamadas inciertas

Si una llamada pudo haber sido enviada y no se recibe respuesta, no debe liberarse automáticamente toda la reserva como si no hubiera sido facturada.

Se conservará una contabilización conservadora hasta reconciliar el uso.

### 10.2. Concurrencia

El gestor presupuestario debe evitar carreras entre reservas.

Si varias tareas comparten un límite de equipo, necesitarán una frontera contable común o límites independientes cuya suma sea controlada.

El archivo de estado no podrá aumentar los máximos fijados por la política del operador.

## 11. Latencia y reintentos del proveedor

Las llamadas respetarán el tiempo restante de la tarea.

No se iniciará una consulta cuyo timeout configurado exceda sin ajuste el deadline disponible.

Los reintentos de inferencia:

- Serán limitados.
- Consumirán presupuesto.
- No provocarán repetición automática de una acción de aplicación.
- Se registrarán.
- No se realizarán si el plazo restante es insuficiente.

Cambiar de modelo no debe interpretarse como prueba de que una acción anterior falló.

## 12. Fallback

El adaptador scripted se conservará para pruebas.

No se utilizará como sustituto silencioso de un VLM en evaluación.

Si el proveedor de visión no está disponible:

- La tarea puede esperar brevemente dentro de sus límites.
- Puede utilizarse un modelo alternativo previamente configurado y autorizado.
- Si no existe alternativa válida, se finalizará con un error explícito.

No se declarará éxito mediante respuestas simuladas.

Si la inferencia externa está prohibida, un modelo local podrá evaluarse solo si las reglas, recursos y capacidades lo permiten.

No se asumirá que existe un fallback local funcional sin haberlo desplegado y probado.

## 13. MCP

MCP es un protocolo de integración; no demuestra por sí mismo que un componente sea seguro, nativo o compatible con las reglas.

Para el MVP de evaluación:

- No se requiere MCP.
- El runner invocará directamente el núcleo propio.
- El servidor MCP propio puede conservarse para desarrollo, fuera del camino principal.
- No se permitirá que el modelo invoque herramientas MCP para evitar el ejecutor controlado.

El servidor MCP de navegador actual deberá unificarse con el núcleo antes de utilizarse en otro escenario.

## 14. Servidor externo de archivos

La configuración:

    npx -y @modelcontextprotocol/server-filesystem

queda excluida del despliegue de evaluación.

Motivos:

- Introduce un componente externo innecesario.
- Puede descargar código durante la ejecución.
- Amplía la superficie de acceso a archivos.
- No es necesario para auditar: web-agent ya tiene escritura controlada propia.
- No representa una skill procedimental nativa.

La auditoría y el almacenamiento de evidencias se realizarán mediante código del proyecto con rutas y permisos acotados.

## 15. Infraestructura de despliegue

Puede utilizarse infraestructura de alojamiento para ejecutar el servicio del equipo, siempre que las reglas lo permitan.

El alojamiento no debe ejecutar la automatización por el proyecto.

La infraestructura puede proporcionar:

- Contenedores.
- TLS.
- Red.
- Secretos.
- Almacenamiento.
- Supervisión de procesos.

El equipo mantiene responsabilidad sobre:

- Código desplegado.
- Entrypoint.
- Token.
- Disponibilidad.
- Políticas.
- Resultados.
- Evidencia.

La red del navegador y la red del adaptador de inferencia tendrán permisos separados cuando sea posible.

El navegador no necesita acceso al proveedor de IA y el proveedor no necesita acceso al escritorio.

## 16. Reproducibilidad

Las versiones aprobadas se fijarán mediante un archivo de bloqueo reproducible.

El TXT recibido no incluye un archivo de bloqueo; por tanto, la reproducibilidad exacta no se considera demostrada.

Se documentarán:

- Versión de Python.
- Dependencias.
- Revisión del código.
- Versión del navegador.
- Modelo configurado.
- Parámetros de inferencia.
- Configuración de límites.

Las dependencias de desarrollo se separarán de las necesarias para producción.

La instalación de navegadores y dependencias ocurrirá durante la preparación del entorno o construcción de la imagen, no por instrucciones generadas durante una tarea.

## 17. Revisión de nuevas dependencias

Toda incorporación debe responder:

1. ¿Qué necesidad concreta resuelve?
2. ¿Puede resolverse con un componente existente?
3. ¿Automatiza el problema central o aporta una primitiva?
4. ¿Qué permisos requiere?
5. ¿Qué datos recibe?
6. ¿Ejecuta código o crea procesos?
7. ¿Realiza llamadas externas?
8. ¿Qué licencia tiene?
9. ¿Está mantenida?
10. ¿Cómo se fija su versión?
11. ¿Cómo se prueba?
12. ¿Cómo se elimina o sustituye?
13. ¿Cumple las reglas oficiales?

Si una dependencia cruza la frontera de actuación o acceso a datos, requerirá una revisión de seguridad adicional.

## 18. Alternativas consideradas

### 18.1. Servicio externo que opera el escritorio

Rechazado porque delega la solución del reto.

### 18.2. Framework de agente ya preparado

Rechazado para evaluación por restricciones y por sustituir capacidades que deben ser propias.

### 18.3. Dependencia obligatoria de OpenRouter

Rechazada para evitar acoplamiento y porque su uso depende de autorización, disponibilidad y requisitos de datos.

### 18.4. Modelo local obligatorio

No adoptado inicialmente porque sus recursos y capacidad visual no están demostrados en el repositorio.

Puede ser una alternativa futura si se valida.

### 18.5. Cero bibliotecas de terceros

No adoptado como interpretación automática de las reglas.

El diseño propone bibliotecas de bajo nivel, pero su aceptación debe confirmarse si el reglamento exige una prohibición más amplia.

## 19. Consecuencias

### Positivas

- Cumplimiento más claro y auditable.
- Menor dependencia de servicios de automatización.
- Cambio de proveedor sin rediseñar el ejecutor.
- Menor superficie de permisos.
- Entorno reproducible.
- Presupuesto y datos mejor delimitados.

### Negativas

- El equipo debe implementar más capacidades.
- La inferencia externa puede introducir dependencia operativa.
- Los controles de privacidad pueden reducir información disponible.
- Cambiar de modelo requiere pruebas de regresión.
- Un modelo local viable puede exigir recursos adicionales.

## 20. Criterios de aceptación

1. Ningún servicio externo ejecuta la tarea de aplicación.
2. La orquestación y el ejecutor son propios.
3. El modelo no recibe herramientas de actuación.
4. Las credenciales no se envían al proveedor.
5. El uso de inferencia externa está permitido o documentado como no habilitado.
6. El servidor externo de archivos no forma parte del despliegue.
7. Las dependencias están inventariadas y fijadas.
8. El presupuesto se aplica en el camino real de ejecución.
9. El costo no se presenta como exacto usando una tarifa genérica.
10. Los fallos del proveedor no producen éxitos simulados.
11. Las imágenes tienen una política explícita de manejo.
12. El cambio de proveedor no evita las validaciones del núcleo.
13. El despliegue no descarga herramientas nuevas durante una tarea.
14. Las pruebas de aceptación identifican el modelo utilizado.

## 21. Revisión de la decisión

Se revisará cuando:

- Se publiquen aclaraciones oficiales de las reglas.
- Se incorpore un proveedor nuevo.
- Cambie la política de tratamiento de datos.
- Se proponga una herramienta con capacidad de actuación.
- Se introduzca un modelo local.
- Se modifique el esquema de precios.
- Se necesite una dependencia adicional de visión.
- Se cambie la infraestructura de despliegue.

Toda excepción debe quedar documentada y no puede concederse mediante instrucciones del modelo ni contenido de la aplicación.
