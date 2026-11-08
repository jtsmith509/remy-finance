"""Regression tests for the independent R0 review findings (docs/review/R0-findings.md).

Each test is named after its finding. They failed on the reviewed commit aeda0c2.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from remy_finance.core import (
    FEES_1,
    CorporateAction,
    CorporateActionType,
    DividendEvent,
    LookaheadError,
    MarketState,
    OptionContract,
    OptionQuote,
    Right,
    Session,
    Side,
    Structure,
    StructureType,
    UnderlyingQuote,
)
from remy_finance.data import FixtureSource
from remy_finance.data.polygon import PolygonSource
from remy_finance.execution.settlement import settle_at_expiration
from remy_finance.snapshots.builder import build_prospective
from tests import factories as f
from tests.factories import EXP, bull_call_spread, leg

D = Decimal


# --------------------------------------------------------------- F03: float cash inputs


@pytest.mark.parametrize("bad", [0.1, 2.5, True])
def test_f03_structure_factory_rejects_float_and_bool_cash(bad: object) -> None:
    with pytest.raises(ValueError, match="rejects"):
        Structure.price([leg("100", Right.CALL, Side.LONG)], [bad], StructureType.LONG_CALL, FEES_1)  # type: ignore[list-item]


@pytest.mark.parametrize("bad", [100.5, False])
def test_f03_contract_factory_rejects_float_and_bool_strike(bad: object) -> None:
    with pytest.raises(ValueError, match="rejects"):
        OptionContract.build("XYZ", EXP, bad, Right.CALL)  # type: ignore[arg-type]


def test_f03_pnl_and_settlement_reject_float_prices() -> None:
    with pytest.raises(ValueError):
        bull_call_spread().pnl_at_expiry(110.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        settle_at_expiration(bull_call_spread().legs, 1, expiration=EXP, official_close=110.0)  # type: ignore[arg-type]


def test_f03_exact_inputs_still_accepted() -> None:
    s = Structure.price(
        [leg("100", Right.CALL, Side.LONG)], ["0.1"], StructureType.LONG_CALL, FEES_1
    )
    assert s.net_premium == D(10) and s.capital_at_risk == D("10.65")
    assert OptionContract.build("XYZ", EXP, 100, Right.CALL).strike == D(100)


# --------------------------------------------------- F04: exactness at large prices


@pytest.mark.parametrize("s_t", ["105", "1e27", "1e29", "1e100", "1e300"])
def test_f04_bounded_spread_payoff_exact_at_any_terminal_price(s_t: str) -> None:
    s = bull_call_spread()  # +100C @2.50, -105C @1.00
    assert s.payoff_at_expiry(D(s_t)) == D(500)
    assert s.pnl_at_expiry(D(s_t)) == D("348.70")


def test_f04_unbounded_long_call_is_exact_not_rounded() -> None:
    s = Structure.price(
        [leg("100", Right.CALL, Side.LONG)], ["2.50"], StructureType.LONG_CALL, FEES_1
    )
    with localcontext() as ctx:
        ctx.prec = 200
        expected = (D("1e100") - 100) * 100 - D("250.65")
    assert s.pnl_at_expiry(D("1e100")) == expected


def test_f04_settlement_exact_at_large_close() -> None:
    st_ = settle_at_expiration(bull_call_spread().legs, 3, expiration=EXP, official_close=D("1e40"))
    assert st_.settlement_value == D(1500)


@given(
    st.integers(min_value=1, max_value=400),
    st.integers(min_value=1, max_value=50),
    st.integers(min_value=0, max_value=300),
)
def test_f04_bounded_verticals_keep_exact_max_gain_at_huge_prices(
    k: int, w: int, exponent: int
) -> None:
    s = Structure.price(
        [leg(str(k), Right.CALL, Side.LONG), leg(str(k + w), Right.CALL, Side.SHORT)],
        ["1.00", "0.40"],
        StructureType.VERTICAL_DEBIT,
        FEES_1,
    )
    big = D(10) ** exponent + k + w
    assert s.payoff_at_expiry(big) == D(w * 100)
    assert s.pnl_at_expiry(big) == D(w * 100) - s.net_premium - s.entry_fees


def test_f04_dates_unaffected() -> None:  # sanity: the change does not touch non-decimal fields
    assert bull_call_spread().expiration == date(2026, 11, 20)


# ------------------------------------- F02 / F06: prospective builder read guards

LIVE_AT = f.AS_OF + timedelta(seconds=3)


class _Live:
    def __init__(self, raise_in: str | None = None) -> None:
        self.raise_in = raise_in

    def live_chain_quotes(self, underlying: str) -> tuple[OptionQuote, ...]:
        if self.raise_in == "chain":
            raise LookaheadError("vendor returned a quote stamped after the request")
        return (f.option_quote(at=f.AS_OF),)

    def live_underlying_quote(self, symbol: str) -> UnderlyingQuote | None:
        if self.raise_in == "underlying":
            raise LookaheadError("vendor returned a quote stamped after the request")
        return f.underlying_quote(at=f.AS_OF)


def _prospective(live: _Live, events: FixtureSource) -> MarketState:
    return build_prospective(
        live,
        events,
        Session.OPEN,
        universe_version="U",
        underlyings=("XYZ",),
        clock=lambda: LIVE_AT,
    )


@pytest.mark.parametrize("where", ["chain", "underlying"])
def test_f02_live_lookahead_error_aborts_instead_of_becoming_note(where: str) -> None:
    with pytest.raises(LookaheadError):
        _prospective(_Live(raise_in=where), FixtureSource())


def _split(at: datetime) -> CorporateAction:
    return CorporateAction(
        symbol="XYZ",
        action_type=CorporateActionType.SPLIT,
        effective_date=at.date(),
        details=(("split_from", "1"), ("split_to", "2")),
        event_time=at,
        public_time=at,
        source="fixture",
    )


class _DividendsDown(FixtureSource):
    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        raise RuntimeError("dividend endpoint down")


def test_f06_dividend_failure_does_not_skip_independent_split_read() -> None:
    events = _DividendsDown([_split(f.AS_OF - timedelta(days=3))])
    board = _prospective(_Live(), events)
    assert len(board.corporate_actions) == 1
    assert [n.code for n in board.quality_notes] == ["DIVIDENDS:RuntimeError"]
    assert board.option_quotes and board.underlying_quotes


def test_f06_polygon_keeps_splits_when_dividend_feed_fails() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if "splits" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [{"execution_date": "2026-06-10", "split_from": 1, "split_to": 4}]
                },
            )
        return httpx.Response(500, text="dividends down")

    src = PolygonSource("k", base_url="http://p", transport=httpx.MockTransport(h))
    acts = src.corporate_actions("XYZ", datetime(2026, 11, 6, 15, tzinfo=UTC))
    assert [a.action_type for a in acts] == [CorporateActionType.SPLIT]
    assert any("special dividends unavailable" in s for s in src.skipped)
