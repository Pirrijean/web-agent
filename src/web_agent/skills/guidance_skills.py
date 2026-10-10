"""Skills procedimentales basadas en observación (documento maestro §8.11).

- SKILL-02 CanvasFormFill
- SKILL-03 LegacyPopupManager
- SKILL-04 ReportExportTracker
- SKILL-05 ZeroShotDiscovery

Estas skills no emiten eventos por sí mismas: aportan el procedimiento y las
restricciones al planificador, y sus pasos se ejecutan como acciones
primitivas sometidas a la misma política. Las reglas críticas que no pueden
depender del modelo (riesgo de Enter, reemplazo explícito, anti-bucle) están
impuestas en código por la política y el validador contextual.
"""

from __future__ import annotations

from web_agent.core.context import RunState, TaskContext
from web_agent.skills.base import Skill, SkillSpec


class CanvasFormFill(Skill):
    spec = SkillSpec(
        skill_id="canvas_form_fill",
        version="1.0",
        purpose="Rellenar campos en GUI gráfica/canvas sin DOM.",
        preconditions=("Campo visible en la captura vigente",),
        postcondition="El valor esperado aparece en el campo en una captura posterior.",
        risks=("Escribir en el campo equivocado", "Borrar valores por defecto"),
        repetition_rule="Si el valor no aparece tras escribir, reobservar antes de reescribir.",
        evidence="Captura posterior con el valor visible.",
    )

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        return [
            "FORMULARIOS: usa type_text con x,y del campo para enfocarlo y escribir en un solo paso. "
            "Usa clear_before=true SOLO si el campo ya contiene texto que la tarea pide sustituir. "
            "No pulses Tab ni Enter al terminar salvo que la tarea lo requiera. "
            "Comprueba en la siguiente captura que el valor quedó escrito en el campo correcto.",
        ]


class LegacyPopupManager(Skill):
    spec = SkillSpec(
        skill_id="legacy_popup_manager",
        version="1.0",
        purpose="Interpretar y resolver diálogos modales de software legacy.",
        preconditions=("Diálogo visible en la captura vigente",),
        postcondition="El diálogo desaparece y el estado es coherente con el objetivo.",
        risks=("Confirmar un borrado", "Aceptar condiciones no deseadas"),
        repetition_rule="Nunca pulsar Enter a ciegas; leer el mensaje antes de elegir.",
        evidence="Captura posterior sin el diálogo.",
    )

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        return [
            "DIÁLOGOS: si aparece una ventana emergente, lee su mensaje y elige la opción coherente con "
            "el objetivo autorizado haciendo clic en el botón concreto (describe el botón en "
            "target_description). No uses Enter a ciegas. Si el diálogo advierte de una operación "
            "destructiva no pedida, elige Cancelar/No.",
        ]


class ReportExportTracker(Skill):
    spec = SkillSpec(
        skill_id="report_export_tracker",
        version="1.0",
        purpose="Generar y exportar informes en sistemas legacy.",
        preconditions=("Menú de informes o exportación localizado",),
        postcondition="Confirmación visible de exportación/guardado en el escritorio remoto.",
        risks=("Confundir descarga local con archivo remoto", "Inventar contenido del archivo"),
        repetition_rule="No relanzar la exportación sin comprobar si la anterior terminó.",
        evidence="Mensaje de finalización, diálogo 'Guardar como' cerrado o archivo visible.",
    )

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        return [
            "EXPORTACIONES: guardar un archivo dentro del escritorio remoto no produce una descarga "
            "local. Verifica el resultado con evidencia visible (mensaje de finalizado, diálogo cerrado, "
            "archivo listado). No afirmes nombre, tamaño ni contenido que no veas.",
        ]


class ZeroShotDiscovery(Skill):
    spec = SkillSpec(
        skill_id="zero_shot_discovery",
        version="1.0",
        purpose="Orientarse en aplicaciones desconocidas con hipótesis revisables.",
        preconditions=(),
        postcondition="Ruta hacia el módulo relevante identificada.",
        risks=("Asumir distribuciones de menú universales",),
        repetition_rule="Memoria limitada a la tarea; las coordenadas no se recuerdan entre pantallas.",
        evidence="Resúmenes de pantalla visitados.",
    )

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        lines = [
            "EXPLORACIÓN: en una aplicación desconocida revisa menús superiores, barras de herramientas y "
            "paneles laterales. Rellena screen_summary con una frase sobre la pantalla actual. "
            "Las coordenadas de capturas anteriores no son válidas: calcula siempre sobre la imagen actual.",
        ]
        return lines

    @staticmethod
    def remember(state: RunState, screen_summary: str | None) -> None:
        if not screen_summary:
            return
        summary = screen_summary.strip()[:200]
        if summary and (not state.navigation_memory or state.navigation_memory[-1] != summary):
            state.navigation_memory.append(summary)
