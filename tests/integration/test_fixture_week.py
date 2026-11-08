"""Fixture week (handoff Section 7): snapshot -> candidates -> fills -> assignment/settlement.

One underlying, XYZ, over Mon 2026-11-16 .. Fri 2026-11-20, with options expiring Friday and
a $0.80 dividend going ex on Thursday. Everything runs offline through the real code paths:

- replay boards built through PointInTimeView and stored write-once (decision + execution);
- the move generator on each Monday-open board;
- a *placeholder* selection step (R1's recommendation engines are not built yet) that picks
  two specific candidates by their legs, sized under SIZE-1;
- FILL-1 fills at the 10:05 execution board, not the 10:00 decision board;
- ASSIGN-1 at every later decision snapshot (the short 90 call is assumed assigned at
  Wednesday's open, the day before the ex-date) and a forced close at the next execution
  board (stock at the underlying quote, remaining legs under FILL-1);
- EXP-1 settlement on Friday for the position still open.

The ledger must reconcile to the cent, and two full runs must be byte-identical.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from remy_finance.core import (
    Candidate,
    DividendEvent,
    Leg,
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    Priceability,
    RatePoint,
    Record,
    Right,
    Session,
    Side,
    StructureType,
    UnderlyingQuote,
)
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, next_session
from remy_finance.execution.assignment import assess_short_leg, assignment_consequence
from remy_finance.execution.fill import close_stock, fill_order
from remy_finance.execution.settlement import settle_at_expiration
from remy_finance.movegen.config import MOVEGEN_1, SIZE_1
from remy_finance.movegen.generator import generate
from remy_finance.snapshots.builder import build_replay
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time
from remy_finance.snapshots.store import SnapshotStore

D = Decimal
SYM = "XYZ"
DAYS = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]  # Mon..Fri
EXPIRY = DAYS[-1]
EX_DATE = DAYS[3]  # Thursday
DIVIDEND = D("0.80")
STRIKES = [D(90) + D("2.5") * i for i in range(9)]  # 90 .. 110
# Underlying mid per day at the decision times; execution boards (+5 min) drift +$0.05.
SPOT = {
    DAYS[0]: D("100.00"),
    DAYS[1]: D("101.50"),
    DAYS[2]: D("99.80"),
    DAYS[3]: D("102.20"),
    DAYS[4]: D("103.00"),
}
OFFICIAL_CLOSE = D("103.00")
DRIFT = D("0.05")
CENT = D("0.01")
# The fixture week has 4-0 DTE options, so the test config allows short-dated expirations.
CONFIG = MOVEGEN_1.model_copy(update={"version": "MOVEGEN-FIXTURE-WEEK", "min_dte": 0})


# ------------------------------------------------------------------- fixture data


def _mid(right: Right, k: Decimal, s: Decimal, dte: int) -> Decimal:
    """A simple, deterministic option price: intrinsic plus a tent-shaped time value."""
    intrinsic = max(s - k, D(0)) if right is Right.CALL else max(k - s, D(0))
    moneyness = max(D(0), 1 - abs(k - s) / (D("0.15") * s))
    time_value = max(D("0.03"), D("0.25") * D(dte + 1).sqrt() * moneyness)
    return (intrinsic + time_value).quantize(CENT)


def build_fixture() -> FixtureSource:
    records: list[Record] = []
    declared = et_at(date(2026, 11, 2), 16)
    records.append(
        DividendEvent(
            symbol=SYM,
            ex_date=EX_DATE,
            amount=DIVIDEND,
            declaration_date=date(2026, 11, 2),
            event_time=declared,
            public_time=declared,
            source="fixture",
        )
    )
    rate_t = et_at(date(2026, 11, 13), 9, 30)
    records.append(
        RatePoint(
            symbol="TREASURY_3M",
            tenor="3M",
            observation_date=date(2026, 11, 12),
            rate=D("0.042"),
            event_time=rate_t,
            public_time=rate_t,
            source="fixture",
        )
    )
    for d in DAYS:
        dte = (EXPIRY - d).days
        for session in Session:
            for kind in SnapshotKind:
                at = snapshot_time(d, session, kind)
                s = SPOT[d] + (DRIFT if kind is SnapshotKind.EXECUTION else 0)
                stamp = at - timedelta(seconds=2)
                records.append(
                    UnderlyingQuote(
                        symbol=SYM,
                        bid=s - CENT,
                        ask=s + CENT,
                        bid_size=500,
                        ask_size=500,
                        quote_time=stamp,
                        source="fixture",
                    )
                )
                for k in STRIKES:
                    for right in Right:
                        m = _mid(right, k, s, dte)
                        records.append(
                            OptionQuote(
                                contract=OptionContract.build(SYM, EXPIRY, k, right),
                                bid=max(CENT, m - D("0.02")),
                                ask=m + D("0.02"),
                                bid_size=50,
                                ask_size=50,
                                quote_time=stamp,
                                source="fixture",
                            )
                        )
    return FixtureSource(records)


# ---------------------------------------------------------------------- pipeline


@dataclass
class Position:
    name: str
    candidate: Candidate
    quantity: int
    cash: Decimal = field(default_factory=Decimal)  # running net cash, $
    events: list[str] = field(default_factory=list)
    closed: bool = False


def _board(
    store: SnapshotStore,
    src: FixtureSource,
    d: date,
    session: Session,
    kind: SnapshotKind,
    decision_id: str | None = None,
) -> MarketState:
    state = build_replay(
        src, d, session, kind=kind, universe_version="UNIV-FIXTURE", underlyings=(SYM,)
    )
    store.write(state, kind=kind, decision_snapshot_id=decision_id)
    return state


def _quotes(state: MarketState) -> dict[str, OptionQuote]:
    return {q.contract.osi_symbol: q for q in state.option_quotes}


def _pick(
    cands: tuple[Candidate, ...], stype: StructureType, legs: list[tuple[Right, str, Side]]
) -> Candidate:
    """PLACEHOLDER for R1's recommendation engines: select one candidate by its exact legs."""
    want = [(r, D(k), s) for r, k, s in legs]
    for c in cands:
        got = [(lg.contract.right, lg.contract.strike, lg.side) for lg in c.legs]
        if c.structure_type is stype and got == want and c.priceability is Priceability.PRICED:
            return c
    raise AssertionError(f"move generator did not produce {stype} {legs}")


