"""Deterministic move generator (handoff R0.7).

From a ``MarketState`` and a ``MovegenConfig`` it enumerates long calls and puts, vertical
debit and credit spreads, iron condors and long call/put butterflies, and (MOVEGEN-2, the
default, DR-0035) long straddles, strangles, long iron condors and long iron butterflies,
within the bounds, always plus the null move. A candidate with any leg lacking a two-sided
quote is kept as ``UNPRICEABLE`` with a reason, never silently dropped. Same board + same
config gives a byte-identical ordered candidate list and the same list hash.

Priced candidates use the FILL-1 price assumption at the decision snapshot (ask for long
legs, bid for short legs); actual fills use the execution quote later (DR-0003).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import Field

from remy_finance.core import (
    FEES_1,
    Candidate,
    FeeSchedule,
    Leg,
    MarketState,
    OptionQuote,
    Priceability,
    Record,
    Right,
    Side,
    Structure,
    StructureType,
    canonical,
)
from remy_finance.execution.fill import FILL_1, FillRule
from remy_finance.movegen.config import MOVEGEN_2, SIZE_1, MovegenConfig, SizingRule

NEW_YORK = ZoneInfo("America/New_York")
TWO = Decimal(2)

Shape = tuple[StructureType, tuple[tuple[Right, Decimal, Side, int], ...]]


class MovegenNote(Record):
    """Why something was not generated (never a silent drop)."""

    symbol: str
    code: str
    detail: str = ""


class MovegenSummary(Record):
    snapshot_id: str
    config_version: str
    config_hash: str
    sizing_version: str
    fill_rule_version: str
    fee_version: str
    n_candidates: int = Field(ge=1)
    n_priced: int = Field(ge=0)
    n_unpriceable: int = Field(ge=0)
    n_not_recommendable: int = Field(ge=0)  # priced but quantity 0 under SIZE-1
    list_hash: str
    notes: tuple[MovegenNote, ...] = ()


def list_hash(candidates: tuple[Candidate, ...]) -> str:
    """SHA-256 over the ordered candidate content hashes. One canonical memo is shared, so
    contracts and legs reused across candidates are serialized once (same bytes, faster).
    Each candidate keeps its hash, so later uses (sampling keys, trade IDs, run logs) don't
    serialize it again."""
    h = hashlib.sha256()
    memo: canonical.Memo = {}
    for c in candidates:
        ch = c.__dict__.get("_remy_content_hash")
        if ch is None:
            ch = canonical.content_hash(c, memo)
            c._prime_content_hash(ch)
        h.update(ch.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _on_lattice(k: Decimal, step: Decimal) -> bool:
    q = k / step
    return q == q.to_integral_value()


def _shapes(
    calls: set[Decimal],
    puts: set[Decimal],
    spot: Decimal,
    widths: tuple[Decimal, ...],
    cfg: MovegenConfig,
) -> Iterator[Shape]:
    """All structure shapes on the lattice strikes of one expiration (unordered)."""
    L, S = Side.LONG, Side.SHORT
    C, P = Right.CALL, Right.PUT
    types = set(cfg.structure_types)
    by_right = {C: calls, P: puts}
    if StructureType.LONG_CALL in types:
        for k in calls:
            yield StructureType.LONG_CALL, ((C, k, L, 1),)
    if StructureType.LONG_PUT in types:
        for k in puts:
            yield StructureType.LONG_PUT, ((P, k, L, 1),)
    for right, strikes in by_right.items():
        for k1 in strikes:
            for w in widths:
                k2 = k1 + w
                if k2 not in strikes:
                    continue
                lo_long = ((right, k1, L, 1), (right, k2, S, 1))
                lo_short = ((right, k1, S, 1), (right, k2, L, 1))
                # call: long the lower strike = debit; put: long the higher strike = debit
                debit, credit = (lo_long, lo_short) if right is C else (lo_short, lo_long)
                if StructureType.VERTICAL_DEBIT in types:
                    yield StructureType.VERTICAL_DEBIT, debit
                if StructureType.VERTICAL_CREDIT in types:
                    yield StructureType.VERTICAL_CREDIT, credit
                kc = k1 + w  # butterfly centred on k1 + w with wings k1 and k1 + 2w
                if k1 + 2 * w in strikes:
                    fly_type = (
                        StructureType.LONG_CALL_BUTTERFLY
                        if right is C
                        else StructureType.LONG_PUT_BUTTERFLY
                    )
                    if fly_type in types:
                        yield (
                            fly_type,
                            ((right, k1, L, 1), (right, kc, S, 2), (right, k1 + 2 * w, L, 1)),
                        )
    both = calls & puts
    if StructureType.LONG_STRADDLE in types:
        for k in both:
            yield StructureType.LONG_STRADDLE, ((P, k, L, 1), (C, k, L, 1))
    if StructureType.LONG_STRANGLE in types:
        for kp in (k for k in puts if k < spot):
            for kc in (k for k in calls if k > spot):
                yield StructureType.LONG_STRANGLE, ((P, kp, L, 1), (C, kc, L, 1))
    if StructureType.LONG_IRON_CONDOR in types:
        for w in widths:
            inner_puts = [k for k in puts if k < spot and k - w in puts]
            inner_calls = [k for k in calls if k > spot and k + w in calls]
            for kp in inner_puts:
                for kc in inner_calls:
                    yield (
                        StructureType.LONG_IRON_CONDOR,
                        ((P, kp - w, S, 1), (P, kp, L, 1), (C, kc, L, 1), (C, kc + w, S, 1)),
                    )
    if StructureType.LONG_IRON_BUTTERFLY in types and both:
        gap = min(abs(k - spot) for k in both)
        for k in (k for k in both if abs(k - spot) == gap):  # ATM: nearest strike(s)
            for w in widths:
                if k - w in puts and k + w in calls:
                    yield (
                        StructureType.LONG_IRON_BUTTERFLY,
                        ((P, k - w, S, 1), (P, k, L, 1), (C, k, L, 1), (C, k + w, S, 1)),
                    )
    if StructureType.IRON_CONDOR in types:
        for w in widths:
            short_puts = [k for k in puts if k - w in puts]
            short_calls = [k for k in calls if k + w in calls]
            if cfg.condor_shorts_out_of_the_money:
                short_puts = [k for k in short_puts if k < spot]
                short_calls = [k for k in short_calls if k > spot]
            for kps in short_puts:
                for kcs in short_calls:
                    if kps >= kcs:
                        continue
                    yield (
                        StructureType.IRON_CONDOR,
                        (
                            (P, kps - w, L, 1),
                            (P, kps, S, 1),
                            (C, kcs, S, 1),
                            (C, kcs + w, L, 1),
                        ),
                    )


def _leg_sort_key(shape_leg: tuple[Right, Decimal, Side, int]) -> tuple[int, Decimal]:
    right, strike, _, _ = shape_leg
    return (0 if right is Right.PUT else 1, strike)


def generate(
    state: MarketState,
    config: MovegenConfig = MOVEGEN_2,
    *,
    sizing: SizingRule = SIZE_1,
    fill_rule: FillRule = FILL_1,
    fees: FeeSchedule = FEES_1,
) -> tuple[tuple[Candidate, ...], MovegenSummary]:
    notes: list[MovegenNote] = []
    today: date = state.as_of.astimezone(NEW_YORK).date()

    chains: dict[str, dict[tuple[date, Right, Decimal], OptionQuote]] = defaultdict(dict)
    nonstandard: dict[str, int] = defaultdict(int)
    for q in state.option_quotes:
        c = q.contract
        if not c.standard_deliverable:
            nonstandard[c.underlying] += 1
            continue
        chains[c.underlying][(c.expiration, c.right, c.strike)] = q
    for sym, n in sorted(nonstandard.items()):
        notes.append(MovegenNote(symbol=sym, code="NONSTANDARD_EXCLUDED", detail=f"{n} contracts"))

    underlying_quotes = {u.symbol: u for u in state.underlying_quotes}
    leg_cache: dict[tuple[str, Side, int], Leg] = {}
    keyed: list[tuple[tuple[object, ...], Candidate]] = []

    for sym in state.underlyings:
        uq = underlying_quotes.get(sym)
        if uq is None or uq.bid <= 0 or uq.ask <= 0:
            notes.append(MovegenNote(symbol=sym, code="NO_UNDERLYING_PRICE"))
            continue
        spot = (uq.bid + uq.ask) / TWO
        band = config.band_for(spot)
        lo, hi = spot * (1 - config.strike_band), spot * (1 + config.strike_band)
        chain = chains.get(sym, {})
        if not chain:
            notes.append(MovegenNote(symbol=sym, code="NO_OPTION_QUOTES"))
            continue
        expirations = sorted(
            {e for (e, _, _) in chain if config.min_dte <= (e - today).days <= config.max_dte}
        )
        if not expirations:
            notes.append(MovegenNote(symbol=sym, code="NO_EXPIRATION_IN_RANGE"))
        for exp in expirations:
            lattice: dict[Right, set[Decimal]] = {Right.CALL: set(), Right.PUT: set()}
            for e, right, k in chain:
                if e == exp and lo <= k <= hi and _on_lattice(k, band.strike_step):
                    lattice[right].add(k)
            for stype, shape in _shapes(
                lattice[Right.CALL], lattice[Right.PUT], spot, band.widths, config
            ):
                ordered = tuple(sorted(shape, key=_leg_sort_key))
                legs: list[Leg] = []
                quotes: list[OptionQuote] = []
                for right, k, side, ratio in ordered:
                    q = chain[(exp, right, k)]
                    key = (q.contract.osi_symbol, side, ratio)
                    leg = leg_cache.get(key)
                    if leg is None:
                        leg = Leg(contract=q.contract, side=side, ratio=ratio)
                        leg_cache[key] = leg
                    legs.append(leg)
                    quotes.append(q)
                missing = [
                    q.contract.osi_symbol.replace(" ", "") for q in quotes if not q.is_two_sided
                ]
                if missing:
                    cand = Candidate(
                        legs=tuple(legs),
                        structure_type=stype,
                        quantity=0,
                        entry_rule=fill_rule.version,
                        exit_policy_id=config.exit_policy_id,
                        priceability=Priceability.UNPRICEABLE,
                        unpriceable_reason="NO_TWO_SIDED_QUOTE:" + ",".join(missing),
                    )
                else:
                    prices = [
                        q.ask if lg.side is Side.LONG else q.bid
                        for lg, q in zip(legs, quotes, strict=True)
                    ]
                    structure = Structure.price(legs, prices, stype, fees)
                    cand = Candidate.priced(
                        structure,
                        quantity=sizing.quantity(structure),
                        entry_rule=fill_rule.version,
                        exit_policy_id=config.exit_policy_id,
                    )
                sort_key = (
                    sym,
                    exp,
                    stype.value,
                    tuple((r.value, k, int(s), n) for r, k, s, n in ordered),
                )
                keyed.append((sort_key, cand))

    keyed.sort(key=lambda kc: kc[0])
    null = Candidate.null_move(entry_rule=fill_rule.version, exit_policy_id=config.exit_policy_id)
    candidates = (null, *(c for _, c in keyed))
    priced = [c for c in candidates[1:] if c.priceability is Priceability.PRICED]
    summary = MovegenSummary(
        snapshot_id=state.snapshot_id,
        config_version=config.version,
        config_hash=config.content_hash(),
        sizing_version=sizing.version,
        fill_rule_version=fill_rule.version,
        fee_version=fees.version,
        n_candidates=len(candidates),
        n_priced=len(priced),
        n_unpriceable=len(candidates) - 1 - len(priced),
        n_not_recommendable=sum(1 for c in priced if c.quantity == 0),
        list_hash=list_hash(candidates),
        notes=tuple(notes),
    )
    return candidates, summary
