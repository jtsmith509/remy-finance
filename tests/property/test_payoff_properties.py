"""R0.6 property tests (hypothesis).

1. For random defined-risk structures, realized P&L at any S_T is >= -capital at risk,
   and the bound is attained (capital at risk is the true worst case, not loose).
2. Put/call structures with identical payoff diagrams produce identical payoffs.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from remy_finance.core import FEES_1, Leg, Right, Side, Structure, StructureType, UndefinedRiskError
from tests.factories import leg

D = Decimal
strike_ints = st.integers(min_value=1, max_value=400)
price_cents = st.integers(min_value=0, max_value=5000).map(lambda c: D(c) / 100)


@st.composite
def random_legs(draw: st.DrawFn) -> tuple[list[Leg], list[Decimal]]:
    n = draw(st.integers(min_value=1, max_value=4))
    keys = draw(
        st.lists(
            st.tuples(strike_ints, st.sampled_from(list(Right))),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    legs = [
        leg(
            str(k),
            r,
            draw(st.sampled_from(list(Side))),
            ratio=draw(st.integers(min_value=1, max_value=3)),
        )
        for k, r in keys
    ]
    prices = [draw(price_cents) for _ in legs]
    return legs, prices


def try_price(legs: list[Leg], prices: list[Decimal], closing: bool = False) -> Structure:
    try:
        return Structure.price(
            legs, prices, StructureType.OTHER_DEFINED_RISK, FEES_1, closing_trade_required=closing
        )
    except UndefinedRiskError:
        assume(False)
        raise


underlying_prices = st.one_of(
    st.integers(min_value=0, max_value=1000).map(D),
    st.decimals(min_value=0, max_value=1000, places=2, allow_nan=False, allow_infinity=False),
    st.just(D(10) ** 9),
)


@given(random_legs(), underlying_prices, st.booleans())
def test_pnl_never_below_minus_capital_at_risk(
    data: tuple[list[Leg], list[Decimal]], s_t: Decimal, closing: bool
) -> None:
    s = try_price(*data, closing=closing)
    assert s.pnl_at_expiry(s_t) >= -s.capital_at_risk


@given(random_legs())
def test_capital_at_risk_is_attained(data: tuple[list[Leg], list[Decimal]]) -> None:
    s = try_price(*data)
    points = [D(0), *(lg.contract.strike for lg in s.legs)]
    worst = min(s.pnl_at_expiry(x) for x in points)
    if s.max_loss > 0:
        assert worst == -s.capital_at_risk
    else:  # never loses before fees
        assert worst >= -s.capital_at_risk


@given(random_legs(), underlying_prices)
def test_max_gain_bounds_pnl(data: tuple[list[Leg], list[Decimal]], s_t: Decimal) -> None:
    s = try_price(*data)
    if s.max_gain is not None:
        assert s.pnl_at_expiry(s_t) <= s.max_gain - s.entry_fees - s.exit_fees
    else:
        far = D(10) ** 12
        assert s.pnl_at_expiry(far * 2) > s.pnl_at_expiry(far)


@given(random_legs())
def test_unbounded_loss_always_rejected(data: tuple[list[Leg], list[Decimal]]) -> None:
    legs, prices = data
    net_calls = sum(int(lg.side) * lg.ratio for lg in legs if lg.contract.right is Right.CALL)
    try:
        Structure.price(legs, prices, StructureType.OTHER_DEFINED_RISK, FEES_1)
    except UndefinedRiskError:
        assert net_calls < 0
    else:
        assert net_calls >= 0


@given(strike_ints, st.integers(min_value=1, max_value=50), underlying_prices)
def test_call_and_put_butterflies_have_identical_payoff(k: int, w: int, s_t: Decimal) -> None:
    assume(k - w >= 1)
    lo, mid, hi = str(k - w), str(k), str(k + w)
    calls = [
        leg(lo, Right.CALL, Side.LONG),
        leg(mid, Right.CALL, Side.SHORT, 2),
        leg(hi, Right.CALL, Side.LONG),
    ]
    puts = [
        leg(lo, Right.PUT, Side.LONG),
        leg(mid, Right.PUT, Side.SHORT, 2),
        leg(hi, Right.PUT, Side.LONG),
    ]
    zero = [D(0)] * 3
    bc = Structure.price(calls, zero, StructureType.LONG_CALL_BUTTERFLY, FEES_1)
    bp = Structure.price(puts, zero, StructureType.LONG_PUT_BUTTERFLY, FEES_1)
    assert bc.payoff_at_expiry(s_t) == bp.payoff_at_expiry(s_t)


@given(strike_ints, st.integers(min_value=1, max_value=50), underlying_prices)
def test_bull_call_and_bull_put_spreads_differ_by_a_constant(k1: int, w: int, s_t: Decimal) -> None:
    # Call spread payoff = put spread payoff + (K2 - K1) x 100 at every S_T (same diagram, shifted).
    k2 = k1 + w
    call_spread = Structure.price(
        [leg(str(k1), Right.CALL, Side.LONG), leg(str(k2), Right.CALL, Side.SHORT)],
        [D(0), D(0)],
        StructureType.VERTICAL_DEBIT,
        FEES_1,
    )
    put_spread = Structure.price(
        [leg(str(k1), Right.PUT, Side.LONG), leg(str(k2), Right.PUT, Side.SHORT)],
        [D(0), D(0)],
        StructureType.VERTICAL_CREDIT,
        FEES_1,
    )
    assert call_spread.payoff_at_expiry(s_t) - put_spread.payoff_at_expiry(s_t) == D(w * 100)
