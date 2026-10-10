"""Adaptador OpenRouter con entrada multimodal (documento maestro §8.7; ADR-006).

Cambios respecto a la base: envía la captura por el canal multimodal
(``image_url`` con data URI), usa un cliente HTTP compartido con timeouts,
aplica el presupuesto de forma obligatoria y nunca recibe secretos.
OpenRouter es un adaptador candidato, no una dependencia arquitectónica.
"""

from __future__ import annotations

import base64
import math
import time
from decimal import Decimal
from typing import Optional

import httpx

from web_agent.core.errors import BudgetExhaustedError, ModelOutputError, ModelProviderError
from web_agent.execution.budgets import BudgetExceededError, BudgetManager
from web_agent.models.base import BaseModelAdapter, ModelRequest, ModelUsage, VerificationRequest
from web_agent.models.prompting import (
    SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
    build_action_user_text,
    build_verify_user_text,
    extract_json_object,
)
from web_agent.security.redaction import DataRedactor

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_MTOK = Decimal(1_000_000)


class OpenRouterModelAdapter(BaseModelAdapter):
    supports_vision = True

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        budget: BudgetManager,
        price_input_per_mtok: Decimal = Decimal("0.10"),
        price_output_per_mtok: Decimal = Decimal("0.40"),
        endpoint: str = OPENROUTER_URL,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        super().__init__()
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY no configurada.")
        if budget is None:
            raise ValueError("El presupuesto es obligatorio para llamadas reales.")
        self._api_key = api_key
        self.model_id = model_name
        self.budget = budget
        self.price_in = Decimal(price_input_per_mtok)
        self.price_out = Decimal(price_output_per_mtok)
        self.endpoint = endpoint
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"OpenRouterModelAdapter(model={self.model_id!r})"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ coste
    @staticmethod
    def _estimate_image_tokens(meta: dict) -> int:
        w = int(meta.get("image_width_px", 1280) or 1280)
        h = int(meta.get("image_height_px", 800) or 800)
        return max(258, math.ceil(w / 768) * math.ceil(h / 768) * 258)

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (Decimal(input_tokens) * self.price_in + Decimal(output_tokens) * self.price_out) / _MTOK

    # ------------------------------------------------------------ llamadas
    async def _call(self, system: str, user_text: str, image: Optional[bytes], mime: Optional[str],
                    meta: dict, max_tokens: int, timeout: float,
                    aux_images: tuple[tuple[bytes, str], ...] = ()) -> dict:
        user_text = DataRedactor.redact_secrets_only(user_text)
        content: list[dict] = [{"type": "text", "text": user_text}]
        image_tokens = 0
        if image is not None:
            b64 = base64.b64encode(image).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime or 'image/png'};base64,{b64}"}})
            image_tokens = self._estimate_image_tokens(meta)
        for aux, aux_mime in aux_images:
            b64 = base64.b64encode(aux).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{aux_mime};base64,{b64}"}})
            image_tokens += 258  # imágenes auxiliares pequeñas (≤ 768 px): una tesela
        est_in = (len(system) + len(user_text)) // 3 + image_tokens
        reserve_amount = self._estimate_cost(est_in, max_tokens) * Decimal("1.5")
        try:
            reservation = self.budget.reserve(reserve_amount)
        except BudgetExceededError as exc:
            raise BudgetExhaustedError("Presupuesto de inferencia agotado.") from exc

        payload = {
            "model": self.model_id,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "usage": {"include": True},
        }
        started = time.monotonic()
        dispatched = False
        try:
            dispatched = True
            response = await self._client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            )
        except BaseException as exc:
            # La llamada pudo haberse facturado: contabilidad conservadora (ADR-003 §10.1).
            self._settle(reservation, reserve_amount, 0, None)
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                raise ModelProviderError("El proveedor no respondió a tiempo.") from None
            if isinstance(exc, httpx.HTTPError):
                raise ModelProviderError("Error de red con el proveedor.") from None
            raise
        latency = time.monotonic() - started
        if response.status_code >= 400:
            self._settle(reservation, reserve_amount if dispatched else Decimal(0), 0, None)
            raise ModelProviderError(f"El proveedor respondió HTTP {response.status_code}.")
        try:
            data = response.json()
            usage = data.get("usage") or {}
            in_tok = int(usage.get("prompt_tokens") or est_in)
            out_tok = int(usage.get("completion_tokens") or max_tokens)
            reported = usage.get("cost")
            reported_dec = Decimal(str(reported)) if reported is not None else None
            estimated = self._estimate_cost(in_tok, out_tok)
            self._settle(reservation, estimated, in_tok + out_tok, reported_dec)
            self.last_usage = ModelUsage(
                model_id=self.model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=estimated,
                provider_reported_cost_usd=reported_dec,
                latency_seconds=latency,
            )
            message = data["choices"][0]["message"]
            text = message.get("content")
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            return extract_json_object(text or "")
        except BudgetExhaustedError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelOutputError("Respuesta del proveedor sin JSON válido.") from exc

    def _settle(self, reservation: str, amount: Decimal, tokens: int, reported: Optional[Decimal]) -> None:
        try:
            if amount > 0:
                self.budget.confirm(reservation, actual_spend=amount, tokens_used=tokens, provider_reported=reported)
            else:
                self.budget.release(reservation)
        except BudgetExceededError as exc:
            raise BudgetExhaustedError("Presupuesto de inferencia agotado.") from exc
        except KeyError:
            pass

    async def propose_action(self, request: ModelRequest) -> dict:
        return await self._call(
            SYSTEM_PROMPT,
            build_action_user_text(request),
            request.image_bytes,
            request.image_mime,
            request.observation_meta,
            request.max_output_tokens,
            request.timeout_seconds,
            request.aux_images,
        )

    async def verify_outcome(self, request: VerificationRequest) -> dict:
        return await self._call(
            VERIFY_SYSTEM_PROMPT,
            build_verify_user_text(request),
            request.image_bytes,
            request.image_mime,
            request.observation_meta,
            request.max_output_tokens,
            request.timeout_seconds,
        )
