from abc import ABC, abstractmethod
from web_agent.core.schemas import ProposedAction

class BaseModelAdapter(ABC):
    """Interfaz asilada para modelos de IA. No pueden acceder al navegador (Fase 3)."""
    
    @abstractmethod
    async def propose_action(self, objective: str, page_state: str) -> ProposedAction:
        """
        Recibe información estéril de la página y propone un esquema Pydantic.
        Nunca ejecuta comandos directamente.
        """
        pass
