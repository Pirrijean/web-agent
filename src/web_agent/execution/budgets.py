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
    """Gestor contable estricto para prevenir sobrecostes (documento maestro §8.13).

    Las cifras son estimaciones preventivas, no facturación real del proveedor.
    El coste reportado por el proveedor, si existe, se registra por separado.
    """

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
        self.provider_reported_spend_usd = Decimal("0.00")
        self.reservations: dict[str, Decimal] = {}

        if self.storage_path and self.storage_path.exists():
            self._load_state()

    @property
    def remaining_budget_usd(self) -> Decimal:
        return max(Decimal("0.00"), self.limit_usd - (self.confirmed_spend_usd + self.reserved_spend_usd))

    @property
    def exhausted(self) -> bool:
        return (
            self.confirmed_spend_usd >= self.limit_usd
            or self.calls_completed >= self.max_calls
            or self.tokens_consumed >= self.max_output_tokens
        )

    def can_afford(self, estimated_amount: Decimal | str | float) -> bool:
        amount = Decimal(str(estimated_amount))
        if amount < Decimal("0.00"):
            return False
        if self.confirmed_spend_usd + self.reserved_spend_usd + amount > self.limit_usd:
            return False
        if self.calls_completed + len(self.reservations) >= self.max_calls:
            return False
        if self.tokens_consumed >= self.max_output_tokens:
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
        provider_reported: Decimal | None = None,
    ) -> None:
        """Confirma el gasto estimado tras recibir (o perder) la respuesta.

        Si el gasto supera la reserva se registra igualmente (contabilidad
        conservadora) y se lanza ``BudgetExceededError`` solo si se supera el
        límite global, lo que agota el presupuesto para llamadas posteriores.
        """
        if reservation_id not in self.reservations:
            raise KeyError(f"Reserva inexistente o ya finalizada: {reservation_id}")

        try:
            spent = Decimal(str(actual_spend))
        except Exception as exc:
            raise ValueError("Gasto monetario inválido.") from exc
        if not spent.is_finite() or spent < 0:
            raise ValueError("El gasto debe ser finito y no negativo.")

        reserved_amt = self.reservations.pop(reservation_id)
        self.reserved_spend_usd = max(Decimal("0.00"), self.reserved_spend_usd - reserved_amt)
        self.confirmed_spend_usd += spent
        self.calls_completed += 1
        self.tokens_consumed += max(0, int(tokens_used))
        if provider_reported is not None:
            try:
                self.provider_reported_spend_usd += Decimal(str(provider_reported))
            except Exception:
                pass
        self._persist_state()
        if self.confirmed_spend_usd > self.limit_usd:
            raise BudgetExceededError(
                f"El gasto confirmado ({self.confirmed_spend_usd}) supera el límite ({self.limit_usd})."
            )

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
        # Mantener el límite fijado por la política/operador; nunca aceptar que un archivo lo aumente
        self.confirmed_spend_usd = Decimal(state.confirmed_spend_usd)
        self.reserved_spend_usd = Decimal(state.reserved_spend_usd)
        # Un archivo nunca puede aumentar los máximos del operador.
        self.max_calls = min(self.max_calls, state.max_calls)
        self.calls_completed = state.calls_completed
        self.max_output_tokens = min(self.max_output_tokens, state.max_output_tokens)
        self.tokens_consumed = state.tokens_consumed
        self.reservations = {k: Decimal(v) for k, v in state.reservations.items()}
