"""Contrato de skills nativas (documento maestro §8.11; ADR-002 §10).

Una skill es un procedimiento reutilizable, no un plugin privilegiado:

- se compone exclusivamente de acciones primitivas del catálogo cerrado;
- cada paso pasa por validación contextual, política, ejecutor y auditoría
  a través de ``SkillRuntime`` (implementado por el runner);
- respeta el mismo deadline y presupuesto que el resto de la tarea;
- no codifica rutas, coordenadas ni selectores de aplicaciones concretas.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Protocol

from web_agent.core.context import RunState, TaskContext
from web_agent.core.schemas import ProposedAction


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    version: str
    purpose: str
    preconditions: tuple[str, ...]
    postcondition: str
    risks: tuple[str, ...]
    repetition_rule: str
    evidence: str


class SkillRuntime(Protocol):
    """Superficie mínima que el runner expone a las skills. Sin acceso a Playwright."""

    async def run_primitive(self, action: ProposedAction, *, origin: str) -> bool:
        """Valida, autoriza, audita y ejecuta una acción primitiva. True si se ejecutó."""

    async def observe_visual(self) -> str:
        """Captura una observación visual nueva y devuelve su hash de imagen."""


class Skill(ABC):
    spec: SkillSpec

    def applies(self, ctx: TaskContext) -> bool:
        return True

    def guidance(self, ctx: TaskContext, state: RunState) -> list[str]:
        return []
