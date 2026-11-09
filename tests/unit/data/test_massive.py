"""MassiveSource against mocked HTTP (no network)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from remy_finance.core import CorporateActionType, DividendType, LookaheadError
from remy_finance.data import PointInTimeView
from remy_finance.data.massive import (
    DEFAULT_BASE_URL,
    MassiveEntitlementError,
    MassiveError,
    MassiveSource,
    ns_to_utc,
    utc_to_ns,
)
from remy_finance.data.thetadata import UnsupportedDataError

D = Decimal
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TS = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # Fri 10:00 ET
Handler = Callable[[httpx.Request], httpx.Response]


def src(handler: Handler, **kw: object) -> MassiveSource:
    return MassiveSource(
        "k",
        base_url="http://m",
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
        **kw,  # type: ignore[arg-type]
    )


def test_key_from_env_and_default_host(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("MASSIVE_API_KEY", "POLYGON_API_KEY", "MASSIVE_BASE_URL", "POLYGON_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(MassiveError, match="MASSIVE_API_KEY"):
        MassiveSource()
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    seen: list[tuple[str, str]] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers["Authorization"]))
        return httpx.Response(200, json={"results": []})

    MassiveSource(transport=httpx.MockTransport(h)).dividends("KO", TS)
    assert seen == [("api.massive.com", "Bearer abc")] and DEFAULT_BASE_URL.endswith("massive.com")


def test_dividends_new_endpoint_public_time_types_and_skips() -> None:
    calls: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if "cursor" not in str(req.url):
            row = {
                "cash_amount": 0.51,
                "declaration_date": "2026-10-16",
                "ex_dividend_date": "2026-11-28",
                "pay_date": "2026-12-15",
                "distribution_type": "recurring",
                "currency": "USD",
            }
            return httpx.Response(
                200, json={"results": [row], "next_url": "http://m/stocks/v1/dividends?cursor=x"}
            )
        rows = [
            {
                "cash_amount": 1.0,
                "declaration_date": "2026-11-06",
                "ex_dividend_date": "2026-11-20",
                "distribution_type": "special",
                "currency": "USD",
            },
            {
                "cash_amount": 0.5,
                "ex_dividend_date": "2026-09-01",
                "distribution_type": "recurring",
            },
            {
                "cash_amount": 0.7,
                "declaration_date": "2026-08-01",
                "ex_dividend_date": "2026-08-20",
                "distribution_type": "recurring",
                "currency": "CAD",
            },
        ]
        return httpx.Response(200, json={"results": rows})

    s = src(h)
    divs = s.dividends("KO", TS)  # the 2026-11-06 declaration (16:00 ET) is after 10:00 ET
    assert calls[0].url.path == "/stocks/v1/dividends" and calls[0].url.params["ticker"] == "KO"
    assert [(d.amount, d.dividend_type) for d in divs] == [(D("0.51"), DividendType.ORDINARY)]
    assert divs[0].public_time == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    assert isinstance(divs[0].amount, Decimal)
    assert [d.dividend_type for d in s.dividends("KO", datetime(2026, 11, 6, 21, tzinfo=UTC))] == [
        DividendType.SPECIAL,
        DividendType.ORDINARY,
    ]  # sorted by ex-date
    assert s.skipped == ["KO ex 2026-09-01: no declaration_date", "KO ex 2026-08-20: currency CAD"]
    assert len(calls) == 2  # cached


def _actions_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/stocks/v1/splits":
        rows = [
            {
                "execution_date": "2026-06-10",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
            },
            {
                "execution_date": "2025-01-02",
                "split_from": 10,
                "split_to": 1,
                "adjustment_type": "reverse_split",
            },
            # NVDA's July 2021 4-for-1 split, labelled a stock dividend by Massive (live run, M2)
            {
                "execution_date": "2021-07-20",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "stock_dividend",
            },
            {
                "execution_date": "2025-03-03",
                "split_from": 1,
                "split_to": 1.05,
                "adjustment_type": "stock_dividend",
            },
        ]
        return httpx.Response(200, json={"results": rows})
    if req.url.path == "/vX/reference/tickers/XYZ/events":
        assert req.url.params["types"] == "ticker_change"
        events = [  # shape seen live for META: initial ticker in 2012, rename in 2022
            {"date": "2022-06-09", "type": "ticker_change", "ticker_change": {"ticker": "XYZ"}},
            {"date": "2012-05-18", "type": "ticker_change", "ticker_change": {"ticker": "OLD"}},
        ]
        return httpx.Response(200, json={"results": {"events": events, "name": "XYZ Corp"}})
    row = {
        "cash_amount": 2.5,
        "declaration_date": "2026-05-01",
        "ex_dividend_date": "2026-05-20",
        "distribution_type": "special",
    }
    return httpx.Response(200, json={"results": [row]})


def test_splits_ticker_events_and_specials() -> None:
    acts = src(_actions_handler).corporate_actions("XYZ", TS)
    kinds = sorted(a.action_type.value for a in acts)
    assert kinds == [
        "OTHER",
        "REVERSE_SPLIT",
        "SPECIAL_DIVIDEND",
        "SPLIT",
        "SPLIT",
        "SYMBOL_CHANGE",
    ]
    split = next(a for a in acts if a.effective_date == date(2026, 6, 10))
    assert split.public_time == datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # 09:30 EDT on execution
    assert dict(split.details) == {
        "adjustment_type": "forward_split",
        "split_from": "1",
        "split_to": "4",
    }
    big_stock_div = next(a for a in acts if a.effective_date == date(2021, 7, 20))
    assert big_stock_div.action_type is CorporateActionType.SPLIT  # >= 25%: a split for options
    small = next(a for a in acts if a.effective_date == date(2025, 3, 3))
    assert small.action_type is CorporateActionType.OTHER
    (change,) = [a for a in acts if a.action_type is CorporateActionType.SYMBOL_CHANGE]
    assert dict(change.details) == {"from_ticker": "OLD", "to_ticker": "XYZ"}
    assert change.effective_date == date(2022, 6, 9)


def test_initial_ticker_event_is_not_a_change() -> None:
    """Live run: every symbol had one 2003-09-10 event naming its own ticker (start of records)."""

    def h(req: httpx.Request) -> httpx.Response:
        if "events" in req.url.path:
            ev = {"date": "2003-09-10", "type": "ticker_change", "ticker_change": {"ticker": "KO"}}
            return httpx.Response(200, json={"results": {"events": [ev]}})
        return httpx.Response(200, json={"results": []})

    acts = src(h).corporate_actions("KO", TS)
    assert not any(a.action_type is CorporateActionType.SYMBOL_CHANGE for a in acts)


def test_schedule_declared_dividends_are_public_only_on_ex_date() -> None:
    """Live run: SPY's 2026 distributions all carry declaration 2026-01-02 with exact amounts.
    The amount cannot be known in January, so those become public on the ex-date (DR-0027)."""

    def h(req: httpx.Request) -> httpx.Response:
        rows = [
            {
                "cash_amount": 1.796999,
                "declaration_date": "2026-01-02",
                "ex_dividend_date": "2026-03-20",
                "distribution_type": "recurring",
            },
            {
                "cash_amount": 1.903516,
                "declaration_date": "2026-01-02",
                "ex_dividend_date": "2026-06-18",
                "distribution_type": "recurring",
            },
            {
                "cash_amount": 0.53,
                "declaration_date": "2026-07-15",
                "ex_dividend_date": "2026-09-15",
                "distribution_type": "recurring",
            },  # an ordinary one-to-one declaration (KO-like)
        ]
        return httpx.Response(200, json={"results": rows})

    s = src(h)
    may = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    assert [d.ex_date for d in s.dividends("SPY", may)] == [
        date(2026, 3, 20)
    ]  # June's not yet public
    june = next(d for d in s.dividends("SPY", NOW) if d.ex_date == date(2026, 6, 18))
    assert june.public_time == datetime(2026, 6, 18, 13, 30, tzinfo=UTC)  # ex-date 09:30 EDT
    assert june.declaration_date == date(2026, 1, 2)  # the vendor's date is kept as data
    ko_like = next(d for d in s.dividends("SPY", NOW) if d.ex_date == date(2026, 9, 15))
    assert ko_like.public_time == datetime(2026, 7, 15, 20, 0, tzinfo=UTC)  # declaration 16:00 EDT
    assert len(s.schedule_declared) == 2


def test_events_failure_keeps_splits() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if "events" in req.url.path:
            return httpx.Response(500, text="down")
        return _actions_handler(req)

    s = src(h)
    acts = s.corporate_actions("XYZ", TS)
    assert CorporateActionType.SPLIT in {a.action_type for a in acts}
    assert any("ticker events unavailable" in x for x in s.skipped)


def test_underlying_nbbo_same_day_sip_stamped() -> None:
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        row = {
            "bid_price": 740.5,
            "ask_price": 740.71,
            "bid_size": 120,
            "ask_size": 80,
            "sip_timestamp": utc_to_ns(datetime(2026, 11, 6, 14, 59, 59, 812000, tzinfo=UTC)),
        }
        return httpx.Response(200, json={"results": [row]})

    q = src(h).underlying_quote_at("SPY", TS)
    assert q is not None and q.bid == D("740.5") and q.ask_size == 80
    assert q.quote_time == datetime(2026, 11, 6, 14, 59, 59, 812000, tzinfo=UTC)
    assert q.quality_flags == ()  # real quote time, unlike ThetaData's request-time stamp
    assert int(seen["timestamp.lte"]) == utc_to_ns(TS)
    assert ns_to_utc(int(seen["timestamp.gte"])) == datetime(
        2026, 11, 6, 5, 0, tzinfo=UTC
    )  # ET midnight
    assert seen["order"] == "desc" and seen["limit"] == "1"


def test_nbbo_not_on_plan_raises_entitlement_error() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"status": "NOT_AUTHORIZED", "message": "upgrade your plan"}
        )

    with pytest.raises(MassiveEntitlementError):
        src(h).underlying_quote_at("SPY", TS)


def test_treasury_yields_to_rate_points() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/fed/v1/treasury-yields"
        rows = [
            {"date": "2026-10-30", "yield_1_month": 4.01, "yield_3_month": 3.98},
            {"date": "2026-10-31", "yield_1_month": 4.0, "yield_3_month": 3.97},  # Saturday
            {
                "date": "2026-11-05",
                "yield_1_month": 3.99,
                "yield_3_month": 3.95,
            },  # public Fri 09:30 ET
        ]
        return httpx.Response(200, json={"results": rows})

    rates = src(h).rates(TS)
    got = [(r.symbol, r.observation_date, r.rate) for r in rates]
    assert ("TREASURY_3M", date(2026, 11, 5), D("0.0395")) in got
    assert all(r.observation_date != date(2026, 10, 31) for r in rates)
    assert next(
        r for r in rates if r.observation_date == date(2026, 11, 5)
    ).public_time == datetime(2026, 11, 6, 14, 30, tzinfo=UTC)
    early = src(h).rates(datetime(2026, 11, 6, 14, 0, tzinfo=UTC))
    assert {r.observation_date for r in early} == {date(2026, 10, 30)}


def test_throttle_and_rate_limit_retry() -> None:
    sleeps: list[float] = []
    n = {"calls": 0}

    def h(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"results": []})

    s = src(h, min_interval=0.0, sleep=sleeps.append)
    s.dividends("KO", TS)
    assert n["calls"] == 2 and sleeps and sleeps[0] >= 15.0 and s.requests_made == 2


def test_options_are_not_sourced_from_massive() -> None:
    s = src(lambda r: httpx.Response(200, json={}))
    for call in (
        lambda: s.list_contracts("SPY", TS),
        lambda: s.chain_quotes_at("SPY", TS),
        lambda: s.option_volume("SPY", date(2026, 11, 5)),
    ):
        with pytest.raises(UnsupportedDataError):
            call()


def test_calendar_and_close_helpers() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/marketstatus/upcoming":
            return httpx.Response(
                200,
                json=[
                    {
                        "date": "2026-11-26",
                        "exchange": "NYSE",
                        "name": "Thanksgiving",
                        "status": "closed",
                    }
                ],
            )
        if req.url.path == "/v1/marketstatus/now":
            return httpx.Response(200, json={"market": "closed", "exchanges": {"nyse": "closed"}})
        bar_ms = int(
            datetime(2026, 11, 6, 5, 0, tzinfo=UTC).timestamp() * 1000
        )  # ET midnight bar stamp
        return httpx.Response(200, json={"results": [{"t": bar_ms, "c": 735.29}]})

    s = src(h)
    assert s.upcoming_holidays()[0]["status"] == "closed"
    assert s.market_status()["market"] == "closed"
    assert s.daily_closes("SPY", date(2026, 11, 6), date(2026, 11, 6)) == {
        date(2026, 11, 6): D("735.29")
    }


def test_view_rejects_future_dividends_from_massive() -> None:
    class Leaky(MassiveSource):
        def dividends(self, symbol, known_as_of):  # type: ignore[no-untyped-def]
            return super().dividends(
                symbol, datetime(2100, 1, 1, tzinfo=UTC)
            )  # ignores known_as_of

    def h(req: httpx.Request) -> httpx.Response:
        row = {
            "cash_amount": 1,
            "declaration_date": "2026-11-06",
            "ex_dividend_date": "2026-11-20",
            "distribution_type": "recurring",
        }
        return httpx.Response(200, json={"results": [row]})

    leaky = Leaky("k", base_url="http://m", transport=httpx.MockTransport(h))
    with pytest.raises(LookaheadError):
        PointInTimeView(leaky, TS).dividends("KO")


def test_prefetch_batches_dividends_and_splits_and_serves_from_cache() -> None:
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        tickers = req.url.params["ticker.any_of"].split(",")
        if req.url.path == "/stocks/v1/dividends":
            rows = []
            if "SPY" in tickers:  # schedule-declared: two ex-dates share one declaration
                rows += [
                    {
                        "ticker": "SPY",
                        "cash_amount": 1.8,
                        "declaration_date": "2026-01-02",
                        "ex_dividend_date": d,
                        "distribution_type": "recurring",
                    }
                    for d in ("2026-03-20", "2026-06-18")
                ]
            if "KO" in tickers:
                rows.append(
                    {
                        "ticker": "KO",
                        "cash_amount": 0.53,
                        "declaration_date": "2026-07-15",
                        "ex_dividend_date": "2026-09-15",
                        "distribution_type": "recurring",
                    }
                )
            return httpx.Response(200, json={"results": rows})
        if req.url.path == "/stocks/v1/splits":
            rows = (
                [
                    {
                        "ticker": "NVDA",
                        "execution_date": "2024-06-10",
                        "split_from": 1,
                        "split_to": 10,
                        "adjustment_type": "forward_split",
                    }
                ]
                if "NVDA" in tickers
                else []
            )
            return httpx.Response(200, json={"results": rows})
        raise AssertionError(f"unexpected request {req.url}")  # no per-symbol calls, no events

    symbols = ["SPY", "KO", "NVDA"] + [f"S{i:02d}" for i in range(27)]  # 30 symbols
    s = src(h, include_ticker_events=False)
    s.prefetch(symbols)
    assert len(seen) == 4  # 2 batches x (dividends + splits)
    assert all(len(r.url.params["ticker.any_of"].split(",")) <= 25 for r in seen)
    later = datetime(2026, 11, 6, 21, tzinfo=UTC)
    assert [d.ex_date for d in s.dividends("KO", later)] == [date(2026, 9, 15)]
    assert len(s.schedule_declared) == 2  # SPY detection works inside a mixed batch
    assert [a.action_type for a in s.corporate_actions("NVDA", later)] == [
        CorporateActionType.SPLIT
    ]
    assert s.dividends("S05", later) == () and s.corporate_actions("S05", later) == ()
    assert len(seen) == 4  # everything above came from cache
    s.prefetch(symbols)
    assert len(seen) == 4  # already cached: no new requests


def test_ticker_events_flag_controls_per_symbol_event_calls() -> None:
    paths: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if "events" in req.url.path:
            return httpx.Response(200, json={"results": {"events": []}})
        return httpx.Response(200, json={"results": []})

    src(h, include_ticker_events=False).corporate_actions("KO", TS)
    assert not any("events" in p for p in paths)
    src(h).corporate_actions("KO", TS)
    assert any("events" in p for p in paths)
