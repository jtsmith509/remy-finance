"""Paper fill engine: turns registry recommendations into journal events (R1.2-R1.3, DR-0029).

The engine never sees anything but stored boards. At each step it reads one decision
board (for decisions) and, where something trades, that slot's execution board, which is
taken 5 minutes later (DR-0003). Everything it concludes is appended to the registry
journal with the record that justifies it, in an order the registry's lifecycle allows.

Per trade:

1. ``submit``: ``ORDER_SUBMITTED`` at the recommendation's receipt time.
2. ``fill_entry``: FILL-1 against the execution board. A fill writes one ``LEG_FILLED``
   per leg (their cash sums exactly to the order's net cash). No fill, or no execution
   board at all (a gap), writes ``ORDER_UNFILLED`` then ``TRADE_CLOSED``. Single attempt.
3. ``on_snapshot`` at every later decision snapshot while the position is open:
   - If a short leg was assumed assigned at an earlier snapshot, or the exit policy wants
     out: ``EXIT_SUBMITTED`` and then ``EXIT_FILLED`` or ``EXIT_UNFILLED`` at this slot's
     execution quote. Stock from an assignment always closes (DR-0009), owing any
     dividend that went ex while it was short; the remaining option legs close
     all-or-none and retry at every later snapshot until expiration.
   - Otherwise ASSIGN-1 on each open short leg, using the decision board only.
     An assigned leg writes ``ASSIGNMENT_ASSUMED`` (strike cash now; the stock closes at
     the next snapshot). ASSIGN-1 is not applied at the expiration day's close
     snapshot: what happens after it is EXP-1's job.
4. ``settle``: EXP-1 on whatever is still open, at the official close, then
   ``TRADE_CLOSED`` with the realized-versus-predicted summary.

Re-running a step for a board already processed writes nothing, so the scheduler can be
re-run safely after a crash.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from pydantic import Field

from remy_finance.core import (
    FEES_1,
    FeeSchedule,
    Leg,
    MarketState,
    OfficialClose,
    OptionQuote,
    Record,
    Session,
    Side,
    UnderlyingQuote,
)
from remy_finance.core.exact import exact_decimal
from remy_finance.core.types import Dec, NonNegDec
from remy_finance.data.timeutil import NEW_YORK, et_date, next_session
from remy_finance.execution.assignment import (
    ASSIGN_1,
    AssignmentAssessment,
    AssignmentConsequence,
    AssignmentRule,
    assess_short_leg,
    assignment_consequence,
)
from remy_finance.execution.exit_policy import EXIT_POLICIES, ExitPolicy
from remy_finance.execution.fill import (
    FILL_1,
    FillRule,
    LegFill,
    OrderFill,
    StockFill,
    TradeSide,
    close_stock,
    fill_order,
)
from remy_finance.execution.settlement import (
    EXP_1,
    ExpirationRule,
    ExpirationSettlement,
    settle_at_expiration,
)
from remy_finance.registry import EventKind, JournalEvent, Recommendation, Registry
from remy_finance.snapshots.schedule import PROSPECTIVE_MAX_DELAY

E = EventKind
ZERO = Decimal(0)
RETURN_PLACES = Decimal("1E-12")  # reported returns are ratios, not cash: rounded here


# ---------------------------------------------------------------------- evidence records


class AssignmentRecord(Record):
    """Evidence for ``ASSIGNMENT_ASSUMED``: the ASSIGN-1 test and its consequence."""

    assessment: AssignmentAssessment
    consequence: AssignmentConsequence


class ExitResult(Record):
    """Evidence for ``EXIT_FILLED``/``EXIT_UNFILLED``.

    ``options`` is the all-or-none closing order for the remaining legs (None if none
    remain). ``stock`` closes assignment stock (always fillable, DR-0009) and is None if
    there was none, or no underlying quote to close it at. ``dividend_owed`` is owed on
    short stock for dividends that went ex while it was held.
    """

    forced: bool  # True: after an assumed assignment; False: the exit policy's choice
    exit_policy_id: str
    options: OrderFill | None
    stock: StockFill | None
    dividend_owed: NonNegDec
    note: str = ""


class SettlementResult(Record):
    """Evidence for ``EXPIRATION_SETTLED``: EXP-1 on the open legs, plus any stock left
    from an assignment, closed at the official close (EXP-1's convention)."""

    official_close: OfficialClose
    settlement: ExpirationSettlement | None
    stock: StockFill | None
    dividend_owed: NonNegDec


class TradeSummary(Record):
    """Evidence for ``TRADE_CLOSED``: the realized outcome against the forecast."""

    entry_filled: bool
    realized_pnl: Dec  # $: sum of every event's cash
    capital_at_risk: NonNegDec
    realized_return: Dec | None  # realized_pnl / capital_at_risk (None if unfilled)
    predicted_mean_return: Dec
    deviation: Dec | None  # realized_return - predicted_mean_return
    assignments: int = Field(ge=0)
    exit_attempts: int = Field(ge=0)


# ---------------------------------------------------------------------- position


@dataclass(frozen=True)
class Position:
    """A trade's state, rebuilt from its journal (the journal is the only state)."""

    rec: Recommendation
    submitted: bool
    entry_filled: bool
    entry_unfilled: bool
    open_legs: tuple[Leg, ...]
    stock_shares: int
    assigned_at: datetime | None  # the first assumed assignment (forces closing)
    exit_pending_since: datetime | None
    closed: bool
    last_time: datetime | None
    cash: Decimal
    assignments: int
    exit_attempts: int

    @property
    def is_open(self) -> bool:
        return self.entry_filled and not self.closed and bool(self.open_legs or self.stock_shares)

    @property
    def is_flat(self) -> bool:
        return self.entry_unfilled or (
            self.entry_filled and not self.open_legs and self.stock_shares == 0
        )

    @property
    def quantity(self) -> int:
        return self.rec.candidate.quantity


def position_of(rec: Recommendation, events: Sequence[JournalEvent]) -> Position:
    legs: tuple[Leg, ...] = ()
    stock = 0
    submitted = filled = unfilled = closed = False
    assigned_at: datetime | None = None
    pending: datetime | None = None
    last: datetime | None = None
    cash = ZERO
    n_assign = n_exit = 0
    for ev in events:
        with exact_decimal(cash, ev.cash):
            cash += ev.cash
        if ev.kind in (E.ANNOTATION, E.CORRECTION):
            continue
        last = ev.event_time if last is None else max(last, ev.event_time)
        match ev.kind:
            case E.ORDER_SUBMITTED:
                submitted = True
            case E.LEG_FILLED:
                filled, legs = True, rec.candidate.legs
            case E.ORDER_UNFILLED:
                unfilled = True
            case E.ASSIGNMENT_ASSUMED:
                a = AssignmentRecord.from_canonical_json(ev.evidence or "")
                legs = tuple(
                    lg for lg in legs if lg.contract.osi_symbol != a.consequence.osi_symbol
                )
                stock += a.consequence.stock_shares
                assigned_at = assigned_at or ev.event_time
                n_assign += 1
            case E.EXIT_SUBMITTED:
                pending = ev.event_time
                n_exit += 1
            case E.EXIT_FILLED | E.EXIT_UNFILLED:
                pending = None
                r = ExitResult.from_canonical_json(ev.evidence or "")
                if r.stock is not None:
                    stock += r.stock.shares
                if r.options is not None and r.options.filled:
                    legs = ()
            case E.EXPIRATION_SETTLED:
                s = SettlementResult.from_canonical_json(ev.evidence or "")
                legs = ()
                if s.stock is not None:
                    stock += s.stock.shares
            case E.TRADE_CLOSED:
                closed = True
    return Position(
        rec=rec,
        submitted=submitted,
        entry_filled=filled,
        entry_unfilled=unfilled,
        open_legs=legs,
        stock_shares=stock,
        assigned_at=assigned_at,
        exit_pending_since=pending,
        closed=closed,
        last_time=last,
        cash=cash,
        assignments=n_assign,
        exit_attempts=n_exit,
    )


# ---------------------------------------------------------------------- engine


class EngineError(ValueError):
    """The engine was handed the wrong board or called out of order."""


@dataclass(frozen=True)
class PaperEngine:
    registry: Registry
    fill_rule: FillRule = FILL_1
    fees: FeeSchedule = FEES_1
    assignment_rule: AssignmentRule = ASSIGN_1
    expiration_rule: ExpirationRule = EXP_1
    exit_policies: Mapping[str, ExitPolicy] = EXIT_POLICIES

    # ------------------------------------------------------------------ helpers

    def position(self, trade_id: str) -> Position:
        rec = self.registry.recommendation(trade_id)
        if rec is None:
            raise EngineError(f"unknown trade {trade_id}")
        return position_of(rec, [e for _, e in self.registry.events(trade_id)])

    def _append(
        self, rec: Recommendation, kind: EventKind, at: datetime, now: datetime, **kw: object
    ) -> JournalEvent:
        evidence = kw.pop("evidence", None)
        fields: dict[str, object] = {
            "trade_id": rec.trade_id,
            "mode": rec.mode,
            "kind": kind,
            "event_time": at,
            "recorded_at": now,
            **kw,
        }
        if isinstance(evidence, Record):
            ev = JournalEvent.with_evidence(evidence, **fields)
        else:
            ev = JournalEvent.model_validate(fields)
        self.registry.add_event(ev)
        return ev

    def _policy(self, rec: Recommendation) -> ExitPolicy:
        try:
            return self.exit_policies[rec.exit_policy_id]
        except KeyError as e:
            raise EngineError(f"no exit policy {rec.exit_policy_id!r}") from e

    def _check_board(self, rec: Recommendation, board: MarketState, what: str) -> None:
        if board.mode is not rec.mode:
            raise EngineError(f"{what} board is {board.mode.value}, trade is {rec.mode.value}")
        if rec.candidate.legs[0].contract.underlying not in board.underlyings:
            raise EngineError(f"{what} board does not cover {self._underlying(rec)}")

    @staticmethod
    def _underlying(rec: Recommendation) -> str:
        return rec.candidate.legs[0].contract.underlying

    def _execution_window(self, decision_time: datetime) -> tuple[datetime, datetime]:
        start = decision_time + timedelta(seconds=self.fill_rule.execution_latency_seconds)
        return start, start + PROSPECTIVE_MAX_DELAY

    def _check_execution(
        self, rec: Recommendation, decision_time: datetime, board: MarketState | None
    ) -> None:
        if board is None:
            return
        self._check_board(rec, board, "execution")
        start, end = self._execution_window(decision_time)
        if not start <= board.as_of <= end:
            raise EngineError(
                f"execution board at {board.as_of} is outside [{start}, {end}] for the "
                f"decision at {decision_time}"
            )

    @staticmethod
    def _option_book(board: MarketState | None) -> dict[str, OptionQuote]:
        return {} if board is None else {q.contract.osi_symbol: q for q in board.option_quotes}

    @staticmethod
    def _underlying_quote(board: MarketState | None, symbol: str) -> UnderlyingQuote | None:
        if board is None:
            return None
        return next((u for u in board.underlying_quotes if u.symbol == symbol), None)

    def _close_trade(self, pos: Position, now: datetime) -> JournalEvent:
        rec = pos.rec
        car = rec.capital_at_risk
        realized: Decimal | None = None
        deviation: Decimal | None = None
        if pos.entry_filled and car > 0:
            with localcontext() as ctx:
                ctx.prec = 34
                ctx.rounding = ROUND_HALF_EVEN
                realized = (pos.cash / car).quantize(RETURN_PLACES)
                deviation = realized - rec.predicted_return.mean
        summary = TradeSummary(
            entry_filled=pos.entry_filled,
            realized_pnl=pos.cash,
            capital_at_risk=car,
            realized_return=realized,
            predicted_mean_return=rec.predicted_return.mean,
            deviation=deviation,
            assignments=pos.assignments,
            exit_attempts=pos.exit_attempts,
        )
        at = pos.last_time or rec.receipt_time
        return self._append(rec, E.TRADE_CLOSED, at, now, evidence=summary)

    # ------------------------------------------------------------------ entry

    def submit(self, trade_id: str, now: datetime) -> list[JournalEvent]:
        pos = self.position(trade_id)
        if pos.submitted:
            return []
        rec = pos.rec
        note = f"{rec.fill_rule_version}: all-or-none at the execution quote; single attempt"
        return [self._append(rec, E.ORDER_SUBMITTED, rec.receipt_time, now, note=note)]

    def fill_entry(
        self, trade_id: str, execution: MarketState | None, now: datetime
    ) -> list[JournalEvent]:
        """Fill the entry at the execution board, or record it unfilled. ``execution`` None
        means the execution snapshot is a gap: the order is unfilled (no quote)."""
        pos = self.position(trade_id)
        rec = pos.rec
        if not pos.submitted:
            raise EngineError("submit the order before filling it")
        if pos.entry_filled or pos.entry_unfilled:
            return []
        self._check_execution(rec, rec.decision_time, execution)
        legs = rec.candidate.legs
        fill = fill_order(
            legs,
            pos.quantity,
            self._option_book(execution),
            opening=True,
            fees=self.fees,
            rule=self.fill_rule,
        )
        at = (
            execution.as_of
            if execution is not None
            else self._execution_window(rec.decision_time)[0]
        )
        at = max(at, rec.receipt_time)
        if not fill.filled:
            note = "no execution snapshot (gap)" if execution is None else _unfilled_note(fill)
            out = [self._append(rec, E.ORDER_UNFILLED, at, now, evidence=fill, note=note)]
            out.append(self._close_trade(self.position(trade_id), now))
            return out
        assert execution is not None  # a fill needs quotes
        out = []
        total = ZERO
        for leg, lf in zip(legs, fill.legs, strict=True):
            cash = _leg_cash(leg, lf, self.fees)
            with exact_decimal(total, cash):
                total += cash
            out.append(
                self._append(
                    rec,
                    E.LEG_FILLED,
                    at,
                    now,
                    evidence=lf,
                    cash=cash,
                    snapshot_id=execution.snapshot_id,
                )
            )
        if total != fill.net_cash:  # pragma: no cover - guards the per-leg fee split
            raise EngineError(f"leg cash {total} != order net cash {fill.net_cash}")
        return out

    # ------------------------------------------------------------------ while open

    def on_snapshot(
        self,
        trade_id: str,
        decision: MarketState,
        execution: MarketState | None,
        now: datetime,
    ) -> list[JournalEvent]:
        """Process one decision snapshot (and its execution board, None if a gap)."""
        pos = self.position(trade_id)
        rec = pos.rec
        if not pos.is_open:
            return []
        self._check_board(rec, decision, "decision")
        if et_date(decision.as_of) > rec.expiration:
            raise EngineError("the position is past expiration: settle it")
        if pos.exit_pending_since is not None:
            if pos.exit_pending_since != decision.as_of:  # pragma: no cover - lifecycle guard
                raise EngineError("an exit from an earlier snapshot is still pending")
            return self._finish_exit(pos, decision, execution, now)
        if pos.last_time is not None and decision.as_of <= pos.last_time:
            return []  # this snapshot (or a later one) was already processed
        if decision.as_of <= rec.decision_time:
            raise EngineError("a position is managed from the snapshot after its decision")

        forced = pos.assigned_at is not None
        if forced or self._policy(rec).wants_exit(pos, decision):
            note = "forced close after assumed assignment" if forced else "exit policy"
            submitted = self._append(rec, E.EXIT_SUBMITTED, decision.as_of, now, note=note)
            return [
                submitted,
                *self._finish_exit(self.position(trade_id), decision, execution, now),
            ]
        return self._assess_assignments(pos, decision, now)

    def _finish_exit(
        self,
        pos: Position,
        decision: MarketState,
        execution: MarketState | None,
        now: datetime,
    ) -> list[JournalEvent]:
        rec = pos.rec
        self._check_execution(rec, decision.as_of, execution)
        symbol = self._underlying(rec)
        options = (
            fill_order(
                pos.open_legs,
                pos.quantity,
                self._option_book(execution),
                opening=False,
                fees=self.fees,
                rule=self.fill_rule,
            )
            if pos.open_legs
            else None
        )
        stock: StockFill | None = None
        owed = ZERO
        uq = self._underlying_quote(execution, symbol)
        if pos.stock_shares and uq is not None and uq.bid > 0 and uq.ask > 0:
            stock = close_stock(pos.stock_shares, uq, fees=self.fees)
            if pos.stock_shares < 0 and pos.assigned_at is not None and execution is not None:
                owed = _dividends_owed(
                    execution, symbol, pos.assigned_at, execution.as_of, -pos.stock_shares
                )
        filled = (options is None or options.filled) and (
            pos.stock_shares == 0 or stock is not None
        )
        parts: list[Decimal] = []
        if options is not None and options.filled:
            parts.append(options.net_cash)
        if stock is not None:
            parts += [stock.cash, -stock.fees, -owed]
        with exact_decimal(ZERO, *parts):
            cash = sum(parts, ZERO)
        notes = []
        if execution is None:
            notes.append("no execution snapshot (gap)")
        if options is not None and not options.filled:
            notes.append(_unfilled_note(options))
        if pos.stock_shares and stock is None:
            notes.append("no usable underlying quote to close stock")
        result = ExitResult(
            forced=pos.assigned_at is not None,
            exit_policy_id=rec.exit_policy_id,
            options=options,
            stock=stock,
            dividend_owed=owed,
            note="; ".join(notes),
        )
        at = execution.as_of if execution is not None else self._execution_window(decision.as_of)[0]
        kind = E.EXIT_FILLED if filled else E.EXIT_UNFILLED
        out = [
            self._append(
                rec,
                kind,
                at,
                now,
                evidence=result,
                cash=cash,
                note=result.note,
                snapshot_id=execution.snapshot_id if execution else None,
            )
        ]
        after = self.position(rec.trade_id)
        if after.is_flat and kind is E.EXIT_FILLED:
            out.append(self._close_trade(after, now))
        return out

    def _assess_assignments(
        self, pos: Position, decision: MarketState, now: datetime
    ) -> list[JournalEvent]:
        rec = pos.rec
        day = et_date(decision.as_of)
        if day == rec.expiration and decision.session is Session.CLOSE:
            return []  # EXP-1 governs from here
        shorts = [lg for lg in pos.open_legs if lg.side is Side.SHORT]
        if not shorts:
            return []
        symbol = self._underlying(rec)
        uq = self._underlying_quote(decision, symbol)
        if uq is None or uq.bid <= 0 or uq.ask <= 0:
            note = "ASSIGN-1 not assessed: no two-sided underlying quote on the decision board"
            return [self._append(rec, E.ANNOTATION, decision.as_of, now, note=note)]
        with exact_decimal(uq.bid, uq.ask):
            mid = (uq.bid + uq.ask) / 2
        book = self._option_book(decision)
        out: list[JournalEvent] = []
        for leg in shorts:
            q = book.get(leg.contract.osi_symbol)
            assessment = assess_short_leg(
                leg,
                option_bid=None if q is None else q.bid,
                underlying_price=mid,
                as_of=decision.as_of,
                next_session=next_session(day),
                known_dividends=[d for d in decision.dividends if d.symbol == symbol],
                rule=self.assignment_rule,
            )
            if not assessment.assigned:
                continue
            consequence = assignment_consequence(
                leg,
                leg.ratio * pos.quantity,
                assignment_fee_per_contract=self.fees.exercise_assignment_per_contract,
            )
            with exact_decimal(consequence.strike_cash, consequence.assignment_fees):
                cash = consequence.strike_cash - consequence.assignment_fees
            out.append(
                self._append(
                    rec,
                    E.ASSIGNMENT_ASSUMED,
                    decision.as_of,
                    now,
                    evidence=AssignmentRecord(assessment=assessment, consequence=consequence),
                    cash=cash,
                    snapshot_id=decision.snapshot_id,
                    note=f"{assessment.reason.value if assessment.reason else ''}",
                )
            )
        return out

    # ------------------------------------------------------------------ expiration

    def settle(self, trade_id: str, close: OfficialClose, now: datetime) -> list[JournalEvent]:
        """EXP-1 on whatever is still open, then close the trade."""
        pos = self.position(trade_id)
        rec = pos.rec
        if not pos.is_open:
            return []
        symbol = self._underlying(rec)
        if close.symbol != symbol or close.session_date != rec.expiration:
            raise EngineError(
                f"need {symbol}'s official close on {rec.expiration}, got "
                f"{close.symbol} on {close.session_date}"
            )
        if pos.exit_pending_since is not None:
            raise EngineError("an exit is still pending")
        settlement = (
            settle_at_expiration(
                pos.open_legs,
                pos.quantity,
                expiration=rec.expiration,
                official_close=close.price,
                fees=self.fees,
                rule=self.expiration_rule,
            )
            if pos.open_legs
            else None
        )
        stock: StockFill | None = None
        if pos.stock_shares:
            shares = -pos.stock_shares
            with exact_decimal(close.price, self.fees.stock_per_share):
                stock = StockFill(
                    symbol=symbol,
                    shares=shares,
                    price=close.price,
                    cash=-shares * close.price,
                    fees=self.fees.stock_per_share * abs(shares),
                )
        result = SettlementResult(
            official_close=close, settlement=settlement, stock=stock, dividend_owed=ZERO
        )
        cash = ZERO
        if settlement is not None:
            with exact_decimal(settlement.settlement_value, settlement.exercise_assignment_fees):
                cash = settlement.settlement_value - settlement.exercise_assignment_fees
        if stock is not None:
            with exact_decimal(cash, stock.cash, stock.fees):
                cash += stock.cash - stock.fees
        flags = ",".join(settlement.flags) if settlement is not None else ""
        out = [
            self._append(
                rec,
                E.EXPIRATION_SETTLED,
                close.event_time,
                now,
                evidence=result,
                cash=cash,
                public_time=close.public_time,
                note=flags,
            )
        ]
        out.append(self._close_trade(self.position(trade_id), now))
        return out


# ---------------------------------------------------------------------- pure helpers


def _leg_cash(leg: Leg, lf: LegFill, fees: FeeSchedule) -> Decimal:
    assert lf.price is not None
    sign = -1 if lf.trade_side is TradeSide.BUY else 1
    with exact_decimal(lf.price, fees.option_per_contract_per_side):
        return sign * lf.price * lf.contracts * leg.contract.multiplier - fees.option_fees(
            lf.contracts
        )


def _unfilled_note(fill: OrderFill) -> str:
    bad = [f"{lf.osi_symbol.replace(' ', '')}:{lf.reason}" for lf in fill.legs if not lf.filled]
    return "unfilled: " + ", ".join(bad)


def _dividends_owed(
    board: MarketState, symbol: str, since: datetime, until: datetime, short_shares: int
) -> Decimal:
    """Cash dividends going ex after the assignment (``since``) and on or before the stock
    is closed (``until``), as known on ``board``. Ex-dates count from the session after
    the assignment snapshot: assignment is assumed overnight, before that session opens."""
    first, last = et_date(since), et_date(until)
    total = ZERO
    for d in board.dividends:
        if d.symbol == symbol and first < d.ex_date <= last:
            with exact_decimal(total, d.amount):
                total += d.amount * short_shares
    return total


def official_close_time(d: date, hhmm: tuple[int, int] = (16, 0)) -> datetime:
    return datetime.combine(d, time(*hhmm), tzinfo=NEW_YORK).astimezone(UTC)


__all__ = [
    "AssignmentRecord",
    "EngineError",
    "ExitResult",
    "PaperEngine",
    "Position",
    "SettlementResult",
    "TradeSummary",
    "official_close_time",
    "position_of",
]