def run_week(root: Path) -> tuple[list[Position], list[str], SnapshotStore]:
    src = build_fixture()
    store = SnapshotStore(root / "snapshots")
    ledger: list[str] = []

    # Monday open: decide on the 10:00 board, fill on the 10:05 board.
    decision = _board(store, src, DAYS[0], Session.OPEN, SnapshotKind.DECISION)
    cands, summary = generate(decision, CONFIG)
    ledger.append(f"movegen {summary.n_candidates} candidates hash {summary.list_hash}")
    execution = _board(
        store, src, DAYS[0], Session.OPEN, SnapshotKind.EXECUTION, decision.snapshot_id
    )
    picks = {
        "debit": _pick(
            cands,
            StructureType.VERTICAL_DEBIT,
            [(Right.CALL, "100", Side.LONG), (Right.CALL, "105", Side.SHORT)],
        ),
        "credit": _pick(
            cands,
            StructureType.VERTICAL_CREDIT,
            [(Right.CALL, "90", Side.SHORT), (Right.CALL, "92.5", Side.LONG)],
        ),
    }
    positions: list[Position] = []
    for name, cand in picks.items():
        assert cand.structure is not None and cand.quantity == SIZE_1.quantity(cand.structure) > 0
        fill = fill_order(cand.legs, cand.quantity, _quotes(execution), opening=True)
        assert fill.filled, f"{name} did not fill"
        pos = Position(name, cand, cand.quantity, cash=fill.net_cash)
        pos.events.append(f"OPEN {fill.content_hash()[:16]} net {fill.net_cash}")
        positions.append(pos)
        ledger.append(
            f"{name} open qty {cand.quantity} net {fill.net_cash} fill {fill.content_hash()}"
        )

    pending_assignment: dict[str, Leg] = {}
    slots = [(d, s) for d in DAYS for s in Session][1:]  # every snapshot after Monday open
    for d, session in slots:
        board = _board(store, src, d, session, SnapshotKind.DECISION)
        exec_board = _board(store, src, d, session, SnapshotKind.EXECUTION, board.snapshot_id)
        # Forced closes for assignments assumed at the previous snapshot (DR-0009).
        for pos in positions:
            if pos.closed or pos.name not in pending_assignment:
                continue
            leg = pending_assignment.pop(pos.name)
            conseq = assignment_consequence(
                leg,
                pos.quantity * leg.ratio,
                dividend_per_share_before_close=DIVIDEND if d >= EX_DATE else D(0),
            )
            (uq,) = exec_board.underlying_quotes
            stock = close_stock(conseq.stock_shares, uq)
            remaining = tuple(lg for lg in pos.candidate.legs if lg is not leg)
            exit_fill = fill_order(remaining, pos.quantity, _quotes(exec_board), opening=False)
            assert exit_fill.filled
            pos.cash += conseq.strike_cash - conseq.assignment_fees - conseq.dividend_owed
            pos.cash += stock.cash - stock.fees + exit_fill.net_cash
            pos.closed = True
            pos.events.append(f"FORCED_CLOSE {d} {session.value}")
            ledger.append(
                f"{pos.name} forced close {d} {session.value} strike {conseq.strike_cash} "
                f"div {conseq.dividend_owed} stock {stock.cash} legs {exit_fill.net_cash}"
            )
        # ASSIGN-1 on every open short leg, using only what this board knows.
        (uq,) = board.underlying_quotes
        spot = (uq.bid + uq.ask) / 2
        quotes = _quotes(board)
        for pos in positions:
            if pos.closed or pos.name in pending_assignment:
                continue
            for leg in pos.candidate.legs:
                if leg.side is not Side.SHORT:
                    continue
                q = quotes.get(leg.contract.osi_symbol)
                a = assess_short_leg(
                    leg,
                    option_bid=q.bid if q else None,
                    underlying_price=spot,
                    as_of=board.as_of,
                    next_session=next_session(d),
                    known_dividends=board.dividends,
                )
                if a.assigned:
                    pending_assignment[pos.name] = leg
                    pos.events.append(
                        f"ASSIGNED {d} {session.value} {a.reason.value if a.reason else ''}"
                    )
                    ledger.append(
                        f"{pos.name} assignment assumed {d} {session.value} {a.content_hash()}"
                    )

    # Friday: EXP-1 settles whatever is still open at the official close.
    for pos in positions:
        if pos.closed:
            continue
        st = settle_at_expiration(
            pos.candidate.legs, pos.quantity, expiration=EXPIRY, official_close=OFFICIAL_CLOSE
        )
        pos.cash += st.settlement_value - st.exercise_assignment_fees
        pos.closed = True
        pos.events.append(f"SETTLED {st.settlement_value}")
        ledger.append(f"{pos.name} settled {st.content_hash()}")
    for pos in positions:
        ledger.append(f"{pos.name} realized {pos.cash}")
    return positions, ledger, store


