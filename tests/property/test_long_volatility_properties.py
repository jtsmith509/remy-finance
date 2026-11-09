"""R0 amendment (DR-0035) property tests: for each long-volatility structure, realized
P&L at expiration is >= -capital at risk at every S_T and any price; for debits (all
realistic quotes) the bound is attained; and the downside gain is bounded (S_T >= 0)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from remy_finance.core import FEES_1, Right, Side, Structure, StructureType
from tests.factories import leg

D = Decimal
C, P = Right.CALL, Right.PUT
L, S = Side.LONG, Side.SHORT
cents = st.integers(min_value=0, max_value=3000).map(lambda c: D(c) / 100)
s_t = st.one_of(
    st.decimals(min_value=0, max_value=2000, places=2, allow_nan=False, allow_infinity=False),
    st.just(D(0)),
    st.just(D(10) ** 9),
)


def straddle(k: int, w: int, px: list[Decimal]) -> Structure:
    return Structure.price(
        [leg(str(k), C, L), leg(str(k), P, L)], px[:2], StructureType.LONG_STRADDLE, FEES_1
    )


def strangle(k: int, w: int, px: list[Decimal]) -> Structure:
    return Structure.price(
        [leg(str(k + w), C, L), leg(str(k), P, L)], px[:2], StructureType.LONG_STRANGLE, FEES_1
    )


def long_iron_condor(k: int, w: int, px: list[Decimal]) -> Structure:
    # inner put k, inner call k + w, wings one w further out
    return Structure.price(
        [
            leg(str(k - w), P, S),
            leg(str(k), P, L),
            leg(str(k + w), C, L),
            leg(str(k + 2 * w), C, S),
        ],
        px,
        StructureType.LONG_IRON_CONDOR,
        FEES_1,
    )


def long_iron_butterfly(k: int, w: int, px: list[Decimal]) -> Structure:
    return Structure.price(
        [leg(str(k - w), P, S), leg(str(k), P, L), leg(str(k), C, L), leg(str(k + w), C, S)],
        px,
        StructureType.LONG_IRON_BUTTERFLY,
        FEES_1,
    )


BUILDERS: list[Callable[[int, int, list[Decimal]], Structure]] = [
    straddle,
    strangle,
    long_iron_condor,
    long_iron_butterfly,
]


@given(
    build=st.sampled_from(BUILDERS),
    k=st.integers(min_value=20, max_value=500),
    w=st.integers(min_value=1, max_value=19),
    px=st.lists(cents, min_size=4, max_size=4),
    s_ts=st.lists(s_t, min_size=1, max_size=20),
    qty=st.integers(min_value=1, max_value=10),
)
def test_pnl_never_below_minus_capital_at_risk(
    build: Callable[[int, int, list[Decimal]], Structure],
    k: int,
    w: int,
    px: list[Decimal],
    s_ts: list[Decimal],
    qty: int,
) -> None:
    s = build(k, w, px)
    for x in s_ts:
        assert s.pnl_at_expiry(x, qty) >= -s.capital_at_risk * qty


@given(
    build=st.sampled_from(BUILDERS),
    k=st.integers(min_value=20, max_value=500),
    w=st.integers(min_value=1, max_value=19),
    px=st.lists(cents, min_size=4, max_size=4),
)
def test_bound_is_attained_and_downside_is_bounded(
    build: Callable[[int, int, list[Decimal]], Structure],
    k: int,
    w: int,
    px: list[Decimal],
) -> None:
    s = build(k, w, px)
    # tightness holds for debits (every realistic quote of these structures); a "long"
    # structure quoted at a net credit profits everywhere and its max loss floors at 0
    assume(s.net_premium > 0)
    strikes = {lg.contract.strike for lg in s.legs}
    worst = min(s.pnl_at_expiry(x) for x in (*strikes, D(0)))
    assert worst == -s.capital_at_risk  # capital at risk is the true worst case, not loose
    # the downside (S_T = 0) gain is finite and no larger than the put side can pay
    put_strikes = [lg.contract.strike for lg in s.legs if lg.contract.right is P]
    assert s.pnl_at_expiry(D(0)) <= max(put_strikes) * 100
