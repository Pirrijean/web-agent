"""Componentes del motor tri-nivel: extracción de objetivos, macros, imagen, caché, OCR y router."""

import asyncio

import pytest

from fakes import FakeOcr, make_png, make_png_regions
from web_agent.core.adaptive_router import (
    LEVEL_CLASSIC,
    LEVEL_LOCAL,
    LEVEL_MACRO,
    AdaptiveRouter,
    extract_click_targets,
)
from web_agent.core.context import RunState, StepRecord, TaskContext
from web_agent.core.deadline import Deadline
from web_agent.core.errors import ModelOutputError
from web_agent.core.macro_schemas import parse_model_output
from web_agent.core.observation import ImageTransform, VisualObservation, new_observation_id
from web_agent.core.runner import AgentRunner
from web_agent.core.schemas import ActionType
from web_agent.core.semantic_cache import CacheKey, SemanticCache, screen_signature
from web_agent.perception import ocr_engine as ocr_mod
from web_agent.perception.dual_vision import build_dual_view, roi_to_global
from web_agent.perception.image_ops import change_map, crop_roi, decode, thumbnail
from web_agent.perception.ocr_engine import (
    NullOcrEngine,
    OcrUnavailable,
    TextBox,
    create_ocr_engine,
    normalize_text,
    text_contains,
    text_matches,
)

OBS = "obs_" + "c" * 24
validate = AgentRunner._validate_model_action


# ------------------------------------------------------------------ objetivos literales
@pytest.mark.parametrize("objective,expected", [
    ("Haz clic en 'Clientes' y luego en 'Nuevo'", ["Clientes", "Nuevo"]),
    ("Pulsa el botón «Buscar» del formulario", ["Buscar"]),
    ("Escribe 'Empresa S.A.' en el campo Nombre", []),
    ("Abre 'Facturación', escribe 'ACME' y pulsa 'Guardar'", ["Facturación", "Guardar"]),
    ("Busca 'Pedro' en la lista", []),
    ("Exportar el informe de ventas", []),
    ("Selecciona la pestaña \"Informes\", después 'Mensual'", ["Informes", "Mensual"]),
])
def test_extract_click_targets(objective, expected):
    assert [t.text for t in extract_click_targets(objective)] == expected


# ------------------------------------------------------------------ macros
def macro(*steps, **kw):
    return {"macro_action": "rellenar", "reasoning": "formulario", "steps": list(steps), **kw}


CLICK = {"action": "click_xy", "x": 300, "y": 300, "target_description": "Campo Nombre"}
TYPE = {"action": "type_text", "text": "ACME"}
TAB = {"action": "press_key", "key": "Tab"}


def test_single_action_passthrough():
    p = parse_model_output({**CLICK, "observation_id": OBS, "reasoning": "r"}, validate=validate,
                           observation_id=OBS, allow_macro=True)
    assert len(p.actions) == 1 and p.macro is None and not p.is_burst


def test_macro_fills_observation_and_inherits_metadata():
    p = parse_model_output(macro(CLICK, TYPE, TAB, screen_summary="Formulario de clientes"),
                           validate=validate, observation_id=OBS, allow_macro=True)
    assert [a.action for a in p.actions] == [ActionType.CLICK_XY, ActionType.TYPE_TEXT, ActionType.PRESS_KEY]
    assert all(a.observation_id == OBS for a in p.actions)
    assert p.actions[0].screen_summary == "Formulario de clientes"
    assert p.actions[1].reasoning == "formulario"


def test_macro_not_allowed_keeps_only_first_step():
    p = parse_model_output(macro(CLICK, TYPE), validate=validate, observation_id=OBS, allow_macro=False)
    assert len(p.actions) == 1 and p.truncated_reason == "nivel2_no_permitido"


@pytest.mark.parametrize("bad,reason", [
    ({"action": "navigate", "url": "https://desktop.example.com/"}, "paso_2_no_permitido_en_rafaga"),
    ({"action": "done"}, "paso_2_no_permitido_en_rafaga"),
    ({"action": "fill_dom", "target_element_id": "e1", "value": "x"}, "paso_2_invalido"),
    ({"action": "click", "x": 1, "y": 1}, "paso_2_invalido"),  # catálogo cerrado también en macros
])
def test_burst_truncated_at_first_invalid_step(bad, reason):
    p = parse_model_output(macro(CLICK, bad, TYPE), validate=validate, observation_id=OBS, allow_macro=True)
    assert len(p.actions) == 1 and p.truncated_reason == reason


