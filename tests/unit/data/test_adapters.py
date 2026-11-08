"""ThetaData and Polygon adapters against mocked HTTP (no network)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from remy_finance.core import CorporateActionType, DividendType, QualityFlag, Right
from remy_finance.data import CompositeSource, FixtureSource
from remy_finance.data.polygon import PolygonError, PolygonSource
from remy_finance.data.thetadata import ThetaDataError, ThetaDataSource, UnsupportedDataError
from tests import factories as f

D = Decimal
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TS = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # 10:00 ET


def theta(handler: object) -> ThetaDataSource:
    return ThetaDataSource("http://t", transport=httpx.MockTransport(handler), clock=lambda: NOW)  # type: ignore[arg-type]


def test_theta_chain_quotes_mapping_and_params() -> None:
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        body = (
            '{"response":[{"contract":{"symbol":"SPY","strike":740.5,"right":"CALL",'
            '"expiration":"2026-11-20"},"data":[{"bid":1.23,"ask":1.27,"bid_size":5,'
            '"ask_size":7,"timestamp":"2026-11-06T09:59:55.898"}]}]}'
        )
        return httpx.Response(200, text=body)

    (q,) = theta(h).chain_quotes_at("SPY", TS)
    assert (
        seen["time_of_day"] == "10:00:00.000"
        and seen["start_date"] == seen["end_date"] == "20261106"
    )
    assert seen["expiration"] == "*"
    assert q.bid == D("1.23") and isinstance(q.bid, Decimal) and q.contract.strike == D("740.5")
    assert q.contract.right is Right.CALL and q.contract.standard_deliverable
    assert q.quote_time == datetime(2026, 11, 6, 14, 59, 55, 898000, tzinfo=UTC)
    assert q.ingest_time == NOW


def test_theta_drops_prior_session_rows_and_marks_adjusted_roots() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        rows = [
            {
                "contract": {
                    "symbol": "AAPL1",
                    "strike": 100,
                    "right": "PUT",
                    "expiration": "2026-11-20",
                },
                "data": [
                    {
                        "bid": 1,
                        "ask": 2,
                        "bid_size": 1,
                        "ask_size": 1,
                        "timestamp": "2026-11-06T09:31:00",
                    }
                ],
            },
            {
                "contract": {
                    "symbol": "AAPL",
                    "strike": 100,
                    "right": "PUT",
                    "expiration": "2026-11-20",
                },
                "data": [
                    {
                        "bid": 1,
                        "ask": 2,
                        "bid_size": 1,
                        "ask_size": 1,
                        "timestamp": "2026-11-05T15:59:00",
                    }
                ],
            },
        ]
        return httpx.Response(200, json={"response": rows})

    (q,) = theta(h).chain_quotes_at("AAPL", TS)
    assert q.contract.root == "AAPL1" and not q.contract.standard_deliverable


def test_theta_underlying_quote_flagged_request_time() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"response":[{"bid":740.5,"ask":740.71,"bid_size":120,"ask_size":80,"timestamp":"2026-11-06T10:00:00.000"}]}',
        )

    q = theta(h).underlying_quote_at("SPY", TS)
    assert q is not None and q.bid == D("740.5") and q.quote_time == TS
    assert q.quality_flags == (QualityFlag.QUOTE_TIME_IS_REQUEST_TIME,)


def test_theta_rates_percent_weekends_and_public_time() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        rows = [
            {"rate": 3.92, "created": "2026-10-30"},
            {"rate": 3.92, "created": "2026-10-31"},  # Fri, Sat
            {"rate": 3.91, "created": "2026-11-05"},
        ]  # Thu: public Fri 09:30 ET
        return httpx.Response(200, json={"response": rows})

    rates = theta(h).rates(TS)
    assert [(r.observation_date, r.rate) for r in rates] == [
        (date(2026, 10, 30), D("0.0392")),
        (date(2026, 11, 5), D("0.0391")),
    ]
    assert rates[1].public_time == datetime(2026, 11, 6, 14, 30, tzinfo=UTC)
    early = theta(h).rates(
        datetime(2026, 11, 6, 14, 0, tzinfo=UTC)
    )  # 09:00 ET: Thu's rate not yet public
    assert [r.observation_date for r in early] == [date(2026, 10, 30)]


def test_theta_errors() -> None:
    assert theta(lambda r: httpx.Response(472, text="NO_DATA")).chain_quotes_at("SPY", TS) == ()
    with pytest.raises(ThetaDataError) as e:
        theta(lambda r: httpx.Response(403, text="FREE subscription")).underlying_quote_at(
            "SPY", TS
        )
    assert e.value.status == 403

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(ThetaDataError, match="unreachable"):
        theta(boom).rates(TS)
    with pytest.raises(UnsupportedDataError):
        theta(boom).dividends("KO", TS)


def polygon(handler: object) -> PolygonSource:
    return PolygonSource(
        "k", base_url="http://p", transport=httpx.MockTransport(handler), clock=lambda: NOW
    )  # type: ignore[arg-type]


def test_polygon_dividends_public_time_pagination_and_skips() -> None:
    calls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        assert req.headers["Authorization"] == "Bearer k"
        if "cursor" not in str(req.url):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "cash_amount": 0.51,
                            "declaration_date": "2026-10-16",
                            "ex_dividend_date": "2026-11-28",
                            "pay_date": "2026-12-15",
                            "dividend_type": "CD",
                        }
                    ],
                    "next_url": "http://p/v3/reference/dividends?cursor=abc",
                },
            )
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "results": [
                        {
                            "cash_amount": 1.0,
                            "declaration_date": "2026-11-06",
                            "ex_dividend_date": "2026-11-20",
                            "dividend_type": "SC",
                        },
                        {
                            "cash_amount": 0.5,
                            "ex_dividend_date": "2026-09-01",
                            "dividend_type": "CD",
                        },
                    ]
                }
            ),
        )

    src = polygon(h)
    divs = src.dividends("KO", TS)  # 2026-11-06 declaration at 16:00 ET is after TS (10:00 ET)
    assert [(d.amount, d.dividend_type) for d in divs] == [(D("0.51"), DividendType.ORDINARY)]
    assert divs[0].public_time == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    later = datetime(2026, 11, 6, 21, 0, tzinfo=UTC)
    assert len(src.dividends("KO", later)) == 2
    assert src.skipped == ["KO ex 2026-09-01: no declaration_date"]
    assert len(calls) == 2  # cached after the first load


def test_polygon_splits_and_special_dividend_actions() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if "splits" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"execution_date": "2026-06-10", "split_from": 1, "split_to": 4},
                        {"execution_date": "2025-01-02", "split_from": 10, "split_to": 1},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "cash_amount": 2.5,
                        "declaration_date": "2026-05-01",
                        "ex_dividend_date": "2026-05-20",
                        "dividend_type": "SC",
                    }
                ]
            },
        )

    acts = polygon(h).corporate_actions("XYZ", TS)
    kinds = sorted(a.action_type.value for a in acts)
    assert kinds == ["REVERSE_SPLIT", "SPECIAL_DIVIDEND", "SPLIT"]
    split = next(a for a in acts if a.action_type is CorporateActionType.SPLIT)
    assert split.public_time == datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # 09:30 EDT
    assert dict(split.details) == {"split_from": "1", "split_to": "4"}


def test_polygon_requires_key_and_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    with pytest.raises(PolygonError, match="POLYGON_API_KEY"):
        PolygonSource()
    with pytest.raises(PolygonError, match="401"):
        polygon(lambda r: httpx.Response(401, text="bad key")).dividends("KO", TS)


def test_fixture_round_trip_and_composite(tmp_path: Path) -> None:
    src = FixtureSource([f.option_quote(), f.underlying_quote(), f.dividend(), f.rate()])
    path = tmp_path / "fixture.jsonl"
    src.dump(path)
    again = FixtureSource.load(path)
    assert [r.content_hash() for r in again.records()] == [r.content_hash() for r in src.records()]
    comp = CompositeSource(quotes=again, events=again)
    assert comp.dividends("XYZ", f.AS_OF) and comp.rates(f.AS_OF)
    assert comp.list_contracts("XYZ", f.AS_OF)[0].osi_symbol == f.contract().osi_symbol


def test_polygon_default_base_url_is_massive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLYGON_BASE_URL", raising=False)
    seen: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        return httpx.Response(200, json={"results": []})

    PolygonSource("k", transport=httpx.MockTransport(h)).dividends("KO", TS)
    assert seen == ["api.massive.com"]
