"""Property tests for canonical serialization and hashing (R0.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from remy_finance.core import OptionContract, OptionQuote, Right, canonical
from tests import factories as f

finite_decimals = st.decimals(
    allow_nan=False, allow_infinity=False, min_value=-(10**9), max_value=10**9
)
prices = st.decimals(min_value=0, max_value=10_000, places=4, allow_nan=False, allow_infinity=False)
strikes = st.integers(min_value=1, max_value=99_999_999).map(lambda m: Decimal(m) / 1000)
expirations = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))
times = st.datetimes(
    min_value=datetime(2010, 1, 1),  # noqa: DTZ001 (hypothesis wants naive bounds)
    max_value=datetime(2040, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)


@given(finite_decimals)
def test_decimal_canonical_is_idempotent_and_value_preserving(d: Decimal) -> None:
    text = canonical.canonical_decimal(d)
    assert Decimal(text) == d
    assert canonical.canonical_decimal(Decimal(text)) == text
    assert "E" not in text and "e" not in text


@given(finite_decimals, st.integers(min_value=0, max_value=6))
def test_trailing_zeros_do_not_change_text(d: Decimal, zeros: int) -> None:
    sign, digits, exponent = d.as_tuple()
    assert isinstance(exponent, int)
    padded = Decimal((sign, digits + (0,) * zeros, exponent - zeros))  # same value, more zeros
    assert padded == d
    assert canonical.canonical_decimal(padded) == canonical.canonical_decimal(d)


@st.composite
def option_quotes(draw: st.DrawFn) -> OptionQuote:
    contract = OptionContract.build(
        draw(st.sampled_from(["SPY", "AAPL", "KO", "F"])),
        draw(expirations),
        draw(strikes),
        draw(st.sampled_from(list(Right))),
    )
    return OptionQuote(
        contract=contract,
        bid=draw(prices),
        ask=draw(prices),
        bid_size=draw(st.integers(min_value=0, max_value=100_000)),
        ask_size=draw(st.integers(min_value=0, max_value=100_000)),
        quote_time=draw(times),
        source="hypothesis",
    )


@given(option_quotes())
def test_quote_round_trip_and_hash_stable(q: OptionQuote) -> None:
    text = q.canonical_json()
    back = OptionQuote.from_canonical_json(text)
    assert back == q
    assert back.content_hash() == q.content_hash()
    assert back.canonical_json() == text


@given(st.permutations(range(3)))
def test_snapshot_id_order_independent(order: list[int]) -> None:
    quotes = f.market_state().option_quotes
    shuffled = tuple(quotes[i] for i in order)
    assert f.market_state(option_quotes=shuffled).snapshot_id == f.market_state().snapshot_id


@given(st.integers(min_value=-12, max_value=12))
def test_timezone_of_input_does_not_change_hash(hours: int) -> None:
    from datetime import timezone

    instant = f.AS_OF - timedelta(seconds=5)
    q1 = f.option_quote(at=instant)
    q2 = f.option_quote(at=instant.astimezone(timezone(timedelta(hours=hours))))
    assert q1.content_hash() == q2.content_hash()
