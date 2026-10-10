import json
import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class BudgetExceededError(Exception):
    """El gasto o las llamadas solicitadas superan el límite presupuestario autorizado."""


class BudgetState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit_usd: str
    confirmed_spend_usd: str
    reserved_spend_usd: str
    max_calls: int
    calls_completed: int
    max_output_tokens: int
    tokens_consumed: int
    reservations: Dict[str, str] = Field(default_factory=dict)


class BudgetManager:
    """Gestor contable estricto para prevenir sobrecostes (Fase Presupuestaria P0)."""

    def __init__(
        self,
        limit_usd: Decimal | str | float = Decimal("5.00"),
        max_calls: int = 100,
        max_output_tokens: int = 100_000,
        storage_path: Optional[Path | str] = None,
    ):
        self.limit_usd = Decimal(str(limit_usd))
        self.max_calls = int(max_calls)
        self.max_output_tokens = int(max_output_tokens)
        self.storage_path = Path(storage_path).resolve() if storage_path else None

        self.confirmed_spend_usd = Decimal("0.00")
        self.reserved_spend_usd = Decimal("0.00")
        self.calls_completed = 0
        self.tokens_consumed = 0
        self.reservations: dict[str, Decimal] = {}

        if self.storage_path and self.storage_path.exists():
            self._load_state()

    @property
    def remaining_budget_usd(self) -> Decimal:
        return max(Decimal("0.00"), self.limit_usd - (self.confirmed_spend_usd + self.reserved_spend_usd))

    def can_afford(self, estimated_amount: Decimal | str | float) -> bool:
        amount = Decimal(str(estimated_amount))
        if amount < Decimal("0.00"):
            return False
        if self.confirmed_spend_usd + self.reserved_spend_usd + amount > self.limit_usd:
            return False
        if self.calls_completed + len(self.reservations) >= self.max_calls:
            return False
        return True

    def reserve(self, estimated_amount: Decimal | str | float) -> str:
        """Reserva saldo antes de emitir una consulta externa."""
        amount = Decimal(str(estimated_amount))
        if not self.can_afford(amount):
            raise BudgetExceededError(
                f"Presupuesto insuficiente: Límite=${self.limit_usd}, "
                f"Gastado=${self.confirmed_spend_usd}, Reservado=${self.reserved_spend_usd}, "
                f"Solicitado=${amount}"
            )
        reservation_id = f"res_{uuid.uuid4().hex[:12]}"
        self.reservations[reservation_id] = amount
        self.reserved_spend_usd += amount
        self._persist_state()
        return reservation_id

    def confirm(
        self,
        reservation_id: str,
        actual_spend: Decimal | str | float,
        tokens_used: int = 0,
    ) -> None:
        """Confirma el gasto real facturado tras recibir la respuesta."""
        if reservation_id not in self.reservations:
            raise KeyError(f"Reserva inexistente o ya finalizada: {reservation_id}")

        reserved_amt = self.reservations.pop(reservation_id)
        self.reserved_spend_usd = max(Decimal("0.00"), self.reserved_spend_usd - reserved_amt)

        spent = Decimal(str(actual_spend))
        self.confirmed_spend_usd += spent
        self.calls_completed += 1
        self.tokens_consumed += max(0, tokens_used)
        self._persist_state()

    def release(self, reservation_id: str) -> None:
        """Cancela una reserva si la llamada falló antes de emitirse o no se facturó."""
        if reservation_id in self.reservations:
            reserved_amt = self.reservations.pop(reservation_id)
            self.reserved_spend_usd = max(Decimal("0.00"), self.reserved_spend_usd - reserved_amt)
            self._persist_state()

    def _persist_state(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        state = BudgetState(
            limit_usd=str(self.limit_usd),
            confirmed_spend_usd=str(self.confirmed_spend_usd),
            reserved_spend_usd=str(self.reserved_spend_usd),
            max_calls=self.max_calls,
            calls_completed=self.calls_completed,
            max_output_tokens=self.max_output_tokens,
            tokens_consumed=self.tokens_consumed,
            reservations={k: str(v) for k, v in self.reservations.items()},
        )
        content = state.model_dump_json(indent=2)
        fd, tmp_path_str = tempfile.mkstemp(dir=self.storage_path.parent, prefix="budget_tmp_")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.storage_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

    def _load_state(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        content = self.storage_path.read_text(encoding="utf-8")
        state = BudgetState.model_validate_json(content)
        self.limit_usd = Decimal(state.limit_usd)
        self.confirmed_spend_usd = Decimal(state.confirmed_spend_usd)
        self.reserved_spend_usd = Decimal(state.reserved_spend_usd)
        self.max_calls = state.max_calls
        self.calls_completed = state.calls_completed
        self.max_output_tokens = state.max_output_tokens
        self.tokens_consumed = state.tokens_consumed
        self.reservations = {k: Decimal(v) for k, v in state.reservations.items()}
