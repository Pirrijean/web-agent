"""Geometría (§9.2) y contrato de acciones (§10). Documento maestro §18.1."""

import pytest
from pydantic import ValidationError

from fakes import make_png
from web_agent.core.geometry import GeometryError, image_dimensions, norm_to_css
from web_agent.core.schemas import ActionType, ProposedAction, normalize_key

OBS = "obs_" + "a" * 24


@pytest.mark.parametrize("w,h", [(1280, 800), (1920, 1080), (320, 240), (1366, 768)])
def test_corners_map_to_first_and_last_css_pixel(w, h):
    assert norm_to_css(0, 0, viewport_width_css=w, viewport_height_css=h) == (0, 0)
    assert norm_to_css(1000, 1000, viewport_width_css=w, viewport_height_css=h) == (w - 1, h - 1)


def test_all_normalized_values_stay_inside_viewport():
    for x in range(0, 1001, 7):
        xc, yc = norm_to_css(x, 1000 - x, viewport_width_css=1281, viewport_height_css=799)
        assert 0 <= xc < 1281 and 0 <= yc < 799


@pytest.mark.parametrize("bad", [-1, 1001, 500.5, True])
def test_out_of_range_or_non_integer_rejected(bad):
    with pytest.raises(GeometryError):
        norm_to_css(bad, 0, viewport_width_css=100, viewport_height_css=100)


def test_explicit_transform_is_inverted():
    # Imagen a escala 0.5 de un recorte que empieza en (100, 50) CSS.
    x, y = norm_to_css(1000, 0, viewport_width_css=1280, viewport_height_css=800,
                       image_width_px=200, image_height_px=100, offset_x_css=100, offset_y_css=50, scale=0.5)
    assert (x, y) == (100 + 398, 50)


def test_png_dimensions_parsed():
    assert image_dimensions(make_png(37, 21)) == (37, 21, "png")
    with pytest.raises(GeometryError):
        image_dimensions(b"not an image")


def test_spatial_action_requires_coordinates_and_observation():
    with pytest.raises(ValidationError):
        ProposedAction(action="click_xy", observation_id=OBS, reasoning="r")
    with pytest.raises(ValidationError):
        ProposedAction(action="click_xy", x=1, y=2, reasoning="r")
    a = ProposedAction(action="click_xy", x=1, y=2, observation_id=OBS, reasoning="r")
    assert a.action == ActionType.CLICK_XY


def test_legacy_alias_action_type_accepted():
    a = ProposedAction.model_validate({"action_type": "wait", "duration_seconds": 1, "reasoning": "r"})
    assert a.action == ActionType.WAIT


def test_extra_and_incompatible_fields_rejected():
    with pytest.raises(ValidationError):
        ProposedAction(action="navigate", url="https://example.com", reasoning="r", malicious="x")
    with pytest.raises(ValidationError):
        ProposedAction(action="navigate", url="https://example.com", key="Enter", reasoning="r")
    with pytest.raises(ValidationError):
        ProposedAction(action="done", x=1, y=1, reasoning="r")


def test_type_text_requires_exactly_one_value_source():
    with pytest.raises(ValidationError):
        ProposedAction(action="type_text", observation_id=OBS, reasoning="r")
    with pytest.raises(ValidationError):
        ProposedAction(action="type_text", observation_id=OBS, text="a", secret_ref="DESKTOP_PASSWORD", reasoning="r")
    ProposedAction(action="type_text", observation_id=OBS, secret_ref="DESKTOP_PASSWORD", reasoning="r")


@pytest.mark.parametrize("bad", [0.0, -1, 11, 60])
def test_wait_bounds(bad):
    with pytest.raises(ValidationError):
        ProposedAction(action="wait", duration_seconds=bad, reasoning="r")


def test_key_catalogue():
    assert normalize_key("ctrl+A") == "Control+a"
    assert normalize_key("enter") == "Enter"
    assert normalize_key("Shift+Control+s") == "Control+Shift+s"
    for bad in ["Control+Alt+Delete", "Alt+Tab", "Meta+r", "rm -rf", "Control+Control+a", ""]:
        with pytest.raises(ValueError):
            normalize_key(bad)


def test_scroll_requires_nonzero_delta():
    with pytest.raises(ValidationError):
        ProposedAction(action="scroll", observation_id=OBS, delta_y=0, reasoning="r")
    ProposedAction(action="scroll", observation_id=OBS, delta_y=300, reasoning="r")
