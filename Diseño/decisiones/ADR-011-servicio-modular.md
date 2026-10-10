# ADR-011: Servicio Modular Monolítico antes que Arquitectura de Microservicios

**Estado:** Aceptado (Dirección de Despliegue del MVP)  
**Fecha:** 2026-10-10  
**Contexto Técnico:** Arquitectura General de Despliegue  

---

## Contexto y Planteamiento del Problema
Para el despliegue del agente accesible desde Internet antes de las 20:00 y durante el Demo Day, debe elegirse entre una arquitectura distribuida (múltiples microservicios con colas externas como Redis/RabbitMQ) o un servicio modular único en Python.

## Decisión
Construir un **servicio modular monolítico** en un único proceso o contenedor:
1. La API REST, el gestor de tareas (`TaskManager`), el supervisor de workers, el runner y el navegador residen dentro del mismo entorno de ejecución.
2. El registro de tareas se gestiona en memoria o almacenamiento local ligero con interfaz desacoplada (`TaskStore`).
3. No se agregan bases de datos distribuidas ni intermediarios de mensajes a menos que la escala del Demo Day lo justifique formalmente.

## Consecuencias
### Positivas
- Despliegue ultrarrápido, menor consumo de recursos y latencia mínima de comunicación intermodular.
- Cumplimiento directo del tiempo de respuesta < 2 s en `POST /tasks`.
- Mantenimiento y depuración simplificados en un único contenedor.

### Negativas / Límites
- Las tareas en memoria no sobreviven al reinicio de la instancia del servidor (mitigado con registros en disco si se requiere persistencia).
