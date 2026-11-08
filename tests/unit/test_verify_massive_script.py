"""Offline tests for scripts/verify_massive.py against a mocked Massive API (no network)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx

from remy_finance.data.massive import MassiveSource, utc_to_ns

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_massive.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_massive", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_massive"] = mod
    spec.loader.exec_module(mod)
    return mod


vm = _load()


def basic_plan(req: httpx.Request) -> httpx.Response:
    """Free-plan behaviour: reference/economy data served, NBBO quotes refused (403)."""
    p = req.url.path
    if p == "/stocks/v1/dividends":
        t = req.url.params["ticker"]
        rows = [
            {
                "cash_amount": 0.51,
                "declaration_date": "2026-10-16",
                "ex_dividend_date": "2026-11-28",
                "distribution_type": "recurring",
                "currency": "USD",
                "ticker": t,
            }
        ]
        return httpx.Response(200, json={"results": rows})
    if p == "/stocks/v1/splits":
        rows = (
            [
                {
                    "execution_date": "2024-06-10",
                    "split_from": 1,
                    "split_to": 10,
                    "adjustment_type": "forward_split",
                }
            ]
            if req.url.params["ticker"] == "NVDA"
            else []
        )
        return httpx.Response(200, json={"results": rows})
    if p.endswith("/events"):
        return httpx.Response(200, json={"results": {"events": []}})
    if p.startswith("/v3/quotes/"):
        return httpx.Response(
            403, json={"status": "NOT_AUTHORIZED", "message": "You are not entitled"}
        )
    if p == "/fed/v1/treasury-yields":
        return httpx.Response(
            200,
            json={
                "results": [{"date": "2026-11-05", "yield_1_month": 3.99, "yield_3_month": 3.95}]
            },
        )
    if p == "/v1/marketstatus/upcoming":
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2026-11-26",
                    "exchange": "NYSE",
                    "name": "Thanksgiving",
                    "status": "closed",
                },
                {
                    "date": "2026-11-27",
                    "exchange": "NYSE",
                    "name": "Thanksgiving",
                    "status": "early-close",
                    "open": "2026-11-27T14:30:00.000Z",
                    "close": "2026-11-27T18:00:00.000Z",
                },
            ],
        )
    if p == "/v1/marketstatus/now":
        return httpx.Response(
            200, json={"market": "closed", "serverTime": "2026-11-08T12:00:00-05:00"}
        )
    return httpx.Response(404)


def source(handler: object = basic_plan) -> MassiveSource:
    return MassiveSource("k", base_url="http://m", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_free_plan_classification() -> None:
    src = source()
    m1 = vm.m1_dividends(src, ["SPY", "KO"])
    m2 = vm.m2_splits_events(src, ["KO"], ["NVDA"])
    m3 = vm.m3_nbbo(src, ["SPY"], None)
    m4 = vm.m4_treasury(src, None)
    m5 = vm.m5_calendar(src)
    assert (m1.id, m1.status) == ("M1", vm.PASS)
    assert m2.status == vm.PASS and any(e["splits"] for e in m2.evidence if e["symbol"] == "NVDA")
    assert (m3.id, m3.status) == ("M3", vm.UPGRADE) and "Stock NBBO" in m3.title
    assert m4.status == vm.PASS
    assert m5.status == vm.PASS, m5.summary  # Thanksgiving + 13:00 ET half-day match XNYS
    md = vm.render({"run": "test"}, [m1, m2, m3, m4, m5])
    assert "NEEDS_UPGRADE" in md and "Stocks Advanced" in md


def test_nbbo_on_advanced_plan_reports_ages() -> None:
    def advanced(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/v3/quotes/"):
            lte = int(req.url.params["timestamp.lte"])
            row = {
                "bid_price": 100.0,
                "ask_price": 100.02,
                "bid_size": 3,
                "ask_size": 4,
                "sip_timestamp": lte - 1_500_000_000,
            }  # 1.5 s before the snapshot
            return httpx.Response(200, json={"results": [row]})
        return basic_plan(req)

    m3 = vm.m3_nbbo(source(advanced), ["SPY"], None)
    assert m3.status == vm.PASS and "median age 1.500s" in m3.summary


def test_calendar_disagreement_is_reported() -> None:
    def wrong(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/marketstatus/upcoming":
            return httpx.Response(
                200,
                json=[{"date": "2026-11-25", "exchange": "NYSE", "name": "x", "status": "closed"}],
            )  # a normal XNYS session
        return basic_plan(req)

    assert vm.m5_calendar(source(wrong)).status == vm.PARTIAL


def test_ns_round_trip() -> None:
    t = datetime(2026, 11, 6, 15, 0, 0, 123456, tzinfo=UTC)
    from remy_finance.data.massive import ns_to_utc

    assert ns_to_utc(utc_to_ns(t)) == t
