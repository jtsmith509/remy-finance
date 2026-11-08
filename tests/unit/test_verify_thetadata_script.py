"""Offline tests for scripts/verify_thetadata.py against a mocked Theta Terminal (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_thetadata.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_thetadata", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_thetadata"] = mod
    spec.loader.exec_module(mod)
    return mod


vt = _load()
D = date(2026, 11, 6)


def _quote(strike: float, ts: str, bs: int = 10, as_: int = 12) -> dict[str, Any]:
    return {
        "bid": 1.0,
        "ask": 1.1,
        "bid_size": bs,
        "ask_size": as_,
        "timestamp": ts,
        "strike": strike,
    }


def _chain(ts: str) -> dict[str, Any]:
    return {
        "response": [
            {
                "contract": {
                    "symbol": "SPY",
                    "expiration": "2026-11-20",
                    "strike": k,
                    "right": "CALL",
                },
                "data": [_quote(k, ts)],
            }
            for k in (500.0, 505.0)
        ]
    }


def _handler(request: httpx.Request) -> httpx.Response:
    p, q = request.url.path, request.url.params
    if p == "/v3/option/list/expirations":
        return httpx.Response(
            200,
            json={
                "response": [
                    {"symbol": "SPY", "expiration": "2026-08-21"},
                    {"symbol": "SPY", "expiration": "2026-11-20"},
                ]
            },
        )
    if p == "/v3/option/at_time/quote":
        tod = q["time_of_day"][:8]
        return httpx.Response(200, json=_chain(f"{q['start_date'][:4]}-11-06T{tod}"))
    if p == "/v3/option/snapshot/quote":
        return httpx.Response(200, json=_chain("2026-11-06T15:59:59.5"))
    if p == "/v3/stock/at_time/quote":
        return httpx.Response(403, text="PERMISSION: subscription does not include this")
    if p == "/v3/stock/history/eod":
        return httpx.Response(
            200, json={"response": [{"close": 501.2, "bid": 501.1, "ask": 501.3}]}
        )
    if p == "/v3/option/history/greeks/first_order":
        return httpx.Response(
            200,
            json={
                "response": [
                    {
                        "contract": {},
                        "data": [
                            {
                                "timestamp": "2026-11-06T10:00:00",
                                "underlying_price": 501.0,
                                "underlying_timestamp": "2026-11-06T09:59:59.8",
                            }
                        ],
                    }
                ]
            },
        )
    return httpx.Response(404, text="Not Found")


def _theta() -> Any:
    return vt.Theta("http://terminal.test", transport=httpx.MockTransport(_handler))


def test_flatten_nested_and_flat() -> None:
    rows = vt.flatten(_chain("2026-11-06T10:00:00"))
    assert len(rows) == 2 and rows[0]["symbol"] == "SPY" and rows[0]["bid_size"] == 10
    assert vt.flatten({"response": [{"a": 1}]}) == [{"a": 1}]
    assert vt.flatten("garbage") == []


def test_staleness_flags_rows_after_requested_time() -> None:
    rows = [_quote(1, "2026-11-06T09:59:58"), _quote(2, "2026-11-06T10:00:01")]
    st = vt.staleness(rows, "10:00:00.000")
    assert st["rows_stamped_after_requested_time"] == 1


def test_size_stats_counts_positive_sizes() -> None:
    rows = [_quote(1, "x"), _quote(2, "x", bs=0)]
    st = vt.size_stats(rows)
    assert st["size_fields_present"] and st["two_sided_quotes"] == 2
    assert st["two_sided_with_positive_sizes"] == 1


def test_checks_against_mock_terminal() -> None:
    t = _theta()
    assert vt.check_connectivity(t, ["SPY"]).status == vt.PASS
    assert vt.check_sizes(t, ["SPY"], D).status == vt.PASS
    und = vt.check_underlying(t, ["SPY"], D)
    assert und.status == vt.PARTIAL  # no stock NBBO (403), but greeks underlying_price
    assert vt.check_dividends(t, ["SPY"], D).status == vt.FAIL
    assert vt.check_time_of_day(t, ["SPY"], [D]).status == vt.PASS


def test_unreachable_terminal_reports_fail() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    t = vt.Theta("http://terminal.test", transport=httpx.MockTransport(boom))
    c = vt.check_connectivity(t, ["SPY"])
    assert c.status == vt.FAIL and "Cannot reach" in c.summary
    json.dumps(c.evidence, default=str)
