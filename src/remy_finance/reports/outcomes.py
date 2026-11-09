"""Per-trade outcomes rebuilt from the registry journal (R1.7, EVAL-1 §1).

The journal is the only source. Realized P&L is the sum of every event's cash, including
``CORRECTION`` cash (EVAL-1 §1.6: the primary analysis uses corrected values);
``realized_pnl_original`` leaves corrections out, for the sensitivity analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum

from remy_finance.core.exact import exact_decimal
from remy_finance.data.timeutil import et_date
from remy_finance.execution.engine import ExitResult, SettlementResult
from remy_finance.execution.fill import OrderFill
from remy_finance.registry import EventKind, JournalEvent, Recommendation, Registry

E = EventKind
ZERO = Decimal(0)
RETURN_PLACES = Decimal("1E-12")
EVAL1_CENSOR_DAYS = 61  # EVAL-1 §1.4


class Status(StrEnum):
    PENDING = "PENDING"  # submitted, entry not yet resolved
    OPEN = "OPEN"
    UNFILLED = "UNFILLED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class TradeOutcome:
    rec: Recommendation
    status: Status
    unfilled_reason: str | None
    filled_at: datetime | None
    closed_at: datetime | None
    realized_pnl: Decimal  # corrected
    realized_pnl_original: Decimal  # without CORRECTION cash
    corrections: int
    assignments: int
    forced_exits: int
    exit_retries: int
    pin_flags: int
    annotations: int

    # ------------------------------------------------------------------ derived

    @property
    def trade_id(self) -> str:
        return self.rec.trade_id

    @property
    def engine_group(self) -> str:
        return f"{self.rec.engine} v{self.rec.engine_version}"

    @property
    def underlying(self) -> str:
        return self.rec.candidate.legs[0].contract.underlying

    @property
    def filled(self) -> bool:
        return self.filled_at is not None

    @property
    def realized_return(self) -> Decimal | None:
        return _ratio(self.realized_pnl, self.rec.capital_at_risk) if self.closed_filled else None

    @property
    def realized_return_original(self) -> Decimal | None:
        if not self.closed_filled:
            return None
        return _ratio(self.realized_pnl_original, self.rec.capital_at_risk)

    @property
    def closed_filled(self) -> bool:
        return self.status is Status.CLOSED and self.filled

    @property
    def deviation(self) -> Decimal | None:
        r = self.realized_return
        return None if r is None else r - self.rec.predicted_return.mean

    def eval1_eligible(self, cutoff: date) -> bool:
        """EVAL-1 §1.4 censoring: decided at least 61 days before the cutoff."""
        return et_date(self.rec.decision_time) <= cutoff - timedelta(days=EVAL1_CENSOR_DAYS)


def _ratio(num: Decimal, den: Decimal) -> Decimal | None:
    if den <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.rounding = ROUND_HALF_EVEN
        return (num / den).quantize(RETURN_PLACES)


def _unfilled_reason(ev: JournalEvent) -> str:
    if ev.note and not ev.note.startswith("unfilled:"):
        return ev.note  # e.g. a gap, or recorded after the execution quote
    fill = OrderFill.from_canonical_json(ev.evidence or "")
    reasons = sorted({str(lf.reason) for lf in fill.legs if not lf.filled and lf.reason})
    return ",".join(reasons) or "UNFILLED"


def outcome_of(rec: Recommendation, events: list[JournalEvent]) -> TradeOutcome:
    status = Status.PENDING
    reason: str | None = None
    filled_at = closed_at = None
    pnl = original = ZERO
    corrections = assignments = forced = retries = pins = notes = 0
    for ev in events:
        with exact_decimal(pnl, original, ev.cash):
            pnl += ev.cash
            if ev.kind is not E.CORRECTION:
                original += ev.cash
        match ev.kind:
            case E.LEG_FILLED:
                filled_at = filled_at or ev.event_time
                status = Status.OPEN
            case E.ORDER_UNFILLED:
                status, reason = Status.UNFILLED, _unfilled_reason(ev)
            case E.ASSIGNMENT_ASSUMED:
                assignments += 1
            case E.EXIT_SUBMITTED:
                pass
            case E.EXIT_FILLED | E.EXIT_UNFILLED:
                r = ExitResult.from_canonical_json(ev.evidence or "")
                forced += r.forced and ev.kind is E.EXIT_FILLED
                retries += ev.kind is E.EXIT_UNFILLED
            case E.EXPIRATION_SETTLED:
                s = SettlementResult.from_canonical_json(ev.evidence or "")
                pins += len(s.settlement.flags) if s.settlement else 0
            case E.TRADE_CLOSED:
                closed_at = ev.event_time
                if status is not Status.UNFILLED:
                    status = Status.CLOSED
            case E.CORRECTION:
                corrections += 1
            case E.ANNOTATION:
                notes += 1
            case _:
                pass
    return TradeOutcome(
        rec=rec,
        status=status,
        unfilled_reason=reason,
        filled_at=filled_at,
        closed_at=closed_at,
        realized_pnl=pnl,
        realized_pnl_original=original,
        corrections=corrections,
        assignments=assignments,
        forced_exits=forced,
        exit_retries=retries,
        pin_flags=pins,
        annotations=notes,
    )


def outcomes(registry: Registry) -> list[TradeOutcome]:
    return [
        outcome_of(rec, [e for _, e in registry.events(rec.trade_id)])
        for rec in registry.recommendations()
    ]
