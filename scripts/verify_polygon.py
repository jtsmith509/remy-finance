#!/usr/bin/env python3
"""R0.3 verification of Polygon.io / Massive (DR-0014) and the EXP-1 close spot-check (DR-0010).

Run on the Mac mini with POLYGON_API_KEY set and the Theta Terminal running:

    uv run python scripts/verify_polygon.py
    uv run python scripts/verify_polygon.py --symbols SPY,AAPL,KO --years 2 --sessions 10

Reports, per symbol:
  1. Dividends over the last N years: count, how many have a declaration date (needed for
     public_time), types, and the latest few records.
  2. Splits on record.
  3. ThetaData stock EOD `close` vs Polygon's daily-bar close for the last K sessions
     (agreement supports using ThetaData EOD close as the official close in EXP-1).
Writes data/verification/polygon_<UTC>.md (git-ignored). Never prints the API key.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from remy_finance.data.polygon import PolygonError, PolygonSource


def polygon_daily_closes(
    key: str, base: str, symbol: str, start: date, end: date
) -> dict[date, Decimal]:
    r = httpx.get(
        f"{base}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
        params={"adjusted": "false", "sort": "asc", "limit": "5000"},
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    )
    r.raise_for_status()
    out: dict[date, Decimal] = {}
    for bar in r.json(parse_float=Decimal).get("results") or []:
        day = datetime.fromtimestamp(int(bar["t"]) / 1000, tz=UTC).date()
        out[day] = Decimal(str(bar["c"]))
    return out


def theta_eod_closes(url: str, symbol: str, start: date, end: date) -> dict[date, Decimal]:
    r = httpx.get(
        f"{url}/v3/stock/history/eod",
        params={
            "symbol": symbol,
            "start_date": f"{start:%Y%m%d}",
            "end_date": f"{end:%Y%m%d}",
            "format": "json",
        },
        timeout=120,
    )
    r.raise_for_status()
    out: dict[date, Decimal] = {}
    for row in r.json(parse_float=Decimal).get("response") or []:
        out[date.fromisoformat(str(row["created"])[:10])] = Decimal(str(row["close"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbols", default="SPY,AAPL,KO")
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument(
        "--theta-url", default=os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503")
    )
    ap.add_argument(
        "--out", default=os.path.join(os.environ.get("REMY_DATA_DIR", "data"), "verification")
    )
    a = ap.parse_args()

    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        print("POLYGON_API_KEY is not set", file=sys.stderr)
        return 2
    base = os.environ.get("POLYGON_BASE_URL", "https://api.polygon.io").rstrip("/")
    now = datetime.now(UTC)
    since = now.date() - timedelta(days=365 * a.years)
    src = PolygonSource(key, base_url=base)
    lines = [
        "# R0.3 Polygon verification and EXP-1 close spot-check",
        "",
        f"- run_at_utc: {now.isoformat(timespec='seconds')}",
        f"- polygon base: {base}",
        f"- window: dividends since {since}; closes over the last ~{a.sessions} sessions",
        "",
    ]
    ok = True
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        lines += [f"## {sym}", ""]
        try:
            n_skipped_before = len(src.skipped)
            divs = [d for d in src.dividends(sym, now) if d.ex_date >= since]
            skipped = [s for s in src.skipped[n_skipped_before:] if s.startswith(sym)]
            total = len(divs) + len(skipped)
            pct = 100.0 * len(divs) / total if total else 0.0
            lines.append(
                f"- Dividends since {since}: {total}; "
                f"with declaration_date: {len(divs)} ({pct:.0f}%)"
            )
            if skipped:
                ok = False
                lines.append(f"- **Missing declaration date (unusable):** {skipped[:5]}")
            for d in divs[-4:]:
                lines.append(
                    f"  - ex {d.ex_date} amount {d.amount} {d.dividend_type.value} "
                    f"declared {d.declaration_date} (public_time {d.public_time.isoformat()})"
                )
            acts = src.corporate_actions(sym, now)
            lines.append(
                "- Corporate actions on record: "
                + str([(x.action_type.value, str(x.effective_date)) for x in acts][:8])
            )
        except PolygonError as e:
            ok = False
            lines.append(f"- **Polygon error:** {e}")
        try:
            end = now.date()
            start = end - timedelta(days=int(a.sessions * 1.6) + 5)
            pc = polygon_daily_closes(key, base, sym, start, end)
            tc = theta_eod_closes(a.theta_url, sym, start, end)
            days = sorted(set(pc) & set(tc))[-a.sessions :]
            diffs: list[Any] = [(str(d), str(tc[d]), str(pc[d])) for d in days if tc[d] != pc[d]]
            lines.append(
                f"- Close spot-check: {len(days)} sessions compared, "
                f"{len(diffs)} differ (theta, polygon): {diffs[:5]}"
            )
            ok &= bool(days) and not diffs
        except (httpx.HTTPError, KeyError, ValueError) as e:
            ok = False
            lines.append(f"- **Close spot-check error:** {type(e).__name__}: {e}")
        lines.append("")
    lines.append(f"**Overall: {'PASS' if ok else 'CHECK FINDINGS'}**")
    report = "\n".join(lines) + "\n"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"polygon_{now:%Y%m%dT%H%M%SZ}.md"
    path.write_text(report)
    print(report)
    print(f"wrote {path}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
