import uuid
from typing import Dict, List, Optional
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, Field


class ObservedElement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    element_id: str
    role: str
    name: str = Field(..., max_length=150)
    is_enabled: bool = True
    is_visible: bool = True
    form_context: Optional[str] = None


class PageObservationSnapshot(BaseModel):
    """Instantánea inmutable y acotada del estado interactivo de la página (Fase 1 P0)."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str
    url: str = Field(..., max_length=500)
    title: str = Field(..., max_length=200)
    elements: Dict[str, ObservedElement] = Field(default_factory=dict)

    def to_model_prompt(self, max_chars: int = 2500) -> str:
        """Genera un resumen textual seguro, sin exponer contraseñas ni superar el límite de coste."""
        lines = [
            f"OBSERVATION_ID: {self.observation_id}",
            f"URL: {self.url}",
            f"TITULO: {self.title}",
            "ELEMENTOS INTERACTIVOS DISPONIBLES:",
        ]
        for eid, el in self.elements.items():
            line = f"  [{eid}] {el.role.upper()}: '{el.name}'"
            if el.form_context:
                line += f" (Formulario: {el.form_context})"
            lines.append(line)

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [Elementos truncados por límite de seguridad de contexto]"
        return text


class DOMObserver:
    """Extrae de forma segura controles interactivos esenciales asignando identificadores temporales."""

    @staticmethod
    async def capture_observation(page: Page) -> PageObservationSnapshot:
        obs_id = f"obs_{uuid.uuid4().hex[:12]}"
        if page is None or page.is_closed():
            return PageObservationSnapshot(
                observation_id=obs_id,
                url="about:blank",
                title="No page",
                elements={},
            )

        title = await page.title()
        url = page.url

        # Extraer elementos interactivos clave mediante script seguro en el navegador
        # Nunca extraemos valores de inputs de tipo password
        js_extractor = """() => {
            const items = [];
            const interactive = document.querySelectorAll('button, a, input, select, textarea');
            let idx = 0;
            for (const el of interactive) {
                if (idx >= 30) break; // Limite duro para acotar coste y contexto
                const isPassword = el.type === 'password';
                const isHidden = el.type === 'hidden' || el.offsetParent === null;
                if (isHidden) continue;

                let role = el.tagName.toLowerCase();
                if (role === 'a') role = 'link';
                if (role === 'input') role = isPassword ? 'password_field' : (el.type || 'input');

                let name = el.getAttribute('aria-label') || el.innerText || el.placeholder || el.name || '';
                name = name.trim().substring(0, 100);

                const form = el.closest('form');
                const formName = form ? (form.name || form.id || 'form') : null;

                items.push({
                    element_id: 'e' + idx,
                    role: role,
                    name: isPassword ? '[CAMPO_PASSWORD_PROTEGIDO]' : name,
                    is_enabled: !el.disabled,
                    is_visible: true,
                    form_context: formName
                });
                idx++;
            }
            return items;
        }"""
        try:
            raw_items = await page.evaluate(js_extractor)
        except Exception:
            raw_items = []

        elements_map: Dict[str, ObservedElement] = {}
        for item in raw_items:
            eid = item["element_id"]
            elements_map[eid] = ObservedElement(
                element_id=eid,
                role=item["role"],
                name=item["name"] or "sin_etiqueta",
                is_enabled=item["is_enabled"],
                is_visible=item["is_visible"],
                form_context=item.get("form_context"),
            )

        return PageObservationSnapshot(
            observation_id=obs_id,
            url=url,
            title=title[:200] if title else "Sin titulo",
            elements=elements_map,
        )
