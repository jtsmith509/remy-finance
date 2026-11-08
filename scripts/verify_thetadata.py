#!/usr/bin/env python3
"""R0.1 data-source verification spike for ThetaData (handoff Section 5, R0.1).

Runs against a local Theta Terminal v3 (default http://127.0.0.1:25503) and reports
PASS / PARTIAL / FAIL with evidence for the five data-contract items:

  1. Bid/ask sizes on historical and live option quotes
  2. Underlying prices at the same timestamps
  3. Dividends and corporate actions with public-time information
  4. Time-of-day historical retrieval of the full-chain NBBO across a date range
  5. Expired contracts (chains and quotes)

plus extras: interest rates, index EOD, concurrency/latency, storage per snapshot.

This script must run on the machine where the Theta Terminal runs (the Terminal only
listens on localhost). It never sends credentials: the Terminal holds the login.

Usage (on the Mac mini, from the repo root):

    uv run python scripts/verify_thetadata.py
    uv run python scripts/verify_thetadata.py --date 2026-11-06 --range-days 5 \
        --symbols SPY,AAPL,KO --out data/verification

Outputs a Markdown report on stdout and writes `<out>/r0_1_<timestamp>.json` and `.md`.
Paste or commit the Markdown into docs/memos/R0.1-data-verification.md (Appendix).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503")
DEFAULT_SYMBOLS = ("SPY", "AAPL", "KO")  # ETF, megacap, dividend payer
SNAP_TIMES = ("10:00:00.000", "15:45:00.000")
EXEC_TIMES = ("10:05:00.000", "15:50:00.000")
MAX_DTE = 60  # MOVEGEN-1 upper bound (DR-0008)
UNIVERSE_SIZE = 50

PASS, PARTIAL, FAIL, ERROR, SKIP = "PASS", "PARTIAL", "FAIL", "ERROR", "SKIP"


# --------------------------------------------------------------------------- HTTP


@dataclass
class Resp:
    path: str
    params: dict[str, str]
    status: int | None
    elapsed_s: float
    n_bytes: int
    rows: list[dict[str, Any]]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None

    def brief(self) -> dict[str, Any]:
        return {
            "request": f"{self.path}?{httpx.QueryParams(self.params)}",
            "status": self.status,
            "elapsed_s": round(self.elapsed_s, 3),
            "bytes": self.n_bytes,
            "rows": len(self.rows),
            "error": self.error,
            "sample": self.rows[:2],
        }


def flatten(payload: Any) -> list[dict[str, Any]]:
    """Flatten ThetaData v3 JSON into rows.

    Per-contract endpoints return {"response": [{"contract": {...}, "data": [{...}]}]};
    others return {"response": [{...}]}.
    """
    items = payload.get("response", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and "data" in item and isinstance(item["data"], list):
            contract = item.get("contract") or {}
            for d in item["data"]:
                rows.append({**contract, **d})
        elif isinstance(item, dict):
            rows.append(item)
    return rows


class Theta:
    def __init__(self, base_url: str, timeout: float = 300.0, transport: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self.log: list[dict[str, Any]] = []

    def get(self, path: str, **params: str | int) -> Resp:
        p = {k: str(v) for k, v in params.items()}
        p.setdefault("format", "json")
        t0 = time.perf_counter()
        try:
            r = self.client.get(path, params=p)
        except httpx.HTTPError as e:
            resp = Resp(path, p, None, time.perf_counter() - t0, 0, [], f"{type(e).__name__}: {e}")
            self.log.append(resp.brief())
            return resp
        elapsed = time.perf_counter() - t0
        rows: list[dict[str, Any]] = []
        error = None
        if r.status_code == 200:
            try:
                rows = flatten(r.json())
            except ValueError:
                error = f"non-JSON body: {r.text[:200]!r}"
        else:
            error = r.text[:300].strip() or r.reason_phrase
        resp = Resp(path, p, r.status_code, elapsed, len(r.content), rows, error)
        self.log.append(resp.brief())
        return resp


# ------------------------------------------------------------------------ results


@dataclass
class Check:
    id: str
    title: str
    status: str = ERROR
    summary: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def guarded(fn: Callable[..., Check]) -> Callable[..., Check]:
    def wrapper(*a: Any, **k: Any) -> Check:
        try:
            return fn(*a, **k)
        except Exception as e:  # report, never crash the whole run
            return Check(fn.__name__, fn.__name__, ERROR, f"{type(e).__name__}: {e}")

    return wrapper


# ------------------------------------------------------------------------ helpers


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def trading_days(end: date, n: int) -> list[date]:
    """Last n XNYS sessions ending at or before `end` (falls back to weekdays)."""
    try:
        import exchange_calendars as xcals

        cal = xcals.get_calendar("XNYS")
        sessions = cal.sessions_in_range(end - timedelta(days=n * 3 + 10), end)
        return [s.date() for s in sessions[-n:]]
    except Exception:
        out: list[date] = []
        d = end
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return sorted(out)


def num(x: Any) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def parse_ts(x: Any) -> datetime | None:
    if not isinstance(x, str) or not x:
        return None
    try:
        return datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return None


def size_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    has_fields = n > 0 and all(k in rows[0] for k in ("bid_size", "ask_size"))
    two_sided = [r for r in rows if (num(r.get("bid")) or 0) > 0 and (num(r.get("ask")) or 0) > 0]
    sized = [
        r
        for r in two_sided
        if (num(r.get("bid_size")) or 0) > 0 and (num(r.get("ask_size")) or 0) > 0
    ]
    return {
        "rows": n,
        "size_fields_present": has_fields,
        "two_sided_quotes": len(two_sided),
        "two_sided_with_positive_sizes": len(sized),
        "pct_sized_of_two_sided": round(100 * len(sized) / len(two_sided), 2)
        if two_sided
        else None,
        "median_bid_size": statistics.median([num(r["bid_size"]) or 0 for r in sized])
        if sized
        else None,
        "median_ask_size": statistics.median([num(r["ask_size"]) or 0 for r in sized])
        if sized
        else None,
    }


def staleness(rows: list[dict[str, Any]], tod: str) -> dict[str, Any]:
    """Distribution of (requested time - quote timestamp) in seconds, per row."""
    hh, mm, ss = tod.split(":")
    target_s = int(hh) * 3600 + int(mm) * 60 + float(ss)
    ages: list[float] = []
    future = 0
    dates: set[str] = set()
    for r in rows:
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        dates.add(ts.date().isoformat())
        sec = ts.hour * 3600 + ts.minute * 60 + ts.second + ts.microsecond / 1e6
        age = target_s - sec
        if age < -0.001:
            future += 1
        ages.append(age)
    if not ages:
        return {"parsed": 0}
    ages.sort()

    def q(p: float) -> float:
        return round(ages[min(len(ages) - 1, int(p * len(ages)))], 3)

    return {
        "parsed": len(ages),
        "distinct_dates": sorted(dates),
        "rows_stamped_after_requested_time": future,
        "age_s_p50": q(0.5),
        "age_s_p90": q(0.9),
        "age_s_p99": q(0.99),
        "age_s_max": round(ages[-1], 3),
    }


def parquet_bytes(rows: list[dict[str, Any]]) -> int | None:
    try:
        import polars as pl
    except ImportError:
        return None
    if not rows:
        return 0
    df = pl.DataFrame(rows, infer_schema_length=None)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "chain.parquet"
        df.write_parquet(p, compression="zstd")
        return p.stat().st_size


def expirations(t: Theta, sym: str) -> tuple[Resp, list[date]]:
    r = t.get("/v3/option/list/expirations", symbol=sym)
    exps: list[date] = []
    for row in r.rows:
        v = str(row.get("expiration", "")).replace("-", "")
        if len(v) == 8 and v.isdigit():
            exps.append(datetime.strptime(v, "%Y%m%d").date())  # noqa: DTZ007
    return r, sorted(exps)


# ------------------------------------------------------------------------- checks


@guarded
def check_connectivity(t: Theta, syms: list[str]) -> Check:
    c = Check("0", "Terminal reachable on localhost")
    r1 = t.get("/v3/option/list/expirations", symbol=syms[0])
    r2 = t.get("/v3/calendar/today")
    c.evidence = [r1.brief(), r2.brief()]
    if r1.status is None:
        c.status, c.summary = FAIL, f"Cannot reach Theta Terminal at {t.base_url}: {r1.error}"
    elif r1.ok:
        c.status, c.summary = (
            PASS,
            f"Terminal answered at {t.base_url} ({len(r1.rows)} expirations).",
        )
    else:
        c.status, c.summary = FAIL, f"Terminal reachable but returned {r1.status}: {r1.error}"
    return c


@guarded
def check_sizes(t: Theta, syms: list[str], d: date) -> Check:
    c = Check("1", "Bid/ask sizes on historical and live option quotes")
    hist_ok = live_ok = True
    for s in syms:
        rh = t.get(
            "/v3/option/at_time/quote",
            symbol=s,
            expiration="*",
            start_date=ymd(d),
            end_date=ymd(d),
            time_of_day=SNAP_TIMES[0],
            max_dte=MAX_DTE,
        )
        st = size_stats(rh.rows)
        c.evidence.append(
            {"symbol": s, "kind": "historical at_time 10:00", **rh.brief(), "stats": st}
        )
        hist_ok &= rh.ok and st["size_fields_present"] and (st["two_sided_with_positive_sizes"] > 0)

        rl = t.get("/v3/option/snapshot/quote", symbol=s, expiration="*", max_dte=MAX_DTE)
        sl = size_stats(rl.rows)
        ts = [parse_ts(r.get("timestamp")) for r in rl.rows]
        ts_ok = [x for x in ts if x]
        c.evidence.append(
            {
                "symbol": s,
                "kind": "live snapshot",
                **rl.brief(),
                "stats": sl,
                "latest_quote_ts": max(ts_ok).isoformat() if ts_ok else None,
            }
        )
        live_ok &= rl.ok and sl["size_fields_present"] and (sl["two_sided_with_positive_sizes"] > 0)
    if hist_ok and live_ok:
        c.status, c.summary = PASS, "bid_size/ask_size present and populated, historical and live."
    elif hist_ok or live_ok:
        c.status = PARTIAL
        c.summary = f"historical={'ok' if hist_ok else 'FAIL'}, live={'ok' if live_ok else 'FAIL'}."
    else:
        c.status, c.summary = FAIL, "Sizes missing or requests failed; see evidence."
    c.notes.append("Live snapshot outside market hours returns the last quote (up to 7 days old).")
    return c


@guarded
def check_underlying(t: Theta, syms: list[str], d: date) -> Check:
    c = Check("2", "Underlying prices at the same timestamps")
    nbbo_at_time = greeks_px = eod_ok = True
    for s in syms:
        # (a) Underlying NBBO at time of day: requires Stock Value+ (expected to fail on FREE).
        ra = t.get(
            "/v3/stock/at_time/quote",
            symbol=s,
            start_date=ymd(d),
            end_date=ymd(d),
            time_of_day=SNAP_TIMES[0],
        )
        c.evidence.append({"symbol": s, "kind": "stock at_time quote 10:00", **ra.brief()})
        nbbo_at_time &= ra.ok and len(ra.rows) > 0

        # (b) Stock EOD (FREE): close + closing NBBO, for EXP-1 official close.
        rb = t.get("/v3/stock/history/eod", symbol=s, start_date=ymd(d), end_date=ymd(d))
        c.evidence.append({"symbol": s, "kind": "stock EOD", **rb.brief()})
        eod_ok &= rb.ok and len(rb.rows) > 0

        # (c) Underlying price embedded in Options Standard greeks at 10:00 (nearest expiry).
        _, exps = expirations(t, s)
        fut = [e for e in exps if e >= d + timedelta(days=7)]
        if not fut:
            greeks_px = False
            continue
        rc = t.get(
            "/v3/option/history/greeks/first_order",
            symbol=s,
            expiration=ymd(fut[0]),
            date=ymd(d),
            interval="1m",
            start_time="09:59:00.000",
            end_time="10:01:00.000",
        )
        upx = [
            (r.get("underlying_timestamp"), r.get("underlying_price"), r.get("timestamp"))
            for r in rc.rows
            if num(r.get("underlying_price"))
        ]
        c.evidence.append(
            {
                "symbol": s,
                "kind": "greeks first_order 09:59-10:01 (underlying_price)",
                **rc.brief(),
                "underlying_samples": upx[:5],
            }
        )
        greeks_px &= rc.ok and len(upx) > 0
    if nbbo_at_time:
        c.status, c.summary = PASS, "Underlying NBBO available at the snapshot times."
    elif greeks_px:
        c.status = PARTIAL
        c.summary = (
            "No underlying bid/ask at time of day on this tier; a single underlying_price "
            "(with underlying_timestamp) is available via Options Standard greeks."
        )
    else:
        c.status, c.summary = FAIL, "No intraday underlying price on this subscription."
    c.notes.append(f"Stock EOD (close, closing NBBO) available: {eod_ok}.")
    return c


@guarded
def check_dividends(t: Theta, syms: list[str], d: date) -> Check:
    c = Check("3", "Dividends and corporate actions with public_time")
    s = syms[-1]
    candidates = [
        "/v3/stock/history/dividends",
        "/v3/stock/list/dividends",
        "/v3/stock/history/dividend",
        "/v3/stock/history/splits",
        "/v3/stock/history/corporate_actions",
        "/v3/stock/list/corporate_actions",
        "/v3/corporate_actions",
        "/v3/dividends",
    ]
    found = []
    for p in candidates:
        r = t.get(p, symbol=s, start_date=ymd(d - timedelta(days=365)), end_date=ymd(d))
        c.evidence.append(r.brief())
        if r.ok and r.rows:
            found.append(p)
    if found:
        fields = set().union(
            *(set(e.get("sample", [{}])[0].keys()) for e in c.evidence if e.get("rows"))
        )
        has_decl = any("declar" in f or "announce" in f for f in fields)
        c.status = PASS if has_decl else PARTIAL
        c.summary = f"Endpoints {found}; declaration-date field present: {has_decl}."
    else:
        c.status = FAIL
        c.summary = (
            "No dividend or corporate-action endpoint on ThetaData v3. A supplementary "
            "source is required for ASSIGN-1 dividend rule and for corporate actions."
        )
    return c


@guarded
def check_time_of_day(t: Theta, syms: list[str], days: list[date]) -> Check:
    c = Check("4", "Time-of-day full-chain NBBO across a date range")
    ok = True
    for s in syms[:2]:
        for tod in SNAP_TIMES + EXEC_TIMES[:1]:
            r = t.get(
                "/v3/option/at_time/quote",
                symbol=s,
                expiration="*",
                start_date=ymd(days[0]),
                end_date=ymd(days[-1]),
                time_of_day=tod,
                max_dte=MAX_DTE,
            )
            stl = staleness(r.rows, tod)
            c.evidence.append({"symbol": s, "time_of_day": tod, **r.brief(), "staleness": stl})
            ok &= (
                r.ok
                and len(r.rows) > 0
                and stl.get("rows_stamped_after_requested_time", 1) == 0
                and len(stl.get("distinct_dates", [])) == len(days)
            )
    c.status = PASS if ok else FAIL
    c.summary = (
        f"One request per (symbol, time) returned the chain for all {len(days)} sessions "
        "with no quote stamped after the requested time."
        if ok
        else "Range retrieval failed or returned rows after the requested time; see evidence."
    )
    c.notes.append("Any row stamped after the requested time would be a look-ahead defect.")
    return c


@guarded
def check_expired(t: Theta, syms: list[str], today: date) -> Check:
    c = Check("5", "Expired contracts: chains and quotes")
    ok = True
    for s in syms:
        rexp, exps = expirations(t, s)
        past = [e for e in exps if e <= today - timedelta(days=30)]
        c.evidence.append(
            {
                "symbol": s,
                "kind": "list expirations",
                **rexp.brief() | {"sample": []},
                "n_expirations": len(exps),
                "n_expired_>=30d": len(past),
                "earliest": exps[0].isoformat() if exps else None,
            }
        )
        if not past:
            ok = False
            continue
        e = past[-1]
        rs = t.get("/v3/option/list/strikes", symbol=s, expiration=ymd(e))
        qd = trading_days(e - timedelta(days=7), 1)[0]
        rq = t.get(
            "/v3/option/at_time/quote",
            symbol=s,
            expiration=ymd(e),
            start_date=ymd(qd),
            end_date=ymd(qd),
            time_of_day=SNAP_TIMES[1],
        )
        c.evidence.append(
            {"symbol": s, "expired_expiration": e.isoformat(), "kind": "strikes", **rs.brief()}
        )
        c.evidence.append(
            {
                "symbol": s,
                "expired_expiration": e.isoformat(),
                "kind": f"quotes on {qd} 15:45",
                **rq.brief(),
                "stats": size_stats(rq.rows),
            }
        )
        ok &= rs.ok and len(rs.rows) > 0 and rq.ok and len(rq.rows) > 0
    rc = t.get(
        "/v3/option/list/contracts/quote",
        date=ymd(trading_days(today - timedelta(days=60), 1)[0]),
        symbol=syms[0],
    )
    c.evidence.append({"kind": "list contracts quoted on a past date", **rc.brief()})
    c.status = PASS if ok else FAIL
    c.summary = (
        "Expired expirations, strikes and historical quotes retrievable."
        if ok
        else "Expired-contract retrieval failed for at least one symbol."
    )
    return c


@guarded
def check_rates_index(t: Theta, d: date) -> Check:
    c = Check("X1", "Interest rates and index EOD (FREE tiers)")
    got_rate = False
    for path in ("/v3/rate/history/eod", "/v3/interest_rate/history/eod"):
        for sym in ("SOFR", "TREASURY_M3"):
            r = t.get(path, symbol=sym, start_date=ymd(d - timedelta(days=14)), end_date=ymd(d))
            c.evidence.append(r.brief())
            got_rate |= r.ok and len(r.rows) > 0
        if got_rate:
            break
    ri = t.get("/v3/index/history/eod", symbol="SPX", start_date=ymd(d), end_date=ymd(d))
    c.evidence.append(ri.brief())
    c.status = PASS if got_rate else FAIL
    c.summary = (
        f"rates={'ok' if got_rate else 'unavailable'}; SPX EOD={'ok' if ri.ok else ri.status}."
    )
    c.notes.append("Check rate units (percent vs decimal) and publication timing in the sample.")
    return c


@guarded
def check_concurrency(t: Theta, syms: list[str], d: date) -> Check:
    c = Check("X2", "Concurrency and latency")
    for n in (1, 2, 4, 8):

        def one(_: int) -> Resp:
            return t.get(
                "/v3/option/at_time/quote",
                symbol=syms[0],
                expiration="*",
                start_date=ymd(d),
                end_date=ymd(d),
                time_of_day=SNAP_TIMES[1],
                max_dte=MAX_DTE,
            )

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as ex:
            res = list(ex.map(one, range(n)))
        wall = time.perf_counter() - t0
        c.evidence.append(
            {
                "parallel": n,
                "wall_s": round(wall, 3),
                "statuses": [r.status for r in res],
                "per_request_s": [round(r.elapsed_s, 3) for r in res],
            }
        )
    errs = [e for e in c.evidence if any(s != 200 for s in e["statuses"])]
    c.status = PASS if not errs else PARTIAL
    c.summary = (
        "No throttling errors up to 8 parallel requests."
        if not errs
        else "Some parallel requests returned non-200; see evidence (likely concurrency cap)."
    )
    c.notes.append(
        "ThetaData caps concurrency per tier rather than request rate; excess requests "
        "queue in the Terminal, so watch wall-clock scaling, not only status codes."
    )
    return c


@guarded
def check_storage(t: Theta, syms: list[str], d: date) -> Check:
    c = Check("X3", "Storage per snapshot (Parquet, zstd)")
    per: list[int] = []
    for s in syms:
        for label, extra in (("max_dte=60", {"max_dte": MAX_DTE}), ("all expirations", {})):
            r = t.get(
                "/v3/option/at_time/quote",
                symbol=s,
                expiration="*",
                start_date=ymd(d),
                end_date=ymd(d),
                time_of_day=SNAP_TIMES[0],
                **extra,
            )
            pb = parquet_bytes(r.rows)
            c.evidence.append(
                {
                    "symbol": s,
                    "scope": label,
                    "rows": len(r.rows),
                    "json_bytes": r.n_bytes,
                    "parquet_bytes": pb,
                    "status": r.status,
                }
            )
            if label == "max_dte=60" and pb:
                per.append(pb)
    if per:
        mean = statistics.mean(per)
        snap = mean * UNIVERSE_SIZE
        daily = snap * 4  # 2 snapshots + 2 execution quotes (DR-0003)
        c.status = PASS
        c.summary = (
            f"Mean {mean / 1e3:.0f} kB per underlying (≤60 DTE); est. "
            f"{snap / 1e6:.1f} MB per 50-name snapshot, {daily / 1e6:.1f} MB/day, "
            f"{daily * 252 / 1e9:.2f} GB/year (option quotes only)."
        )
        c.notes.append(
            "Sample names skew large (SPY has the biggest chain); a 50-name mean is lower."
        )
    else:
        c.status, c.summary = FAIL, "Could not measure (no rows or polars missing)."
    return c


# ------------------------------------------------------------------------- report


def render_md(meta: dict[str, Any], checks: Iterable[Check]) -> str:
    lines = ["# R0.1 ThetaData verification run", ""]
    lines += [f"- **{k}:** {v}" for k, v in meta.items()]
    lines += ["", "| # | Check | Result | Summary |", "|---|---|---|---|"]
    checks = list(checks)
    for ch in checks:
        lines.append(f"| {ch.id} | {ch.title} | **{ch.status}** | {ch.summary} |")
    for ch in checks:
        lines += ["", f"## {ch.id}. {ch.title}: {ch.status}", "", ch.summary, ""]
        lines += [f"- Note: {n}" for n in ch.notes]
        lines += [
            "",
            "<details><summary>Evidence</summary>",
            "",
            "```json",
            json.dumps(ch.evidence, indent=1, default=str)[:20000],
            "```",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="ETF, megacap, dividend payer (comma-separated)",
    )
    ap.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Reference session (YYYY-MM-DD); default = previous XNYS session",
    )
    ap.add_argument("--range-days", type=int, default=5)
    ap.add_argument(
        "--out", default=os.path.join(os.environ.get("REMY_DATA_DIR", "data"), "verification")
    )
    ap.add_argument("--skip", default="", help="Comma-separated check ids to skip, e.g. X2,X3")
    a = ap.parse_args(argv)

    today = datetime.now(UTC).date()
    ref = a.date or trading_days(today - timedelta(days=1), 1)[0]
    days = trading_days(ref, a.range_days)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    t = Theta(a.url)

    meta = {
        "run_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "terminal": a.url,
        "symbols": ", ".join(syms),
        "reference_session": ref.isoformat(),
        "range": f"{days[0]} .. {days[-1]} ({len(days)} sessions)",
        "host": os.uname().nodename,
        "python": sys.version.split()[0],
    }

    checks = [check_connectivity(t, syms)]
    if checks[0].status != PASS:
        print(render_md(meta, checks))
        return 2
    plan: list[tuple[str, Callable[[], Check]]] = [
        ("1", lambda: check_sizes(t, syms, ref)),
        ("2", lambda: check_underlying(t, syms, ref)),
        ("3", lambda: check_dividends(t, syms, ref)),
        ("4", lambda: check_time_of_day(t, syms, days)),
        ("5", lambda: check_expired(t, syms, today)),
        ("X1", lambda: check_rates_index(t, ref)),
        ("X2", lambda: check_concurrency(t, syms, ref)),
        ("X3", lambda: check_storage(t, syms, ref)),
    ]
    for cid, fn in plan:
        if cid in skip:
            checks.append(Check(cid, cid, SKIP, "skipped by --skip"))
            continue
        print(f"running check {cid} ...", file=sys.stderr)
        checks.append(fn())

    md = render_md(meta, checks)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / f"r0_1_{stamp}.md").write_text(md)
    (out / f"r0_1_{stamp}.json").write_text(
        json.dumps(
            {"meta": meta, "checks": [asdict(c) for c in checks], "request_log": t.log},
            indent=1,
            default=str,
        )
    )
    print(md)
    print(f"wrote {out}/r0_1_{stamp}.{{md,json}}", file=sys.stderr)
    return 0 if all(c.status in (PASS, SKIP) for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
