#!/usr/bin/env python3
"""R2.0 data-verification spike (R2 brief §3, B11): can the vendors supply what R2 needs?

Read-only. Runs on the Mac mini against the local Theta Terminal (options, stocks, index,
rates) and Massive (10-year yield, corporate actions). Never prints credentials: the
Terminal holds its login, and the Massive key is read from the environment or Keychain.

Checks, over the R2 windows (history from 2022-11-01; replay decisions 2025-11 to 2026-10):

  H1  Daily official closes: the 80 UNIV-CANDIDATES-1 names, SPY and the 11 sector SPDRs
      (the superset of every point-in-time member, so no survivorship bias)
  H2  VIX daily close (index EOD)
  H3  10-year Treasury yield (Massive)
  H4  Risk-free rates (ThetaData, as R1 uses)
  H5  EOD option chains for 30-day ATM IV (sample dates x names): coverage, bid/ask,
      expirations bracketing 30 days, volume (UNIV-1's input), bytes and time per request
  H6  Replay boards: at-time option quotes WITH displayed sizes at 10:00 / 10:05 and
      15:45 / 15:50 ET on sample replay dates
  H7  Corporate actions (Massive): splits and ticker changes inside the history window,
      i.e. the events HIST-1's as-of adjustment must handle (B10)

Then it extrapolates download size, storage and run time for the full build, and prints a
coverage matrix. Usage (on the Mac mini, from the repo root, Theta Terminal running):

    uv run python scripts/verify_r2_data.py --out data/verification

Writes ``<out>/r2_0_<timestamp>.md`` and ``.json``. Send the .md for the R2.0 memo.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_thetadata import DEFAULT_URL, Theta

HIST_START = date(2022, 11, 1)
REPLAY_START = date(2025, 11, 1)
REPLAY_END = date(2026, 10, 31)
SECTORS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
IV_SAMPLE_SYMBOLS = ("SPY", "AAPL", "KO", "XLE")
BOARD_SAMPLE_SYMBOLS = ("SPY", "AAPL")
BOARD_TIMES = ("10:00:00.000", "10:05:00.000", "15:45:00.000", "15:50:00.000")
R1_SECONDS_PER_BOARD = 42.0  # measured: replay boards built in 38-46 s (R0/R1 memos)

PASS, PARTIAL, FAIL, ERROR = "PASS", "PARTIAL", "FAIL", "ERROR"


@dataclass
class Result:
    id: str
    title: str
    status: str = ERROR
    summary: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)  # the coverage table
    notes: list[str] = field(default_factory=list)


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def sessions(start: date, end: date) -> list[date]:
    import exchange_calendars as xcals

    cal = xcals.get_calendar("XNYS")
    return [s.date() for s in cal.sessions_in_range(start.isoformat(), end.isoformat())]


def quarterly_dates(start: date, end: date) -> list[date]:
    """The first session of each quarter in [start, end]."""
    days = sessions(start, end)
    out: list[date] = []
    seen: set[tuple[int, int]] = set()
    for d in days:
        q = (d.year, (d.month - 1) // 3)
        if q not in seen:
            seen.add(q)
            out.append(d)
    return out


def row_date(row: dict[str, Any]) -> date | None:
    for key in ("created", "date", "timestamp", "last_trade"):
        v = row.get(key)
        if v:
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return None


def coverage(dates_found: set[date], expected: Sequence[date]) -> tuple[int, int, date | None]:
    """(sessions present, sessions missing, first date present)."""
    present = sum(1 for d in expected if d in dates_found)
    first = min(dates_found) if dates_found else None
    return present, len(expected) - present, first


def guarded(fn: Callable[..., Result]) -> Callable[..., Result]:
    def wrap(*a: Any, **k: Any) -> Result:
        try:
            return fn(*a, **k)
        except Exception as e:  # reported, never fatal: the spike continues
            r = Result("?", fn.__name__)
            r.status, r.summary = ERROR, f"{type(e).__name__}: {e}"[:300]
            return r

    return wrap


# ------------------------------------------------------------------ ThetaData checks


def eod_by_year(t: Theta, path: str, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    y0 = start
    while y0 <= end:
        y1 = min(date(y0.year, 12, 31), end)
        r = t.get(path, symbol=symbol, start_date=ymd(y0), end_date=ymd(y1))
        if r.ok:
            rows += r.rows
        y0 = y1 + timedelta(days=1)
    return rows


@guarded
def h1_closes(t: Theta, symbols: Sequence[str], end: date) -> Result:
    res = Result("H1", "Daily official closes (candidates, SPY, sector SPDRs)")
    expected = sessions(HIST_START, end)
    full = 0
    for s in symbols:
        rows = eod_by_year(t, "/v3/stock/history/eod", s, HIST_START, end)
        found = {d for r in rows if r.get("close") and (d := row_date(r))}
        present, missing, first = coverage(found, expected)
        full += missing == 0
        res.rows.append({"symbol": s, "present": present, "missing": missing, "first": str(first)})
    res.status = PASS if full == len(symbols) else PARTIAL if full else FAIL
    res.summary = f"{full} of {len(symbols)} symbols complete over {len(expected)} sessions."
    return res


@guarded
def h2_vix(t: Theta, end: date) -> Result:
    res = Result("H2", "VIX daily close (index EOD)")
    expected = sessions(HIST_START, end)
    rows = eod_by_year(t, "/v3/index/history/eod", "VIX", HIST_START, end)
    found = {d for r in rows if r.get("close") and (d := row_date(r))}
    present, missing, first = coverage(found, expected)
    res.rows.append({"symbol": "VIX", "present": present, "missing": missing, "first": str(first)})
    res.status = PASS if missing == 0 else PARTIAL if present else FAIL
    res.summary = f"VIX: {present} of {len(expected)} sessions."
    if not present:
        res.notes.append(
            "Fallback if VIX is unavailable: SPY's own 30-day ATM IV (derived in H5) as "
            "the volatility factor. That needs a DR."
        )
    return res


@guarded
def h4_rates(t: Theta, end: date) -> Result:
    res = Result("H4", "Risk-free rates (ThetaData interest_rate EOD)")
    expected = sessions(HIST_START, end)
    for sym in ("SOFR", "TREASURY_M3"):
        rows = eod_by_year(t, "/v3/interest_rate/history/eod", sym, HIST_START, end)
        found = {d for r in rows if r.get("rate") is not None and (d := row_date(r))}
        present, missing, first = coverage(found, expected)
        res.rows.append(
            {"symbol": sym, "present": present, "missing": missing, "first": str(first)}
        )
    ok = [r for r in res.rows if r["missing"] <= 5]
    res.status = PASS if len(ok) == len(res.rows) else PARTIAL if ok else FAIL
    res.summary = "; ".join(f"{r['symbol']}: {r['present']}/{len(expected)}" for r in res.rows)
    return res


@guarded
def h5_iv_chains(t: Theta, end: date) -> Result:
    res = Result("H5", "EOD option chains for 30-day ATM IV (sample)")
    dates = quarterly_dates(HIST_START, end)
    ok = 0
    sizes: list[int] = []
    secs: list[float] = []
    for s in IV_SAMPLE_SYMBOLS:
        for d in dates:
            r = t.get(
                "/v3/option/history/eod",
                symbol=s,
                expiration="*",
                start_date=ymd(d),
                end_date=ymd(d),
            )
            rows = r.rows if r.ok else []
            two_sided = [x for x in rows if (x.get("bid") or 0) > 0 and (x.get("ask") or 0) > 0]
            exps = set()
            for x in rows:
                with contextlib.suppress(ValueError):
                    exps.add(date.fromisoformat(str(x.get("expiration"))[:10]))
            dte = sorted((e - d).days for e in exps)
            bracket = any(v <= 30 for v in dte if v >= 7) and any(v >= 30 for v in dte)
            has_volume = any("volume" in x for x in rows)
            good = bool(two_sided) and bracket and has_volume
            ok += good
            sizes.append(r.n_bytes)
            secs.append(r.elapsed_s)
            res.rows.append(
                {
                    "symbol": s,
                    "date": d.isoformat(),
                    "rows": len(rows),
                    "two_sided": len(two_sided),
                    "brackets_30d": bracket,
                    "volume_field": has_volume,
                    "status": r.status,
                }
            )
    n = len(IV_SAMPLE_SYMBOLS) * len(dates)
    res.status = PASS if ok == n else PARTIAL if ok else FAIL
    full_requests = 92 * len(sessions(HIST_START, end))
    mb = statistics.mean(sizes) / 1e6 if sizes else 0.0
    sec = statistics.mean(secs) if secs else 0.0
    res.summary = (
        f"{ok} of {n} samples usable. Full HIST-1 build: about {full_requests:,} requests, "
        f"about {mb * full_requests / 1e3:,.0f} GB raw at {mb:.2f} MB each, and about "
        f"{sec * full_requests / 3600:,.1f} h at {sec:.1f} s each (sequential)."
    )
    res.notes.append("Raw size is the JSON response; HIST-1 stores only the derived ATM IV.")
    return res


@guarded
def h6_replay_boards(t: Theta) -> Result:
    res = Result("H6", "Replay boards: at-time option quotes with displayed sizes")
    dates = quarterly_dates(REPLAY_START, REPLAY_END)
    ok = 0
    for s in BOARD_SAMPLE_SYMBOLS:
        for d in dates:
            for tod in BOARD_TIMES:
                r = t.get(
                    "/v3/option/at_time/quote",
                    symbol=s,
                    expiration="*",
                    start_date=ymd(d),
                    end_date=ymd(d),
                    time_of_day=tod,
                )
                rows = r.rows if r.ok else []
                sized = [
                    x for x in rows if (x.get("bid_size") or 0) > 0 and (x.get("ask_size") or 0) > 0
                ]
                good = bool(rows) and len(sized) >= 0.5 * len(rows)
                ok += good
                res.rows.append(
                    {
                        "symbol": s,
                        "date": d.isoformat(),
                        "time": tod[:5],
                        "rows": len(rows),
                        "with_sizes": len(sized),
                        "status": r.status,
                    }
                )
    n = len(BOARD_SAMPLE_SYMBOLS) * len(dates) * len(BOARD_TIMES)
    boards = 4 * len(sessions(REPLAY_START, REPLAY_END))
    res.status = PASS if ok == n else PARTIAL if ok else FAIL
    res.summary = (
        f"{ok} of {n} samples have quotes, at least half of them two-sided with sizes. "
        f"Full replay: {boards} boards, about {boards * R1_SECONDS_PER_BOARD / 3600:.0f} h "
        f"to build at R1's {R1_SECONDS_PER_BOARD:.0f} s per board."
    )
    return res


# ------------------------------------------------------------------ Massive checks


@guarded
def h3_ten_year(end: date) -> Result:
    from remy_finance.data.massive import MassiveSource

    res = Result("H3", "10-year Treasury yield (Massive)")
    m = MassiveSource()
    expected = sessions(HIST_START, end)
    rows = m._paged(
        "/fed/v1/treasury-yields",
        {
            "date.gte": HIST_START.isoformat(),
            "date.lte": end.isoformat(),
            "sort": "date.asc",
            "limit": "5000",
        },
    )
    found = {date.fromisoformat(str(r["date"])) for r in rows if r.get("yield_10_year") is not None}
    present, missing, first = coverage(found, expected)
    res.rows.append(
        {"symbol": "yield_10_year", "present": present, "missing": missing, "first": str(first)}
    )
    res.status = PASS if missing <= 5 else PARTIAL if present else FAIL
    res.summary = f"10-year yield: {present} of {len(expected)} sessions."
    return res


@guarded
def h7_actions(symbols: Sequence[str], end: date) -> Result:
    from remy_finance.data.massive import MassiveSource

    res = Result("H7", "Corporate actions in the history window (Massive)")
    m = MassiveSource()
    m.prefetch(list(symbols))
    as_of = datetime.combine(end, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    n = 0
    for s in symbols:
        for a in m.corporate_actions(s, as_of):
            when = a.effective_date
            if HIST_START <= when <= end:
                n += 1
                res.rows.append(
                    {"symbol": s, "date": when.isoformat(), "action": str(a.action_type.value)}
                )
    res.status = PASS
    res.summary = (
        f"{n} splits or ticker changes between {HIST_START} and {end}; HIST-1's as-of "
        "adjustment must handle each (B10)."
    )
    return res


# ------------------------------------------------------------------ report


def render(results: list[Result], started: datetime, symbols: Sequence[str]) -> str:
    lines = [
        f"# R2.0 data verification: {started.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"- **History window:** {HIST_START} onward; **replay decisions:** "
        f"{REPLAY_START} to {REPLAY_END}",
        f"- **Symbols checked for closes:** {len(symbols)} (UNIV-CANDIDATES-1, SPY, sector SPDRs)",
        "",
        "## Coverage matrix",
        "",
        "| Check | Status | Summary |",
        "|---|---|---|",
    ]
    lines += [f"| {r.id} {r.title} | **{r.status}** | {r.summary} |" for r in results]
    for r in results:
        lines += ["", f"## {r.id} {r.title}: {r.status}", "", r.summary]
        lines += [f"- {n}" for n in r.notes]
        bad = [x for x in r.rows if x.get("missing", 0) or x.get("status") not in (None, 200)]
        show = bad if r.id in ("H1",) and bad else r.rows
        if show:
            keys = list(show[0])
            lines += ["", "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
            lines += ["| " + " | ".join(str(x.get(k, "")) for k in keys) + " |" for x in show[:120]]
            if len(show) > 120:
                lines.append(f"\n({len(show) - 120} more rows in the JSON)")
        if r.id == "H1" and not bad:
            lines.append("\nEvery symbol complete.")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default="data/verification")
    ap.add_argument("--end", default="", help="last history date (default: yesterday)")
    ap.add_argument("--skip-massive", action="store_true")
    args = ap.parse_args(argv)
    from remy_finance.universe.candidates import UNIV_CANDIDATES_1

    started = datetime.now(UTC)
    end = date.fromisoformat(args.end) if args.end else started.date() - timedelta(days=1)
    symbols = sorted({*UNIV_CANDIDATES_1, "SPY", *SECTORS})
    t = Theta(args.url)
    steps: list[tuple[str, Callable[[], Result]]] = [
        ("H1", lambda: h1_closes(t, symbols, end)),
        ("H2", lambda: h2_vix(t, end)),
        ("H4", lambda: h4_rates(t, end)),
        ("H5", lambda: h5_iv_chains(t, end)),
        ("H6", lambda: h6_replay_boards(t)),
    ]
    if not args.skip_massive:
        steps += [("H3", lambda: h3_ten_year(end)), ("H7", lambda: h7_actions(symbols, end))]
    results = []
    for ident, step in steps:
        t0 = time.perf_counter()
        print(f"{ident}...", file=sys.stderr, flush=True)
        r = step()
        r.id = ident if r.id == "?" else r.id
        results.append(r)
        print(f"{ident}: {r.status} ({time.perf_counter() - t0:.0f}s) {r.summary}", file=sys.stderr)
    results.sort(key=lambda r: r.id)
    md = render(results, started, symbols)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    (out / f"r2_0_{stamp}.md").write_text(md, encoding="utf-8")
    (out / f"r2_0_{stamp}.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=1, default=str), encoding="utf-8"
    )
    print(md)
    print(f"written: {out / f'r2_0_{stamp}.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
