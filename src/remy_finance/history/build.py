"""HIST-1 backfill (DR-0045): closes, Treasury yields, dividends and splits, then 30-day
ATM IV per name and session.

Resumable: keys already stored (or recorded as deterministically missing) are skipped.
Fetch errors are counted and retried on the next run, never recorded as missing. Every
observation keeps its real historical public time; ``ingest_time`` is when we fetched it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from remy_finance.core import CorporateAction, DividendEvent
from remy_finance.data.timeutil import et_at, next_session, session_close, xnys
from remy_finance.history.atm_iv import atm_iv30, rows_from_vendor
from remy_finance.history.records import RATE_SYMBOL, HistObservation, HistSeries
from remy_finance.history.store import HistStore
from remy_finance.history.view import HistoryView

YIELD_FIELDS = {
    HistSeries.YIELD_1M: "yield_1_month",
    HistSeries.YIELD_3M: "yield_3_month",
    HistSeries.YIELD_10Y: "yield_10_year",
}
EOD_PUBLIC = (17, 15)  # ThetaData end-of-day report time (DR-0026)
IV_DECIMALS = Decimal("0.000001")


class HistSource(Protocol):
    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]: ...
    def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]: ...
    def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]: ...
    def events(self, symbols: Sequence[str]) -> list[DividendEvent | CorporateAction]: ...


class ThetaMassiveHistSource:
    """Live vendors: ThetaData for closes and chains, Massive for yields and events."""

    def __init__(self, theta: Any, massive: Any) -> None:
        self.theta, self.massive = theta, massive

    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        y0 = start
        while y0 <= end:  # one request per calendar year keeps responses small
            y1 = min(date(y0.year, 12, 31), end)
            out += self.theta.stock_eod(symbol, y0, y1)
            y0 = y1 + timedelta(days=1)
        return out

    def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self.theta.option_eod_chain(symbol, session)
        return rows

    def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = self.massive.treasury_yields(start, end)
        return rows

    def events(self, symbols: Sequence[str]) -> list[DividendEvent | CorporateAction]:
        self.massive.prefetch(list(symbols))
        out: list[DividendEvent | CorporateAction] = []
        for s in symbols:
            out += list(self.massive.all_dividends(s))
            out += list(self.massive.all_actions(s))
        return out


@dataclass
class BuildSummary:
    closes_added: int = 0
    yields_added: int = 0
    events_added: int = 0
    iv_added: int = 0
    iv_missing: int = 0
    fetch_errors: list[str] = field(default_factory=list)
    conflicts: int = 0
    seconds: float = 0.0


def sessions_between(start: date, end: date) -> list[date]:
    return [s.date() for s in xnys().sessions_in_range(start.isoformat(), end.isoformat())]


def _row_date(row: dict[str, Any]) -> date | None:
    for key in ("created", "date", "last_trade", "timestamp"):
        v = row.get(key)
        if v:
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return None


def close_observations(
    symbol: str, rows: Iterable[dict[str, Any]], sessions: set[date], now: datetime
) -> list[HistObservation]:
    out: list[HistObservation] = []
    seen: set[date] = set()
    for r in rows:
        d = _row_date(r)
        price = r.get("close")
        if d is None or d not in sessions or d in seen or not price:
            continue
        seen.add(d)
        out.append(
            HistObservation(
                series=HistSeries.CLOSE,
                symbol=symbol,
                session=d,
                value=Decimal(str(price)),
                event_time=session_close(d),
                public_time=et_at(d, *EOD_PUBLIC),
                ingest_time=now,
                source="thetadata:stock/history/eod",
            )
        )
    return out


def yield_observations(rows: Iterable[dict[str, Any]], now: datetime) -> list[HistObservation]:
    out: list[HistObservation] = []
    for r in rows:
        d = date.fromisoformat(str(r["date"])[:10])
        if d.weekday() >= 5:
            continue
        public = et_at(next_session(d), 9, 30)
        for series, fld in YIELD_FIELDS.items():
            if r.get(fld) is None:
                continue
            out.append(
                HistObservation(
                    series=series,
                    symbol=RATE_SYMBOL,
                    session=d,
                    value=Decimal(str(r[fld])) / 100,
                    event_time=et_at(d, 17),
                    public_time=public,
                    ingest_time=now,
                    source="massive:fed/v1/treasury-yields",
                )
            )
    return out


def build(
    store: HistStore,
    source: HistSource,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    workers: int = 4,
    with_iv: bool = True,
    log: Callable[[str], None] = print,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BuildSummary:
    t0 = time.perf_counter()
    summary = BuildSummary()
    sessions = sessions_between(start, end)
    session_set = set(sessions)
    now = clock()

    log(f"HIST-1: {len(symbols)} symbols, {len(sessions)} sessions {start}..{end}")
    try:
        summary.events_added = store.add_events(source.events(symbols))
    except Exception as e:  # retried next run
        summary.fetch_errors.append(f"events: {type(e).__name__}: {e}")
    try:
        added, conflicts = store.add_observations(
            yield_observations(source.treasury_yields(start - timedelta(days=14), end), now)
        )
        summary.yields_added, summary.conflicts = added, summary.conflicts + conflicts
    except Exception as e:
        summary.fetch_errors.append(f"yields: {type(e).__name__}: {e}")
    for s in symbols:
        try:
            obs = close_observations(s, source.stock_eod(s, start, end), session_set, now)
            added, conflicts = store.add_observations(obs)
            summary.closes_added += added
            summary.conflicts += conflicts
        except Exception as e:
            summary.fetch_errors.append(f"closes {s}: {type(e).__name__}: {e}")
    log(f"closes +{summary.closes_added}, yields +{summary.yields_added}, "
        f"events +{summary.events_added}")  # fmt: skip

    if with_iv:
        tasks: list[tuple[str, date, float]] = []
        for s in symbols:
            done = store.sessions_present(HistSeries.ATMIV30, s)
            closes = {o.session: float(o.value) for o in store.observations(HistSeries.CLOSE, s)}
            tasks += [(s, d, closes[d]) for d in sessions if d not in done and d in closes]
        log(f"ATM IV: {len(tasks)} (symbol, session) tasks with {workers} workers")

        def one(task: tuple[str, date, float]) -> tuple[str, date, Any]:
            sym, d, spot = task
            rows = rows_from_vendor(source.option_eod_chain(sym, d))
            rates = HistoryView(store, et_at(d, *EOD_PUBLIC)).rates()
            return sym, d, atm_iv30(rows, spot, d, rates)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(one, t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), start=1):
                sym, d, _ = futures[fut]
                try:
                    _, _, res = fut.result()
                except Exception as e:
                    summary.fetch_errors.append(f"iv {sym} {d}: {type(e).__name__}: {e}")
                    continue
                if res.iv30 is None:
                    store.add_missing(HistSeries.ATMIV30, sym, d, res.reason)
                    summary.iv_missing += 1
                else:
                    o = HistObservation(
                        series=HistSeries.ATMIV30,
                        symbol=sym,
                        session=d,
                        value=Decimal(str(res.iv30)).quantize(IV_DECIMALS),
                        event_time=session_close(d),
                        public_time=et_at(d, *EOD_PUBLIC),
                        ingest_time=clock(),
                        source="derived:thetadata:option/history/eod (DR-0045)",
                        details=res.details(),
                    )
                    added, conflicts = store.add_observations([o])
                    summary.iv_added += added
                    summary.conflicts += conflicts
                if i % 500 == 0:
                    errors = len(summary.fetch_errors)
                    log(f"ATM IV {i}/{len(tasks)}: +{summary.iv_added}, "
                        f"missing {summary.iv_missing}, errors {errors}")  # fmt: skip
    summary.seconds = time.perf_counter() - t0
    return summary
