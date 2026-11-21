"""Offline tests for scripts/verify_r2_data.py against a mocked Theta Terminal (no network)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vt = _load("verify_thetadata")
vr = _load("verify_r2_data")
END = date(2023, 1, 31)


def _days(start: str, end: str) -> list[date]:
    s, e = (
        date.fromisoformat(f"{start[:4]}-{start[4:6]}-{start[6:]}"),
        date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}"),
    )
    return [s + timedelta(days=i) for i in range((e - s).days + 1)]


def handler(missing_symbol: str = "", no_vix: bool = False) -> Any:
    def h(request: httpx.Request) -> httpx.Response:
        p, q = request.url.path, request.url.params
        sym = q.get("symbol", "")
        if p in ("/v3/stock/history/eod", "/v3/index/history/eod"):
            if (sym == missing_symbol) or (sym == "VIX" and no_vix):
                return httpx.Response(200, json={"response": []})
            rows = [
                {"created": f"{d.isoformat()}T17:15:00", "close": 100.0}
                for d in _days(q["start_date"], q["end_date"])
                if d.weekday() < 5
            ]
            return httpx.Response(200, json={"response": rows})
        if p == "/v3/interest_rate/history/eod":
            rows = [
                {"created": f"{d.isoformat()}T17:00:00", "rate": 4.3}
                for d in _days(q["start_date"], q["end_date"])
            ]
            return httpx.Response(200, json={"response": rows})
        if p == "/v3/option/history/eod":
            d = _days(q["start_date"], q["end_date"])[0]
            rows = [
                {
                    "contract": {
                        "symbol": sym,
                        "expiration": (d + timedelta(days=n)).isoformat(),
                        "strike": 100,
                        "right": "CALL",
                    },
                    "data": [{"bid": 1.0, "ask": 1.1, "volume": 5}],
                }
                for n in (14, 45)
            ]
            return httpx.Response(200, json={"response": rows})
        if p == "/v3/option/at_time/quote":
            rows = [
                {
                    "contract": {
                        "symbol": sym,
                        "expiration": "2026-01-16",
                        "strike": 100,
                        "right": "PUT",
                    },
                    "data": [{"bid": 1.0, "ask": 1.1, "bid_size": 5, "ask_size": 0 if k else 7}],
                }
                for k in range(2)
            ]
            return httpx.Response(200, json={"response": rows})
        return httpx.Response(404, text="not mocked")

    return h


def theta(**kw: Any) -> Any:
    return vt.Theta("http://terminal", transport=httpx.MockTransport(handler(**kw)))


def test_closes_coverage_flags_a_missing_symbol() -> None:
    ok = vr.h1_closes(theta(), ["SPY", "KO"], END)
    assert ok.status == vr.PASS and all(r["missing"] == 0 for r in ok.rows)
    gap = vr.h1_closes(theta(missing_symbol="KO"), ["SPY", "KO"], END)
    assert gap.status == vr.PARTIAL
    assert next(r for r in gap.rows if r["symbol"] == "KO")["present"] == 0


def test_vix_missing_suggests_the_spy_iv_fallback() -> None:
    r = vr.h2_vix(theta(no_vix=True), END)
    assert r.status == vr.FAIL and any("SPY's own 30-day ATM IV" in n for n in r.notes)


def test_iv_chains_need_two_sided_quotes_bracketing_30_days_and_volume() -> None:
    r = vr.h5_iv_chains(theta(), END)
    assert r.status == vr.PASS
    assert all(x["brackets_30d"] and x["volume_field"] for x in r.rows)
    assert "Full HIST-1 build" in r.summary


def test_replay_boards_count_quotes_with_sizes() -> None:
    r = vr.h6_replay_boards(theta())
    assert r.status == vr.PASS  # half of each sample's rows carry both sizes
    assert all(x["with_sizes"] == 1 and x["rows"] == 2 for x in r.rows)


def test_guarded_step_reports_errors_and_report_renders() -> None:
    @vr.guarded
    def boom() -> Any:
        raise RuntimeError("terminal down")

    r = boom()
    assert r.status == vr.ERROR and "terminal down" in r.summary
    ok = vr.h1_closes(theta(), ["SPY"], END)
    md = vr.render([ok, r], vr.datetime(2026, 11, 21, tzinfo=vr.UTC), ["SPY"])
    assert "## Coverage matrix" in md and "Every symbol complete." in md


def test_quarterly_dates_are_first_sessions() -> None:
    qs = vr.quarterly_dates(date(2025, 11, 1), date(2026, 10, 31))
    assert qs[0] == date(2025, 11, 3) and qs[1] == date(2026, 1, 2)
    assert len(qs) == 5
