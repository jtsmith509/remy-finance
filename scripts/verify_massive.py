#!/usr/bin/env python3
"""R0.1 extension: can Massive (formerly Polygon) supply our supplementary data?

Runs on the Mac mini with ``MASSIVE_API_KEY`` set (Theta Terminal optional, for
cross-checks). All Massive access goes through ``MassiveSource`` (the data adapter) and,
where time matters, ``PointInTimeView``. For SPY, AAPL and KO over the R0.1 window:

  M1  Dividends with ex-dates and declaration dates usable as public_time
  M2  Splits and ticker events (plus control symbols with known events)
  M3  Stock NBBO quotes at our snapshot times (10:00, 10:05, 15:45, 15:50 ET)
  M4  Treasury yields for the risk-free rate (vs ThetaData rates when reachable)
  M5  Market holidays (forward-looking) and current status, vs the XNYS calendar
  M6  Daily close vs ThetaData EOD close (the DR-0010 spot-check)

A 403 from Massive is reported as NEEDS_UPGRADE (the endpoint is not in the current plan).
The free plan allows about 5 requests/minute, so requests are throttled (--min-interval,
default 12.5 s; use 0 on a paid plan). Writes data/verification/massive_<UTC>.{md,json}.
The API key is never printed.

    uv run python scripts/verify_massive.py
    uv run python scripts/verify_massive.py --min-interval 0          # paid plan
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from remy_finance.core import CorporateActionType
from remy_finance.data import PointInTimeView
from remy_finance.data.massive import MassiveEntitlementError, MassiveError, MassiveSource
from remy_finance.data.thetadata import ThetaDataError, ThetaDataSource
from remy_finance.data.timeutil import NEW_YORK, et_at, xnys

PASS, PARTIAL, UPGRADE, FAIL, ERROR = "PASS", "PARTIAL", "NEEDS_UPGRADE", "FAIL", "ERROR"
SYMBOLS = ("SPY", "AAPL", "KO")
REFERENCE = date(2026, 11, 6)
SESSIONS = (date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), REFERENCE)
TIMES = ((10, 0), (10, 5), (15, 45), (15, 50))
UPGRADE_NOTES = {
    "M3": "Stocks Advanced (real-time NBBO, all history) or Business + Expansion; "
    "not in Basic, Starter or Developer",
}


@dataclass
class Check:
    id: str
    title: str
    status: str = ERROR
    summary: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def checked(cid: str, title: str) -> Callable[[Callable[..., Check]], Callable[..., Check]]:
    """Run a check; a 403 becomes NEEDS_UPGRADE and any other failure ERROR, never a crash."""

    def wrap(fn: Callable[..., Check]) -> Callable[..., Check]:
        def run(*a: Any, **k: Any) -> Check:
            try:
                return fn(Check(cid, title), *a, **k)
            except MassiveEntitlementError as e:
                return Check(cid, title, UPGRADE, f"not in the current plan: {e}")
            except Exception as e:
                return Check(cid, title, ERROR, f"{type(e).__name__}: {e}")

        return run

    return wrap


def as_of(d: date, hh: int = 10, mm: int = 0) -> datetime:
    return et_at(d, hh, mm)


# ---------------------------------------------------------------------- checks


@checked("M1", "Dividends with ex-dates and declaration dates (public_time)")
def m1_dividends(c: Check, src: MassiveSource, syms: list[str]) -> Check:
    now = datetime.now(UTC)
    ok = True
    for s in syms:
        before = len(src.skipped)
        divs = list(src.dividends(s, now))
        skipped = [x for x in src.skipped[before:] if x.startswith(s)]
        view_divs = PointInTimeView(src, as_of(REFERENCE)).dividends(s)  # point-in-time read
        bad_order = [
            str(d.ex_date) for d in divs if d.declaration_date and d.declaration_date > d.ex_date
        ]
        earliest = min((d.ex_date for d in divs), default=None)
        c.evidence.append(
            {
                "symbol": s,
                "usable_with_declaration_date": len(divs),
                "skipped_no_declaration_or_non_usd": len(skipped),
                "earliest_ex_date_returned": str(earliest) if earliest else None,
                "declared_after_ex_date": bad_order,
                "known_at_2026-11-06_10:00_ET": len(view_divs),
                "latest": [
                    {
                        "ex": str(d.ex_date),
                        "declared": str(d.declaration_date),
                        "amount": str(d.amount),
                        "type": d.dividend_type.value,
                        "public_time": d.public_time.isoformat(),
                    }
                    for d in divs[-3:]
                ],
                "skipped_samples": skipped[:3],
            }
        )
        ok &= bool(divs) and not skipped and not bad_order
    c.status = PASS if ok else PARTIAL
    c.summary = (
        "Declaration dates present; public_time = declaration date 16:00 ET (DR-0014)."
        if ok
        else ("Some records lack declaration dates or look inconsistent; see evidence.")
    )
    c.notes.append(
        "History depth on the free (Basic) plan is 2 years; Starter and above: all history."
    )
    return c


@checked("M2", "Splits and ticker events")
def m2_splits_events(c: Check, src: MassiveSource, syms: list[str], controls: list[str]) -> Check:
    now = datetime.now(UTC)
    for s in [*syms, *controls]:
        acts = src.corporate_actions(s, now)
        c.evidence.append(
            {
                "symbol": s,
                "control": s in controls,
                "splits": [
                    (a.action_type.value, str(a.effective_date), dict(a.details))
                    for a in acts
                    if a.action_type
                    in (
                        CorporateActionType.SPLIT,
                        CorporateActionType.REVERSE_SPLIT,
                        CorporateActionType.OTHER,
                    )
                ],
                "ticker_changes": [
                    (str(a.effective_date), dict(a.details))
                    for a in acts
                    if a.action_type is CorporateActionType.SYMBOL_CHANGE
                ],
                "special_dividends": [
                    (str(a.effective_date), dict(a.details))
                    for a in acts
                    if a.action_type is CorporateActionType.SPECIAL_DIVIDEND
                ],
            }
        )
    events_unavailable = [x for x in src.skipped if "ticker events unavailable" in x]
    c.status = PASS if not events_unavailable else PARTIAL
    c.summary = (
        "Splits and ticker events retrievable through the adapter."
        if c.status == PASS
        else "Splits OK; ticker events failed for some symbols (see notes)."
    )
    c.notes += events_unavailable[:5]
    c.notes.append(
        "Controls show whether events older than the plan's history window are visible "
        "(Basic: 2 years). No announcement dates: public_time = effective date 09:30 ET."
    )
    return c


@checked("M3", "Stock NBBO at snapshot times")
def m3_nbbo(c: Check, src: MassiveSource, syms: list[str], theta: ThetaDataSource | None) -> Check:
    ages: list[float] = []
    diffs: list[Decimal] = []
    for s in syms:
        for d in SESSIONS:
            for hh, mm in TIMES:
                ts = as_of(d, hh, mm)
                q = PointInTimeView(src, ts).underlying_quote(s)  # raises if entitlement missing
                row: dict[str, Any] = {"symbol": s, "at": ts.astimezone(NEW_YORK).isoformat()}
                if q is None:
                    row["quote"] = None
                else:
                    age = (ts - q.quote_time).total_seconds()
                    ages.append(age)
                    row.update(
                        bid=str(q.bid),
                        ask=str(q.ask),
                        bid_size=q.bid_size,
                        ask_size=q.ask_size,
                        quote_time=q.quote_time.isoformat(),
                        age_s=round(age, 3),
                    )
                    if theta is not None:
                        try:
                            tq = theta.underlying_quote_at(s, ts)
                            if tq is not None:
                                diff = (q.bid + q.ask) / 2 - (tq.bid + tq.ask) / 2
                                diffs.append(abs(diff))
                                row["theta_mid_diff"] = str(diff)
                        except ThetaDataError as e:
                            row["theta_error"] = str(e)[:120]
                c.evidence.append(row)
    missing = sum(1 for e in c.evidence if e.get("quote") is None and "bid" not in e)
    c.status = PASS if ages and not missing else PARTIAL
    ages.sort()
    c.summary = (
        f"{len(ages)} quotes, median age {ages[len(ages) // 2]:.3f}s, max {ages[-1]:.1f}s; "
        f"{missing} missing" + (f"; max |mid diff| vs ThetaData {max(diffs)}" if diffs else "")
        if ages
        else "No quotes returned."
    )
    c.notes.append(
        "Massive quotes carry their SIP timestamp (staleness is measurable), unlike "
        "ThetaData stock at_time rows, which carry the request time."
    )
    return c


@checked("M4", "Treasury yields for the risk-free rate")
def m4_treasury(c: Check, src: MassiveSource, theta: ThetaDataSource | None) -> Check:
    known = as_of(REFERENCE)
    rates = list(PointInTimeView(src, known).rates())
    by_date = {(r.symbol, r.observation_date): r.rate for r in rates}
    c.evidence.append(
        {
            "known_as_of": known.isoformat(),
            "points": [
                {
                    "symbol": r.symbol,
                    "date": str(r.observation_date),
                    "rate": str(r.rate),
                    "public_time": r.public_time.isoformat(),
                }
                for r in rates[-6:]
            ],
        }
    )
    if theta is not None:
        try:
            trates = {
                (r.observation_date): r.rate
                for r in ThetaDataSource(rate_symbols=("TREASURY_M3",)).rates(known)
            }
            cmp = [
                {
                    "date": str(d),
                    "massive_3m": str(by_date.get(("TREASURY_3M", d))),
                    "theta_3m": str(v),
                }
                for d, v in sorted(trates.items())[-5:]
            ]
            c.evidence.append({"vs_thetadata_3m": cmp})
        except ThetaDataError as e:
            c.notes.append(f"ThetaData comparison skipped: {e}")
    latest = max((r.observation_date for r in rates), default=None)
    c.status = PASS if rates else FAIL
    c.summary = (
        f"{len(rates)} points known at 10:00 ET {REFERENCE}; latest observation {latest}."
        if rates
        else "No yields returned."
    )
    c.notes.append(
        "Yields are percent in the API; stored as fractions. public_time follows DR-0011."
    )
    return c


@checked("M5", "Market holidays and status")
def m5_calendar(c: Check, src: MassiveSource) -> Check:
    hol = src.upcoming_holidays()
    status = src.market_status()
    cal = xnys()
    mismatches = []
    for h in hol:
        if str(h.get("exchange", "")).upper() != "NYSE":
            continue
        d = str(h["date"])
        is_session = bool(cal.is_session(d))
        if h.get("status") == "closed" and is_session:
            mismatches.append(f"{d}: Massive closed, XNYS session")
        if h.get("status") == "early-close":
            if not is_session:
                mismatches.append(f"{d}: Massive early close, XNYS no session")
            else:
                ours = cal.session_close(d).tz_convert("UTC")
                theirs = datetime.fromisoformat(str(h["close"]).replace("Z", "+00:00"))
                if ours.to_pydatetime() != theirs:
                    mismatches.append(f"{d}: close {theirs.isoformat()} vs XNYS {ours.isoformat()}")
    c.evidence.append(
        {
            "upcoming_nyse": [h for h in hol if str(h.get("exchange", "")).upper() == "NYSE"],
            "status_now": {k: status.get(k) for k in ("market", "serverTime", "exchanges")},
        }
    )
    c.status = PASS if hol and not mismatches else (PARTIAL if hol else FAIL)
    c.summary = f"{len(hol)} upcoming holiday rows; {len(mismatches)} disagreements with XNYS." + (
        f" {mismatches[:3]}" if mismatches else ""
    )
    c.notes.append(
        "Holidays are forward-looking only: no historical calendar, so exchange_calendars "
        "stays the source for replay; Massive can cross-check upcoming days."
    )
    return c


@checked("M6", "Daily close vs ThetaData EOD close (DR-0010)")
def m6_closes(c: Check, src: MassiveSource, syms: list[str], theta_url: str) -> Check:
    start, end = SESSIONS[0] - timedelta(days=10), REFERENCE
    agree = total = 0
    for s in syms:
        mc = src.daily_closes(s, start, end)
        r = httpx.get(
            f"{theta_url}/v3/stock/history/eod",
            timeout=60,
            params={
                "symbol": s,
                "start_date": f"{start:%Y%m%d}",
                "end_date": f"{end:%Y%m%d}",
                "format": "json",
            },
        )
        r.raise_for_status()
        tc = {
            date.fromisoformat(str(x["created"])[:10]): Decimal(str(x["close"]))
            for x in r.json(parse_float=Decimal).get("response") or []
        }
        days = sorted(set(mc) & set(tc))
        diffs = [(str(d), str(tc[d]), str(mc[d])) for d in days if tc[d] != mc[d]]
        agree += len(days) - len(diffs)
        total += len(days)
        c.evidence.append(
            {"symbol": s, "sessions": len(days), "differences_theta_massive": diffs[:5]}
        )
    c.status = PASS if total and agree == total else (PARTIAL if total else FAIL)
    c.summary = f"{agree}/{total} sessions agree to the cent."
    return c


# ---------------------------------------------------------------------- report


def render(meta: dict[str, Any], checks: list[Check]) -> str:
    lines = ["# R0.1 extension: Massive data check", ""]
    lines += [f"- **{k}:** {v}" for k, v in meta.items()]
    lines += ["", "| # | Check | Result | Summary |", "|---|---|---|---|"]
    for ch in checks:
        lines.append(f"| {ch.id} | {ch.title} | **{ch.status}** | {ch.summary} |")
    for ch in checks:
        lines += ["", f"## {ch.id}. {ch.title}: {ch.status}", "", ch.summary, ""]
        if ch.status == UPGRADE and ch.id in UPGRADE_NOTES:
            lines.append(f"- Needs: {UPGRADE_NOTES[ch.id]}")
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
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument(
        "--controls", default="NVDA,META", help="symbols with known splits/ticker changes"
    )
    ap.add_argument("--min-interval", type=float, default=12.5, help="seconds between requests")
    ap.add_argument(
        "--theta-url", default=os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503")
    )
    ap.add_argument(
        "--out", default=os.path.join(os.environ.get("REMY_DATA_DIR", "data"), "verification")
    )
    ap.add_argument("--only", default="", help="comma-separated check ids, e.g. M1,M4")
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    controls = [s.strip().upper() for s in a.controls.split(",") if s.strip()]
    only = {s.strip().upper() for s in a.only.split(",") if s.strip()}

    try:
        src = MassiveSource(min_interval=a.min_interval)
    except MassiveError as e:
        print(f"{e}. Set MASSIVE_API_KEY (see the R0.1 memo).", file=sys.stderr)
        return 2
    theta: ThetaDataSource | None = ThetaDataSource(a.theta_url)
    try:
        httpx.get(f"{a.theta_url}/v3/calendar/today", timeout=5).raise_for_status()
    except httpx.HTTPError:
        theta = None

    started = time.monotonic()
    plan: list[tuple[str, Callable[[], Check]]] = [
        ("M1", lambda: m1_dividends(src, syms)),
        ("M2", lambda: m2_splits_events(src, syms, controls)),
        ("M3", lambda: m3_nbbo(src, syms, theta)),
        ("M4", lambda: m4_treasury(src, theta)),
        ("M5", lambda: m5_calendar(src)),
        (
            "M6",
            lambda: (
                m6_closes(src, syms, a.theta_url)
                if theta
                else Check(
                    "M6",
                    "Daily close vs ThetaData EOD close (DR-0010)",
                    ERROR,
                    "Theta Terminal not reachable",
                )
            ),
        ),
    ]
    checks: list[Check] = []
    for cid, fn in plan:
        if only and cid not in only:
            continue
        print(f"running {cid} ...", file=sys.stderr)
        checks.append(fn())

    meta = {
        "run_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "symbols": ", ".join(syms),
        "controls": ", ".join(controls),
        "sessions": f"{SESSIONS[0]} .. {SESSIONS[-1]}",
        "requests_to_massive": src.requests_made,
        "elapsed_s": round(time.monotonic() - started),
        "theta_terminal": "reachable" if theta else "not reachable",
    }
    md = render(meta, checks)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / f"massive_{stamp}.md").write_text(md)
    (out / f"massive_{stamp}.json").write_text(
        json.dumps({"meta": meta, "checks": [asdict(c) for c in checks]}, indent=1, default=str)
    )
    print(md)
    print(f"wrote {out}/massive_{stamp}.{{md,json}}", file=sys.stderr)
    return 0 if all(c.status in (PASS, UPGRADE) for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
