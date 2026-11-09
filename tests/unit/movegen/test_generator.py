"""R0.7 move generator tests: bounds, counts, priceability, sizing, determinism."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from remy_finance.core import (
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    Priceability,
    Right,
    Session,
    Side,
    StructureType,
    UnderlyingQuote,
)
from remy_finance.movegen.config import SIZE_1
from remy_finance.movegen.generator import generate

D = Decimal
AS_OF = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # Fri 10:00 ET
QT = AS_OF - timedelta(seconds=2)
EXP = date(2026, 11, 20)  # 14 DTE
STRIKES = [D(90) + D("2.5") * i for i in range(9)]  # 90 .. 110


def oq(
    k: Decimal,
    right: Right,
    exp: date = EXP,
    *,
    bid: str = "1.00",
    ask: str = "1.10",
    sym: str = "XYZ",
    root: str | None = None,
    standard: bool = True,
) -> OptionQuote:
    c = OptionContract.build(sym, exp, k, right, root=root, standard_deliverable=standard)
    return OptionQuote(
        contract=c, bid=D(bid), ask=D(ask), bid_size=20, ask_size=20, quote_time=QT, source="t"
    )


def board(
    extra: tuple[OptionQuote, ...] = (),
    *,
    base: tuple[OptionQuote, ...] | None = None,
    underlying: bool = True,
) -> MarketState:
    quotes = base if base is not None else tuple(oq(k, r) for k in STRIKES for r in Right)
    uq = (
        UnderlyingQuote(
            symbol="XYZ",
            bid=D("99.99"),
            ask=D("100.01"),
            bid_size=1,
            ask_size=1,
            quote_time=QT,
            source="t",
        ),
    )
    return MarketState(
        session=Session.OPEN,
        as_of=AS_OF,
        mode=Mode.REPLAY,
        universe_version="T",
        underlyings=("XYZ",),
        underlying_quotes=uq if underlying else (),
        option_quotes=quotes + extra,
    )


def counts(state: MarketState) -> Counter[StructureType | None]:
    cands, _ = generate(state)
    return Counter(c.structure_type for c in cands)


def test_hand_computed_counts() -> None:
    c = counts(board())
    assert c[None] == 1  # null move
    assert c[StructureType.LONG_CALL] == 9 and c[StructureType.LONG_PUT] == 9
    # per right: w=2.5 -> 8 pairs, w=5 -> 7, w=10 -> 5 => 20; x2 rights
    assert c[StructureType.VERTICAL_DEBIT] == 40 and c[StructureType.VERTICAL_CREDIT] == 40
    # per right: w=2.5 -> 7, w=5 -> 5, w=10 -> 1
    assert c[StructureType.LONG_CALL_BUTTERFLY] == 13 and c[StructureType.LONG_PUT_BUTTERFLY] == 13
    # OTM shorts around spot 100: w=2.5 -> 3x3, w=5 -> 2x2, w=10 -> 0
    assert c[StructureType.IRON_CONDOR] == 13
    assert sum(c.values()) == 138


def test_null_move_first_and_always_present() -> None:
    cands, summary = generate(board(base=(), underlying=False))
    assert len(cands) == 1 and cands[0].is_null_move
    assert {n.code for n in summary.notes} == {"NO_UNDERLYING_PRICE"}
    cands, _ = generate(board())
    assert cands[0].is_null_move and not any(c.is_null_move for c in cands[1:])


def test_bounds_dte_band_and_lattice() -> None:
    extra = (
        oq(D(100), Right.CALL, date(2026, 11, 12)),  # 6 DTE: too near
        oq(D(100), Right.CALL, date(2027, 1, 8)),  # 63 DTE: too far
        oq(D(100), Right.CALL, date(2026, 11, 13)),  # 7 DTE: in
        oq(D(100), Right.CALL, date(2027, 1, 5)),  # 60 DTE: in
        oq(D(70), Right.CALL),
        oq(D(130), Right.CALL),  # outside +/-25%
        oq(D(101), Right.CALL),  # off the 2.5 lattice
    )
    cands, _ = generate(board(extra))
    strikes = {lg.contract.strike for c in cands for lg in c.legs}
    exps = {lg.contract.expiration for c in cands for lg in c.legs}
    assert {D(70), D(130), D(101)}.isdisjoint(strikes)
    assert exps == {EXP, date(2026, 11, 13), date(2027, 1, 5)}


def test_unpriceable_kept_with_reason() -> None:
    base = tuple(
        oq(k, r, bid="0" if (k == D(105) and r is Right.CALL) else "1.00")
        for k in STRIKES
        for r in Right
    )
    cands, summary = generate(board(base=base))
    bad = [c for c in cands if c.priceability is Priceability.UNPRICEABLE]
    assert bad and summary.n_unpriceable == len(bad)
    assert all("XYZ261120C00105000" in (c.unpriceable_reason or "") for c in bad)
    assert all(c.structure is None and c.quantity == 0 for c in bad)
    assert all(
        any(lg.contract.strike == D(105) and lg.contract.right is Right.CALL for lg in c.legs)
        for c in bad
    )
    assert len(cands) == 138  # nothing dropped


def test_entry_prices_cross_the_spread_and_sizing() -> None:
    cands, _ = generate(board())
    debit = next(
        c
        for c in cands
        if c.structure_type is StructureType.VERTICAL_DEBIT
        and [(lg.contract.right, lg.contract.strike, lg.side) for lg in c.legs]
        == [(Right.CALL, D(100), Side.LONG), (Right.CALL, D("102.5"), Side.SHORT)]
    )
    assert debit.structure is not None
    assert debit.structure.entry_prices == (D("1.10"), D("1.00"))  # ask long, bid short
    assert debit.quantity == SIZE_1.quantity(debit.structure)
    flies = [c for c in cands if c.structure_type is StructureType.LONG_CALL_BUTTERFLY]
    assert all(c.quantity <= 5 for c in flies)  # 10 contracts per leg, short leg ratio 2


def test_nonstandard_excluded_with_note() -> None:
    adj = oq(D(100), Right.CALL, root="XYZ1", standard=False)
    cands, summary = generate(board((adj,)))
    assert all(lg.contract.standard_deliverable for c in cands for lg in c.legs)
    assert any(n.code == "NONSTANDARD_EXCLUDED" for n in summary.notes)


def test_deterministic_and_order_independent() -> None:
    quotes = tuple(oq(k, r) for k in STRIKES for r in Right)
    a, sa = generate(board(base=quotes))
    b, sb = generate(board(base=tuple(reversed(quotes))))
    assert sa.list_hash == sb.list_hash
    assert [c.canonical_json() for c in a] == [c.canonical_json() for c in b]
    assert sa == sb


def test_list_hash_changes_with_inputs() -> None:
    _, s1 = generate(board())
    base = tuple(oq(k, r, ask="1.11" if k == D(100) else "1.10") for k in STRIKES for r in Right)
    _, s2 = generate(board(base=base))
    assert s1.list_hash != s2.list_hash and s1.snapshot_id != s2.snapshot_id


def test_list_hash_with_shared_memo_equals_plain_hash() -> None:
    """The canonical memo used by list_hash is a speed-up only: same bytes, same hash."""
    import hashlib

    cands, summary = generate(board())
    plain = hashlib.sha256()
    for c in cands:
        plain.update(c.content_hash().encode("ascii"))
        plain.update(b"\n")
    assert summary.list_hash == plain.hexdigest()