def test_invalid_first_step_rejects_whole_macro():
    with pytest.raises(ModelOutputError):
        parse_model_output(macro({"action": "click", "x": 1, "y": 1}, TYPE), validate=validate,
                           observation_id=OBS, allow_macro=True)
    with pytest.raises(ModelOutputError):
        parse_model_output(macro(*([TAB] * 6)), validate=validate, observation_id=OBS, allow_macro=True)
    with pytest.raises(ModelOutputError):
        parse_model_output({"macro_action": "x", "steps": [TAB], "extra": 1}, validate=validate,
                           observation_id=OBS, allow_macro=True)


def test_roi_coordinates_are_transformed_deterministically():
    roi = (200, 100, 600, 500)
    p = parse_model_output(macro({**CLICK, "x": 500, "y": 500, "coord_space": "roi"}), validate=validate,
                           observation_id=OBS, allow_macro=True, roi_box=roi)
    assert (p.actions[0].x, p.actions[0].y) == (400, 300)
    with pytest.raises(ModelOutputError):  # sin recorte vigente no se acepta coord_space=roi
        parse_model_output({**CLICK, "observation_id": OBS, "reasoning": "r", "coord_space": "roi"},
                           validate=validate, observation_id=OBS, allow_macro=True, roi_box=None)
    assert roi_to_global(1000, 0, roi) == (600, 100)


# ------------------------------------------------------------------ imagen
def page_png(focus=(640, 400), shade=90, bg=200):
    fx, fy = focus
    return make_png_regions(1280, 800, bg, [(fx - 60, fy - 15, fx + 60, fy + 15, shade)])


def test_change_map_local_vs_global():
    assert change_map(page_png(), page_png()).unchanged
    local = change_map(page_png(shade=90), page_png(shade=180))
    assert 0 < local.changed_fraction < 0.05 and local.clusters == 1
    x0, y0, x1, y1 = local.bbox
    assert x0 <= 640 <= x1 and y0 <= 400 <= y1
    assert change_map(page_png(), page_png(bg=40)).changed_fraction > 0.9
    two_zones = change_map(page_png(focus=(100, 100)), page_png(focus=(1100, 700)))
    assert two_zones.clusters == 2


def test_thumbnail_and_roi_crop():
    thumb = decode(thumbnail(page_png()))
    assert thumb.size == (400, 250)
    crop = crop_roi(page_png(), (1270, 790))  # junto al borde: se ajusta dentro de la imagen
    assert crop.box == (780, 450, 1280, 800) and decode(crop.image).size == (500, 350)
    assert crop.box_norm(1280, 800) == (609, 562, 1000, 1000)


def test_dual_view_falls_back_to_full_image_on_dispersed_changes():
    prev = page_png(focus=(300, 300), shade=90)
    local = build_dual_view(page_png(focus=(300, 300), shade=200), prev, (300, 300), width=1280, height=800)
    assert local is not None and local.meta()["vista"] == "dual"
    assert build_dual_view(page_png(bg=40), prev, (300, 300), width=1280, height=800) is None
    assert build_dual_view(page_png(), None, (300, 300), width=1280, height=800) is None


# ------------------------------------------------------------------ caché semántica
LAYOUT = [TextBox(100, 100, 220, 124), TextBox(100, 160, 260, 184), TextBox(400, 300, 520, 324)]


def test_signature_ignores_clock_and_taskbar_but_not_layout():
    sig = screen_signature(LAYOUT, 1280, 800)
    clock = [TextBox(1180, 8, 1270, 30), TextBox(1200, 775, 1270, 798)]
    clock_changed = [TextBox(1176, 8, 1262, 30), TextBox(1190, 775, 1268, 798)]
    assert screen_signature(LAYOUT + clock, 1280, 800) == sig
    assert screen_signature(LAYOUT + clock_changed, 1280, 800) == sig
    assert screen_signature(LAYOUT + [TextBox(600, 500, 700, 524)], 1280, 800) != sig
    assert screen_signature(LAYOUT[:2], 1280, 800) is None  # demasiado vacía para ser fiable


