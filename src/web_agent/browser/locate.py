from playwright.async_api import Page, Locator

class SemanticLocator:
    """Implementa la jerarquía de identificación sin depender exclusivamente de CSS/XPath (Fase 1)."""
    
    def __init__(self, page: Page):
        self.page = page

    def get_by_role_and_name(self, role: str, name: str) -> Locator:
        """Prioridad 1: Identificación semántica y accesibilidad."""
        return self.page.get_by_role(role, name=name, exact=True)
        
    def get_by_label_text(self, label: str) -> Locator:
        """Prioridad 2: Etiquetas y atributos estables."""
        return self.page.get_by_label(label)

    def get_within_context(self, context_locator: Locator, role: str, name: str) -> Locator:
        """Prioridad 3: Contexto estructural (ej. botón dentro de una tabla)."""
        return context_locator.get_by_role(role, name=name, exact=True)
