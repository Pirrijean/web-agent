# 🌐 web-agent

> Agente generalista de automatización web con políticas de seguridad verificables, aislamiento de red y validación estricta de acciones.

Diseñado para resolver tareas de navegación, extracción y verificación determinista en entornos web, preparado para el brief oficial de la hackathon.

---

## 🏗️ Arquitectura y Principios de Diseño

El sistema opera bajo un bucle estricto de control:
$$\text{Observar} \longrightarrow \text{Proponer} \longrightarrow \text{Validar} \longrightarrow \text{Actuar} \longrightarrow \text{Verificar} \longrightarrow \text{Auditar}$$

1. **Observación Acotada (`browser/observe.py`):** Extrae del DOM únicamente controles interactivos visibles asignando identificadores temporales (`e0`, `e1`...). Oculta y protege campos sensibles como contraseñas.
2. **Frontera de Validación Estricta (`core/policy.py`):** Enforcer *fail-closed*. Ninguna acción no autorizada o no contextualizada se ejecuta en el navegador.
3. **Aislamiento de Red y Mitigación SSRF (`security/`):** Intercepción a nivel de protocolo con Playwright, bloqueo de esquemas no seguros (`file://`, `javascript:`), rangos privados y metadatos de nube.
4. **Presupuesto y Contabilidad (`execution/budgets.py`):** Control de reservas y confirmación en `Decimal` para evitar sobrecostes.
5. **Auditoría *Fail-Safe* (`audit/events.py`):** Si falla la persistencia obligatoria de auditoría, el sistema se detiene de inmediato.

---

## 🚀 Inicio Rápido

### Requisitos previos
- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)

### 1. Instalación
```bash
git clone https://github.com/Pirrijean/web-agent.git
cd web-agent
uv sync
```

### 2. Diagnóstico del Entorno (`doctor`)
Verifica que el entorno, las dependencias y los directorios de auditoría estén listos sin gastar saldo:
```bash
uv run python main.py doctor
```

### 3. Inspeccionar un Perfil de Tarea
```bash
uv run python main.py inspect-profile profiles/examples/portal_demo.yaml
```

### 4. Ejecución Local (Simulador / Offline)
Ejecuta el agente localmente de forma determinista y segura:
```bash
uv run python main.py run --profile profiles/examples/portal_demo.yaml --objective "Comprobar navegación y acceso autorizado"
```

### 5. Adaptador Oficial para Evaluación (`entrypoint.py`)
Listo para conectarse al contrato del evaluador del evento:
```bash
uv run python -m web_agent.entrypoint --health
```

---

## 🧪 Pruebas Automatizadas

```bash
uv run pytest
```
Todas las pruebas unitarias y de seguridad cubren denegación de permisos, rechazo de path traversal, validación cruzada y políticas fail-closed.
