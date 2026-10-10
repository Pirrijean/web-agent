import asyncio
import json
import os
from decimal import Decimal
from typing import Optional

import httpx

from web_agent.core.errors import ModelOutputError
from web_agent.core.schemas import ProposedAction
from web_agent.execution.budgets import BudgetManager
from web_agent.models.base import BaseModelAdapter


class OpenRouterModelAdapter(BaseModelAdapter):
    """Adaptador seguro para invocar modelos vía OpenRouter API con control presupuestario."""

    def __init__(
        self,
        model_name: str = "google/gemini-2.0-flash-001",
        budget_manager: Optional[BudgetManager] = None,
        timeout_seconds: float = 30.0,
        max_cost_per_call: Decimal = Decimal("0.05"),
    ):
        self.model_name = model_name
        self.budget_manager = budget_manager
        self.timeout_seconds = timeout_seconds
        self.max_cost_per_call = max_cost_per_call
        self.api_key = os.environ.get("OPENROUTER_API_KEY")

    async def propose_action(self, objective: str, page_state: str) -> ProposedAction:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY no configurada en el entorno.")

        res_id = None
        if self.budget_manager is not None:
            res_id = self.budget_manager.reserve(self.max_cost_per_call)

        system_prompt = (
            "Eres un agente de navegación web restringido y seguro. "
            "Devuelve EXCLUSIVAMENTE un objeto JSON válido que respete el esquema: "
            "{\"action_type\": \"navigate\"|\"click\"|\"fill\"|\"extract\"|\"done\", "
            "\"observation_id\": str|null, \"target_element_id\": str|null, "
            "\"value\": str|null, \"url\": str|null, \"reasoning\": str}."
        )

        user_content = json.dumps({
            "objective": objective,
            "page_state": page_state,
        }, ensure_ascii=False)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model_name,
                        "temperature": 0.0,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                    },
                )
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"]
                # Parsear contenido JSON
                raw_json = content.strip()
                if raw_json.startswith("```"):
                    lines = raw_json.split("\n")
                    raw_json = "\n".join(lines[1:-1])

                parsed = json.loads(raw_json)
                action = ProposedAction.model_validate(parsed)

                if self.budget_manager is not None and res_id is not None:
                    # Estimar gasto según uso
                    usage = data.get("usage", {})
                    tokens = usage.get("total_tokens", 0)
                    actual_cost = Decimal(str(tokens)) * Decimal("0.000002")
                    self.budget_manager.confirm(res_id, actual_spend=actual_cost, tokens_used=tokens)

                return action

        except Exception as exc:
            if self.budget_manager is not None and res_id is not None:
                self.budget_manager.release(res_id)
            raise ModelOutputError(f"Fallo al consultar OpenRouter: {exc}") from exc