def test_cache_composite_key_and_lru():
    cache = SemanticCache(max_entries=2)
    box = TextBox(1, 2, 3, 4, "Clientes", 0.99)
    k1 = CacheKey.build("sig", "Clientes", 0, None)
    cache.put(k1, box)
    assert cache.get(CacheKey.build("sig", "  CLIENTES ", 0, None)) == box
    assert cache.get(CacheKey.build("sig", "Clientes", 1, None)) is None  # otro paso del flujo
    assert cache.get(CacheKey.build("sig", "Clientes", 0, "click_xy@(1, 2)")) is None
    cache.put(CacheKey.build("a", "x", 0, None), box)
    cache.put(CacheKey.build("b", "x", 0, None), box)
    assert len(cache) == 2 and cache.get(k1) is None


# ------------------------------------------------------------------ OCR
def test_text_normalization_and_matching():
    assert normalize_text("  «FACTURACIÓN»: ") == "facturacion"
    assert text_matches("Facturación", "facturacion") and not text_matches("Facturación 2026", "Facturación")
    assert text_contains("Cliente guardado correctamente", "guardado")
    assert not text_contains("Salvaguardado", "guardado")


def test_targeted_find_reads_only_plausible_boxes():
    boxes = [(100, 100, 190, 124, "Clientes", 0.99), (100, 200, 700, 224, "Un texto muy largo que no encaja", 0.99),
             (100, 300, 120, 324, "X", 0.99)]
    ocr = FakeOcr(lambda: boxes)
    result = asyncio.run(ocr.find_text(b"img", ["Clientes"], timeout=2))
    assert [b.text for b in result.matches] == ["Clientes"]
    assert [b.text for b in result.read] == ["Clientes"]  # las otras cajas ni se reconocen
    assert len(result.layout) == 3


def test_null_engine_and_degradation(monkeypatch):
    with pytest.raises(OcrUnavailable):
        asyncio.run(NullOcrEngine().detect(b"", timeout=1))
    assert not create_ocr_engine(enabled=False, min_free_memory_mb=0).available
    monkeypatch.setattr(ocr_mod, "available_memory_mb", lambda: 100)
    low = create_ocr_engine(enabled=True, min_free_memory_mb=600)
    assert not low.available and "Memoria" in low.unavailable_reason


def test_busy_engine_does_not_pile_up_threads():
    ocr = FakeOcr(lambda: [])
    ocr._lock.acquire()
    try:
        with pytest.raises(OcrUnavailable):
            asyncio.run(ocr.detect(b"", timeout=1))
    finally:
        ocr._lock.release()


# ------------------------------------------------------------------ router
def ctx(objective="Haz clic en 'Clientes'", mode="visual_only"):
    return TaskContext(task_id="t", run_id="run_x", objective=objective, perception_mode=mode,
                       deadline=Deadline.start(300), max_steps=20)


def state_with_image(sha="a" * 64):
    obs_id = new_observation_id()
    obs = VisualObservation(
        observation_id=obs_id, task_id="t", run_id="run_x", session_id="s", page_id="p",
        navigation_epoch=0, geometry_revision=0, captured_at_monotonic=0.0, page_url="https://d/",
        viewport_width_css=1280, viewport_height_css=800, image_width_px=1280, image_height_px=800,
        image_format="png", image_sha256=sha, image_bytes_len=10, image_reference=f"mem:{obs_id}",
        transform=ImageTransform())
    return RunState(current_visual=obs, current_image=make_png(4, 4), observation_stale=False)


TARGET = (580, 380, 700, 404, "Clientes", 0.98)


def router_for(boxes, **kw):
    r = AdaptiveRouter(ocr=FakeOcr(lambda: boxes), **kw)
    r.start(ctx())
    return r


def test_level3_candidate_is_r0_click_at_text_center():
    r = router_for([TARGET, (100, 100, 200, 124, "Inicio", 0.99)])
    st = state_with_image()
    r.begin_step(st)
    attempt = asyncio.run(r.try_level_3(ctx(), st))
    a = attempt.candidate.action
    assert a.action == ActionType.CLICK_XY and (a.x, a.y) == (500, 490)
    assert a.effect_class == "navigate" and a.observation_id == st.current_visual.observation_id


