"""Observador DOM opcional (documento maestro §8.6).

Solo se usa cuando el modo de percepción lo permite (``dom`` o ``auto``). En
``visual_only`` no se invoca nunca. Correcciones respecto a la base anterior:

- Roles normalizados a roles ARIA que Playwright acepta en ``get_by_role``
  (antes se emitía ``password_field``, que no es un rol válido).
- Unicidad real calculada por (rol, nombre) en lugar de ``unique=True`` fijo.
- Las referencias quedan ligadas a un ``observation_id`` concreto.
"""

from __future__ import annotations

import time

from web_agent.core.observation import DOMObservation, ElementRef

MAX_ELEMENTS = 60

_JS_EXTRACTOR = """(maxItems) => {
  const items = [];
  const nodes = document.querySelectorAll(
    'button, a[href], input, select, textarea, [role=button], [role=link], [role=checkbox], [role=tab], [role=menuitem]'
  );
  const inputRole = (el) => {
    const t = (el.getAttribute('type') || 'text').toLowerCase();
    if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
    if (t === 'checkbox') return 'checkbox';
    if (t === 'radio') return 'radio';
    if (t === 'range') return 'slider';
    if (t === 'number') return 'spinbutton';
    if (t === 'search') return 'searchbox';
    if (el.hasAttribute('list')) return 'combobox';
    return 'textbox';
  };
  for (const el of nodes) {
    if (items.length >= maxItems) break;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    if (rect.width === 0 || rect.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute('role');
    if (!role) {
      if (tag === 'a') role = 'link';
      else if (tag === 'button') role = 'button';
      else if (tag === 'select') role = el.multiple ? 'listbox' : 'combobox';
      else if (tag === 'textarea') role = 'textbox';
      else if (tag === 'input') role = inputRole(el);
      else role = 'generic';
    }
    let name = el.getAttribute('aria-label') || '';
    if (!name && el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) name = lab.innerText;
    }
    if (!name && el.closest('label')) name = el.closest('label').innerText;
    if (!name) name = (tag === 'input' && ['button','submit','reset'].includes(type)) ? (el.value || '') : (el.innerText || '');
    if (!name) name = el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
    name = name.replace(/\\s+/g, ' ').trim().substring(0, 120);
    const form = el.closest('form');
    const inDialog = !!el.closest('dialog, [role=dialog], [role=alertdialog]');
    const inTable = !!el.closest('table, [role=grid], [role=table]');
    items.push({
      role: role.toLowerCase(),
      name: name,
      enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
      input_type: tag === 'input' ? (type || 'text') : null,
      form_submit: !!form && (type === 'submit' || (tag === 'button' && (el.getAttribute('type') || 'submit') === 'submit')),
      context: inDialog ? 'dialog' : (form ? 'form' : (inTable ? 'table' : 'page')),
      sensitive: type === 'password',
    });
  }
  return items;
}"""


class DOMObserver:
    @staticmethod
    async def capture(
        page,
        *,
        run_id: str,
        session_id: str,
        page_id: str,
        navigation_epoch: int,
        geometry_revision: int,
        include_text: bool = True,
    ) -> DOMObservation:
        title = (await page.title() or "")[:300]
        url = page.url
        raw_items = await page.evaluate(_JS_EXTRACTOR, MAX_ELEMENTS)
        visible_text = ""
        if include_text:
            try:
                visible_text = (await page.inner_text("body", timeout=2000))[:6000]
            except Exception:
                visible_text = ""

        counts: dict[tuple[str, str], int] = {}
        cleaned = []
        for item in raw_items or []:
            role = str(item.get("role", "generic"))
            if not role.isalpha() or not 2 <= len(role) <= 32:
                role = "generic"
            name = str(item.get("name") or "")[:300]
            cleaned.append((role, name, item))
            counts[(role, name)] = counts.get((role, name), 0) + 1

        elements = []
        for idx, (role, name, item) in enumerate(cleaned):
            elements.append(
                ElementRef(
                    element_id=f"e{idx}",
                    role=role,
                    name=name,
                    enabled=bool(item.get("enabled", True)),
                    input_type=(str(item["input_type"])[:32] if item.get("input_type") else None),
                    form_submit=bool(item.get("form_submit")),
                    context=item.get("context") if item.get("context") in ("page", "form", "dialog", "table") else "page",
                    # Sin nombre accesible tampoco hay objetivo inequívoco.
                    unique=bool(name) and counts[(role, name)] == 1,
                    sensitive=bool(item.get("sensitive")),
                )
            )
        return DOMObservation(
            run_id=run_id,
            session_id=session_id,
            page_id=page_id,
            navigation_epoch=navigation_epoch,
            geometry_revision=geometry_revision,
            captured_at_monotonic=time.monotonic(),
            page_url=url,
            title=title,
            elements=tuple(elements),
            visible_text=visible_text,
            truncated=len(raw_items or []) >= MAX_ELEMENTS,
        )
