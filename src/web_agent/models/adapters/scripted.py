from typing import List, Optional
from web_agent.core.schemas import ProposedAction, ActionType
from web_agent.models.base import BaseModelAdapter


class ScriptedModelAdapter(BaseModelAdapter):
    """Adaptador de pruebas locales que simula respuestas del LLM de forma determinista y sin coste."""

    def __init__(self, actions: Optional[List[ProposedAction]] = None):
        self.actions = list(actions or [])
        self.call_count = 0

    async def propose_action(self, objective: str, page_state: str) -> ProposedAction:
        if self.call_count < len(self.actions):
            action = self.actions[self.call_count]
            self.call_count += 1
            return action

        # Por defecto, si no hay más acciones predefinidas, propone DONE
        return ProposedAction(
            action_type=ActionType.DONE,
            reasoning="Objetivo cumplido según simulación local determinista.",
        )
