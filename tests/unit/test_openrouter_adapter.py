"""Adaptador OpenRouter con transporte simulado (§8.7, §8.13; ADR-003)."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from fakes import make_png
from web_agent.core.errors import BudgetExhaustedError, ModelProviderError
from web_agent.execution.budgets import BudgetManager
from web_agent.models.adapters.openrouter import OpenRouterModelAdapter
from web_agent.models.base import ModelRequest
from web_agent.security.redaction import DataRedactor

SECRET = "Pw-Escritorio-998877"


def request():
    return ModelRequest(
        objective=f"Entrar con {SECRET} y exportar",
        perception_mode="visual_only",
        step_index=0,
        max_steps=10,
        remaining_seconds=200,
        observation_meta={"observation_id": "obs_" + "a" * 24, "image_width_px": 1280, "image_height_px": 800},
        image_bytes=make_png(1280, 800),
        image_mime="image/png",
        secret_names=("DESKTOP_PASSWORD",),
    )


def adapter(handler, limit="1.00"):
    budget = BudgetManager(limit_usd=Decimal(limit), max_calls=10, max_output_tokens=10_000_000)
    return OpenRouterModelAdapter(api_key="sk-or-test-key-000000000000", model_name="test/vlm", budget=budget,
                                  transport=httpx.MockTransport(handler)), budget


def test_image_sent_multimodal_without_secrets_and_budget_settled():
    captured = {}

    def handler(req: httpx.Request):
        captured["body"] = json.loads(req.content)
        captured["auth"] = req.headers["authorization"]
        content = '```json\n{"action": "wait", "duration_seconds": 1, "reasoning": "cargando"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 1500, "completion_tokens": 40, "cost": 0.0002}})

    DataRedactor.register_secret(SECRET)
    try:
        a, budget = adapter(handler)
        out = asyncio.run(a.propose_action(request()))
        asyncio.run(a.aclose())
    finally:
        DataRedactor.unregister_secret(SECRET)
    assert out["action"] == "wait"
    user = captured["body"]["messages"][1]["content"]
    assert user[1]["type"] == "image_url" and user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    raw = json.dumps(captured["body"])
    assert SECRET not in raw
    assert captured["body"]["max_tokens"] == 1024
    assert budget.calls_completed == 1 and budget.confirmed_spend_usd > 0
    assert budget.provider_reported_spend_usd == Decimal("0.0002")
    assert a.last_usage.input_tokens == 1500


def test_provider_error_is_classified_and_charged_conservatively():
    a, budget = adapter(lambda req: httpx.Response(503, json={"error": "down"}))
    with pytest.raises(ModelProviderError):
        asyncio.run(a.propose_action(request()))
    assert budget.calls_completed == 1 and not budget.reservations


def test_budget_exhausted_blocks_call_before_dispatch():
    calls = []
    a, _ = adapter(lambda req: calls.append(1) or httpx.Response(200, json={}), limit="0")
    with pytest.raises(BudgetExhaustedError):
        asyncio.run(a.propose_action(request()))
    assert calls == []


def test_dual_vision_sends_both_images_and_macro_doc_only_when_allowed():
    from dataclasses import replace

    from web_agent.models.prompting import build_action_user_text, build_verify_user_text
    from web_agent.models.base import VerificationRequest

    captured = {}

    def handler(req: httpx.Request):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action": "wait", "duration_seconds": 1, "reasoning": "r"}'}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 20}})

    ad, _ = adapter(handler)
    req = replace(request(), allow_macro=True, aux_images=((b"\xff\xd8roi", "image/jpeg"),))
    asyncio.run(ad.propose_action(req))
    content = captured["body"]["messages"][1]["content"]
    images = [c for c in content if c["type"] == "image_url"]
    assert len(images) == 2 and images[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "MODO MACRO" in content[0]["text"]
    assert "MODO MACRO" not in build_action_user_text(request())
    v = build_verify_user_text(VerificationRequest(objective="o", observation_meta={}, local_hints=("OCR local leyó «Guardado»",)))
    assert "no concluyentes" in v and "Guardado" in v
    asyncio.run(ad.aclose())
