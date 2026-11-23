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
from remy_finance.data.timeutil import et_at, next_session, previous_session, session_close, xnys
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
SECTOR_SPDRS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
NIGHTLY_WINDOW_SESSIONS = 10  # the nightly run re-checks this many sessions (catch-up)
IV_DECIMALS = Decimal("0.000001")


MAX_RATE_AGE_DAYS = 10  # older public yields count as missing: retry, don't derive


class MissingInputError(RuntimeError):
    """An input to a derivation is absent (no public rates, an empty chain). Counted as a
    fetch error and retried next run; never recorded as missing (DR-0045)."""


class HistSource(Protocol):
    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]: ...
    def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]: ...
    def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]: ...
    def events(self, symbols: Sequence[str]) -> list[DividendEvent | CorporateAction]: ...
    def unusable_dividends(self) -> list[tuple[str, date, str]]:
        """(symbol, ex_date, reason) for dividends ``events`` could not return."""
        ...

    def event_failures(self) -> list[str]:
        """Event fetches that failed inside ``events`` (retried next run)."""
        ...


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

    def unusable_dividends(self) -> list[tuple[str, date, str]]:
        return list(self.massive.unusable_dividends)

    def event_failures(self) -> list[str]:
        return list(self.massive.event_failures)


@dataclass
class BuildSummary:
    symbols: int = 0
    closes_added: int = 0
    yields_added: int = 0
    events_added: int = 0
    unusable_added: int = 0
    iv_added: int = 0
    iv_missing: int = 0
    fetch_errors: list[str] = field(default_factory=list)
    # (symbol, session) in the range with no stored close after the fetch: a delayed or
    # absent vendor close (or a name not yet listed); retried by the next run.
    closes_absent: list[tuple[str, date]] = field(default_factory=list)
    not_public: int = 0  # sessions in the range whose 17:15 ET public time hadn't passed
    stopped_at_deadline: bool = False
    conflicts: int = 0
    seconds: float = 0.0


def base_symbols() -> list[str]:
    """HIST-1's standing coverage: UNIV-CANDIDATES-1, SPY and the sector SPDRs."""
    from remy_finance.universe.candidates import UNIV_CANDIDATES_1

    return sorted({*UNIV_CANDIDATES_1, "SPY", *SECTOR_SPDRS})


def nightly_window(day: date) -> tuple[date, date]:
    """The last ``NIGHTLY_WINDOW_SESSIONS`` XNYS sessions ending at ``day``."""
    start = day
    for _ in range(NIGHTLY_WINDOW_SESSIONS - 1):
        start = previous_session(start)
    return start, day


def last_session_of_week(day: date) -> bool:
    return next_session(day).isocalendar()[:2] != day.isocalendar()[:2]


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


def public_rates(store: HistStore, session: date) -> dict[float, float]:
    """The yields public at ``session``'s end-of-day time, for discount factors. Raises
    ``MissingInputError`` if the 1-month or 3-month yield is absent or more than
    ``MAX_RATE_AGE_DAYS`` old, so the derivation is retried rather than recorded missing."""
    view = HistoryView(store, et_at(session, *EOD_PUBLIC))
    for series in (HistSeries.YIELD_1M, HistSeries.YIELD_3M):
        o = view.latest(series, RATE_SYMBOL)
        if o is None or (session - o.session).days > MAX_RATE_AGE_DAYS:
            raise MissingInputError(f"no public {series.value} as of {session}")
    return view.rates()


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
    deadline: datetime | None = None,
) -> BuildSummary:
    """Fetch and store the range. Only sessions whose end-of-day public time (17:15 ET)
    has passed are stored, so nothing is ever written before it is public. With a
    ``deadline``, the run stops cleanly when it passes (the rest is left for the next run,
    which is resumable)."""
    t0 = time.perf_counter()
    summary = BuildSummary(symbols=len(symbols))
    now = clock()
    every = sessions_between(start, end)
    sessions = [d for d in every if et_at(d, *EOD_PUBLIC) <= now]
    summary.not_public = len(every) - len(sessions)
    session_set = set(sessions)

    def late() -> bool:
        if deadline is not None and clock() > deadline:
            summary.stopped_at_deadline = True
            return True
        return False

    log(f"HIST-1: {len(symbols)} symbols, {len(sessions)} sessions {start}..{end}")
    try:
        added, conflicts = store.add_events(source.events(symbols))
        summary.events_added, summary.conflicts = added, summary.conflicts + conflicts
        summary.unusable_added = store.add_unusable(
            ("DIVIDEND", sym, ex, reason) for sym, ex, reason in source.unusable_dividends()
        )
        summary.fetch_errors += [f"events: {f}" for f in source.event_failures()]
    except Exception as e:  # retried next run
        summary.fetch_errors.append(f"events: {type(e).__name__}: {e}")
    try:
        rows = source.treasury_yields(start - timedelta(days=14), end)
        added, conflicts = store.add_observations(
            o for o in yield_observations(rows, now) if o.public_time <= now
        )
        summary.yields_added, summary.conflicts = added, summary.conflicts + conflicts
    except Exception as e:
        summary.fetch_errors.append(f"yields: {type(e).__name__}: {e}")
    for s in symbols:
        if late():
            summary.fetch_errors.append(f"deadline: stopped before the closes of {s}")
            break
        try:
            obs = close_observations(s, source.stock_eod(s, start, end), session_set, now)
            added, conflicts = store.add_observations(obs)
            summary.closes_added += added
            summary.conflicts += conflicts
        except Exception as e:
            summary.fetch_errors.append(f"closes {s}: {type(e).__name__}: {e}")
            continue
        present = {o.session for o in store.observations(HistSeries.CLOSE, s, start, end)}
        summary.closes_absent += [(s, d) for d in sessions if d not in present]
    log(
        f"closes +{summary.closes_added}, yields +{summary.yields_added}, "
        f"events +{summary.events_added}, unusable dividends +{summary.unusable_added}"
    )

    if with_iv and not summary.stopped_at_deadline:
        tasks: list[tuple[str, date, float]] = []
        for s in symbols:
            done = store.sessions_present(HistSeries.ATMIV30, s)
            closes = {o.session: float(o.value) for o in store.observations(HistSeries.CLOSE, s)}
            tasks += [(s, d, closes[d]) for d in sessions if d not in done and d in closes]
        log(f"ATM IV: {len(tasks)} (symbol, session) tasks with {workers} workers")

        def one(task: tuple[str, date, float]) -> tuple[str, date, Any]:
            sym, d, spot = task
            rates = public_rates(store, d)  # before the fetch: no rates, no request
            raw = source.option_eod_chain(sym, d)
            if not raw:
                raise MissingInputError("EMPTY_CHAIN")
            return sym, d, atm_iv30(rows_from_vendor(raw, sym), spot, d, rates)

        # Managed by hand so any exit from the loop (Ctrl-C, a store error) cancels the
        # queued chain requests instead of running them all with nobody storing results.
        pool = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            futures = {pool.submit(one, t): t for t in tasks}
            for i, fut in enumerate(as_completed(futures), start=1):
                if late():
                    left = len(tasks) - i + 1
                    summary.fetch_errors.append(f"deadline: {left} IV tasks left for the next run")
                    break
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
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True, cancel_futures=summary.stopped_at_deadline)
    summary.seconds = time.perf_counter() - t0
    return summary