# ------------------------------------------------------------------------- tests


def test_fixture_week_end_to_end(tmp_path: Path) -> None:
    positions, ledger, store = run_week(tmp_path)
    debit, credit = positions

    # Debit call spread 100/105 held to expiry at 103: settlement 300/unit, then reconcile.
    assert debit.events[-1].startswith("SETTLED") and "ASSIGNED" not in " ".join(debit.events)
    s = debit.candidate.structure
    assert s is not None
    assert debit.cash == 300 * debit.quantity + debit_open_cash(ledger)

    # Credit call spread with short 90C. ASSIGN-1's dividend rule fires when the *next trading
    # day* is the ex-date: at Wednesday's open the next session is Thursday (ex-date) and the
    # $0.80 dividend exceeds the call's extrinsic value, so assignment is assumed then. The
    # position is force-closed at the next snapshot (Wednesday close), before the ex-date, so
    # the short stock owes no dividend (DR-0009).
    assert "ASSIGNED 2026-11-18 OPEN DIVIDEND" in credit.events, credit.events
    assert "FORCED_CLOSE 2026-11-18 CLOSE" in credit.events
    forced = next(line for line in ledger if line.startswith("credit forced close"))
    assert " div 0 " in forced
    # Reconcile the credit spread: open credit + strike cash + stock buy-back + long-leg sale.
    assert credit.cash == credit_components(ledger)

    # Every stored board verifies (hashes + ids): 5 days x 2 sessions x 2 kinds.
    manifests = sorted(store.root.glob("*/*/*/manifest.json"))
    assert len(manifests) == 20
    for m in manifests:
        store.load(m.parent)
    assert all(p.parent.parent.parent.name == Mode.REPLAY.value.lower() for p in manifests)


def credit_components(ledger: list[str]) -> Decimal:
    opened = D(
        next(x for x in ledger if x.startswith("credit open")).split(" net ")[1].split(" ")[0]
    )
    f = next(x for x in ledger if x.startswith("credit forced close")).split()
    strike, div, stock, legs = (D(f[f.index(k) + 1]) for k in ("strike", "div", "stock", "legs"))
    return opened + strike - div + stock + legs


def debit_open_cash(ledger: list[str]) -> Decimal:
    line = next(x for x in ledger if x.startswith("debit open"))
    return D(line.split(" net ")[1].split(" ")[0])


def test_fixture_week_is_deterministic(tmp_path: Path) -> None:
    _, first, _ = run_week(tmp_path / "a")
    _, second, _ = run_week(tmp_path / "b")
    assert first == second
    assert (
        hashlib.sha256("\n".join(first).encode()).hexdigest()
        == hashlib.sha256("\n".join(second).encode()).hexdigest()
    )


def test_fills_use_execution_board_prices(tmp_path: Path) -> None:
    """The 10:05 board drifted +$0.05, so fill prices differ from the 10:00 assumptions."""
    positions, _, _ = run_week(tmp_path)
    debit = positions[0]
    s = debit.candidate.structure
    assert s is not None
    opened = D(debit.events[0].split(" net ")[1])
    assumed = -(s.net_premium + s.entry_fees) * debit.quantity
    assert opened != assumed  # filled at the execution quote, not the decision quote


def test_no_board_contains_data_after_its_as_of(tmp_path: Path) -> None:
    _, _, store = run_week(tmp_path)
    for m in sorted(store.root.glob("*/*/*/manifest.json")):
        board = store.load(m.parent)
        assert all(q.quote_time <= board.as_of for q in board.option_quotes)
        assert all(q.quote_time <= board.as_of for q in board.underlying_quotes)
        assert all(x.public_time <= board.as_of for x in (*board.dividends, *board.rates))
        assert board.as_of.tzinfo is UTC and isinstance(board.as_of, datetime)
