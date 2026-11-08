"""Dedicated look-ahead test module (handoff Section 7).

Deliberately requests future data through every public data path and asserts
``LookaheadError``: (a) asking the view for a time after ``as_of``; (b) a source that
returns data available after ``as_of``; (c) honest sources never leak later data.
A meta-test fails if a public read method is added to PointInTimeView without a test.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from remy_finance.core import (
    CorporateAction,
    CorporateActionType,
    DividendEvent,
    LookaheadError,
    OptionContract,
    OptionQuote,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.data import FixtureSource, PointInTimeView
from remy_finance.data.thetadata import ThetaDataSource
from remy_finance.execution.assignment import known_dividend_on
from tests import factories as f

AS_OF = f.AS_OF
LATER = AS_OF + timedelta(microseconds=1)


class LeakySource:
    """A defective source: returns data available just after the time asked for."""

    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        return (f.contract(),)

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        return (f.option_quote(at=ts + timedelta(microseconds=1)),)

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None:
        return f.underlying_quote(at=ts + timedelta(microseconds=1))

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        return (f.dividend(public=known_as_of + timedelta(microseconds=1)),)

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        t = known_as_of + timedelta(microseconds=1)
        return (
            CorporateAction(
                symbol=symbol,
                action_type=CorporateActionType.SPLIT,
                effective_date=t.date(),
                event_time=t,
                public_time=t,
                source="leaky",
            ),
        )

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]:
        r = f.rate()
        return (r.model_copy(update={"public_time": known_as_of + timedelta(microseconds=1)}),)


READS: dict[str, Callable[[PointInTimeView], Any]] = {
    "chain_quotes": lambda v: v.chain_quotes("XYZ"),
    "underlying_quote": lambda v: v.underlying_quote("XYZ"),
    "dividends": lambda v: v.dividends("XYZ"),
    "corporate_actions": lambda v: v.corporate_actions("XYZ"),
    "rates": lambda v: v.rates(),
}
FUTURE_REQUESTS: dict[str, Callable[[PointInTimeView], Any]] = {
    "chain_quotes": lambda v: v.chain_quotes("XYZ", LATER),
    "underlying_quote": lambda v: v.underlying_quote("XYZ", LATER),
}
NO_TIME_ARGUMENT = {"list_contracts", "as_of"}  # cannot be asked for a later time


def test_every_view_read_path_is_covered() -> None:
    public = {n for n, _ in inspect.getmembers(PointInTimeView) if not n.startswith("_")}
    assert public == set(READS) | NO_TIME_ARGUMENT


@pytest.mark.parametrize("path", sorted(FUTURE_REQUESTS))
def test_requesting_a_future_time_raises(path: str) -> None:
    view = PointInTimeView(FixtureSource(), AS_OF)
    with pytest.raises(LookaheadError, match="requested"):
        FUTURE_REQUESTS[path](view)


@pytest.mark.parametrize("path", sorted(READS))
def test_source_returning_future_data_raises(path: str) -> None:
    view = PointInTimeView(LeakySource(), AS_OF)
    with pytest.raises(LookaheadError, match="source returned"):
        READS[path](view)


def honest_fixture() -> FixtureSource:
    """Has data both before and after AS_OF; an honest read must only see the former."""
    return FixtureSource(
        [
            f.option_quote(at=AS_OF - timedelta(minutes=1), bid="2.00", ask="2.10"),
            f.option_quote(at=AS_OF + timedelta(minutes=1), bid="9.00", ask="9.10"),
            f.underlying_quote(at=AS_OF - timedelta(seconds=1)),
            f.underlying_quote(at=AS_OF + timedelta(seconds=1)).model_copy(
                update={"bid": f.underlying_quote().bid + 5}
            ),
            f.dividend(),
            f.dividend(public=AS_OF + timedelta(hours=6)).model_copy(update={"amount": 9}),
            f.rate(),
            f.rate().model_copy(update={"public_time": AS_OF + timedelta(days=1), "rate": 1}),
        ]
    )


def test_honest_source_never_leaks_later_data() -> None:
    view = PointInTimeView(honest_fixture(), AS_OF)
    (q,) = view.chain_quotes("XYZ")
    assert q.bid == 2 and q.quote_time < AS_OF
    uq = view.underlying_quote("XYZ")
    assert uq is not None and uq.quote_time < AS_OF
    assert (
        all(d.public_time <= AS_OF for d in view.dividends("XYZ"))
        and len(view.dividends("XYZ")) == 1
    )
    assert len(view.rates()) == 1


def test_earlier_time_through_view_is_allowed() -> None:
    view = PointInTimeView(honest_fixture(), AS_OF)
    assert view.chain_quotes("XYZ", AS_OF - timedelta(seconds=30))[0].bid == 2


def test_vendor_rows_stamped_after_request_are_caught() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        body = {
            "response": [
                {
                    "contract": {
                        "symbol": "XYZ",
                        "strike": 100,
                        "right": "CALL",
                        "expiration": "2026-11-20",
                    },
                    "data": [
                        {
                            "bid": 1,
                            "ask": 2,
                            "bid_size": 1,
                            "ask_size": 1,
                            "timestamp": "2026-11-06T10:00:00.001",
                        }
                    ],
                }
            ]
        }
        return httpx.Response(200, json=body)

    src = ThetaDataSource("http://t", transport=httpx.MockTransport(h))
    with pytest.raises(LookaheadError):
        PointInTimeView(src, AS_OF).chain_quotes("XYZ")


def test_naive_as_of_rejected() -> None:
    with pytest.raises(ValueError):
        PointInTimeView(FixtureSource(), AS_OF.replace(tzinfo=None))


def test_board_and_assignment_paths_also_guard() -> None:
    with pytest.raises(LookaheadError):
        f.market_state(option_quotes=(f.option_quote(at=LATER),))
    with pytest.raises(LookaheadError):
        known_dividend_on([f.dividend(public=LATER)], "XYZ", f.EXP, AS_OF)