@pytest.mark.parametrize("boxes,reason", [
    ([], "level3_text_not_found"),
    ([TARGET, (580, 600, 700, 624, "Clientes", 0.99)], "level3_ambiguous"),
    ([(580, 380, 700, 404, "Clientes", 0.6)], "level3_low_confidence"),
    ([TARGET, (580, 420, 700, 444, "Eliminar todo", 0.99)], "level3_danger_nearby"),
    ([TARGET, (720, 380, 900, 404, "Advertencia", 0.99)], "level3_danger_nearby"),
])
def test_level3_escalates_when_not_safe(boxes, reason):
    r = router_for(boxes)
    st = state_with_image()
    r.begin_step(st)
    attempt = asyncio.run(r.try_level_3(ctx(), st))
    assert attempt.candidate is None and attempt.reason == reason
    r.begin_step(st)
    assert r.cap == LEVEL_MACRO  # escalamiento: el paso siguiente no reintenta en local


def test_level3_same_screen_not_reanalyzed_and_attempts_bounded():
    r = router_for([])
    for i in range(5):
        st = state_with_image(sha=str(i % 4) * 64)
        r.begin_step(st)
        r.begin_step(st)  # restablece el nivel máximo
        asyncio.run(r.try_level_3(ctx(), st))
    assert r.ocr.detect_calls == 3 and r.next_target() is None


def test_level3_effect_resolution_and_cache():
    r = router_for([TARGET, (100, 100, 200, 124, "Inicio", 0.99), (100, 160, 200, 184, "Ayuda", 0.99)])
    st = state_with_image()
    r.begin_step(st)
    cand = asyncio.run(r.try_level_3(ctx(), st)).candidate
    r.on_level3_executed(cand)
    st.history.append(StepRecord(index=1, action="click_xy", summary="s", outcome="ok", screen_changed=True))
    assert r.begin_step(st) == "level3_effect_observed"
    assert r.next_target() is None and len(r.cache) == 1 and r.stats.level3_actions == 1


def test_level3_no_effect_blacklists_target():
    r = router_for([TARGET, (100, 100, 200, 124, "Inicio", 0.99), (100, 160, 200, 184, "Ayuda", 0.99)])
    st = state_with_image()
    r.begin_step(st)
    r.on_level3_executed(asyncio.run(r.try_level_3(ctx(), st)).candidate)
    st.history.append(StepRecord(index=1, action="click_xy", summary="s", outcome="ok", screen_changed=False))
    assert r.begin_step(st) == "level3_no_effect"
    assert r.cap == LEVEL_MACRO and r.next_target() is None


def test_escalation_rules_have_no_cycles():
    r = AdaptiveRouter(ocr=FakeOcr(lambda: []))
    r.start(ctx())
    st = state_with_image()
    r.begin_step(st)
    assert r.cap == LEVEL_LOCAL
    r.on_burst(executed=1, aborted=True)
    r.begin_step(st)
    assert r.cap == LEVEL_CLASSIC  # ráfaga interrumpida -> Nivel 1
    r.on_model_step(LEVEL_CLASSIC, ok=True)
    r.begin_step(st)
    assert r.cap == LEVEL_MACRO  # obstáculo resuelto en N1 -> como mucho N2
    r.on_model_step(LEVEL_MACRO, ok=True)
    r.begin_step(st)
    assert r.cap == LEVEL_LOCAL  # éxito en N2 -> máximo restaurado


def test_router_without_ocr_or_in_dom_mode_disables_local_levels():
    r = AdaptiveRouter(ocr=NullOcrEngine())
    r.start(ctx())
    assert not r.level3_enabled and r.describe()["ocr"].startswith("no")
    dom = AdaptiveRouter(ocr=FakeOcr(lambda: []))
    dom.start(ctx(mode="dom"))
    assert not dom.level3_enabled and not dom.level2_enabled


def test_transient_ocr_failure_does_not_burn_the_screen():
    calls = {"n": 0}

    def screen():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OcrUnavailable("ocupado")
        return [TARGET, (100, 100, 200, 124, "Inicio", 0.99)]

    r = router_for([])
    r.ocr.screen = screen
    st = state_with_image()
    r.begin_step(st)
    assert asyncio.run(r.try_level_3(ctx(), st)).reason == "level3_ocr_unavailable"
    r.begin_step(st)
    r.begin_step(st)  # tras el escalamiento, el nivel máximo vuelve
    assert asyncio.run(r.try_level_3(ctx(), st)).candidate is not None  # misma pantalla, reintento válido
