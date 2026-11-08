"""MarketState builder (handoff R0.4): replay boards through PointInTimeView, prospective
boards from live quotes with ``as_of`` stamped after the fetch.

Per-symbol data problems become ``QualityNote`` gap records on the board rather than
aborting the snapshot. A ``LookaheadError`` always propagates: it is a defect.
Quote quality flags follow QAGE-1 (DR-0015): STALE_QUOTE when older than 5 minutes,
plus ONE_SIDED, CROSSED and ZERO_SIZE.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from typing import Protocol

from pydantic import Field

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    LookaheadError,
    MarketState,
    Mode,
    OptionQuote,
    Provenance,
    QualityFlag,
    QualityNote,
    RatePoint,
    Record,
    Session,
    UnderlyingQuote,
)
from remy_finance.data.protocol import MarketDataSource
from remy_finance.data.view import PointInTimeView
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time


class QuoteAgeRule(Record):
    version: str = Field(min_length=1)
    stale_after_seconds: int = Field(ge=0)


QAGE_1 = QuoteAgeRule(version="QAGE-1", stale_after_seconds=300)


def _flags(
    q: OptionQuote | UnderlyingQuote, as_of: datetime, rule: QuoteAgeRule
) -> set[QualityFlag]:
    flags = set(q.quality_flags)
    if (as_of - q.quote_time).total_seconds() > rule.stale_after_seconds:
        flags.add(QualityFlag.STALE_QUOTE)
    if q.bid <= 0 or q.ask <= 0:
        flags.add(QualityFlag.ONE_SIDED)
    elif q.bid > q.ask:
        flags.add(QualityFlag.CROSSED)
    if q.bid_size == 0 or q.ask_size == 0:
        flags.add(QualityFlag.ZERO_SIZE)
    return flags


def flag_option_quote(q: OptionQuote, as_of: datetime, rule: QuoteAgeRule = QAGE_1) -> OptionQuote:
    flags = _flags(q, as_of, rule)
    return (
        q if flags == set(q.quality_flags) else q.model_copy(update={"quality_flags": tuple(flags)})
    )


def flag_underlying_quote(
    q: UnderlyingQuote, as_of: datetime, rule: QuoteAgeRule = QAGE_1
) -> UnderlyingQuote:
    flags = _flags(q, as_of, rule)
    return (
        q if flags == set(q.quality_flags) else q.model_copy(update={"quality_flags": tuple(flags)})
    )


def _note(scope: str, err: Exception) -> QualityNote:
    return QualityNote(scope=scope, code=type(err).__name__, detail=str(err)[:300])


@dataclass(frozen=True)
class _SymbolData:
    chain: tuple[OptionQuote, ...]
    underlying: UnderlyingQuote | None
    dividends: tuple[DividendEvent, ...]
    actions: tuple[CorporateAction, ...]
    errors: tuple[QualityNote, ...]  # one per failed read; the other reads are kept


def _read[T](sym: str, what: str, fn: Callable[[], T], default: T, errors: list[QualityNote]) -> T:
    """One guarded read: a vendor/data failure becomes a note; look-ahead always raises."""
    try:
        return fn()
    except LookaheadError:
        raise
    except Exception as e:
        errors.append(
            QualityNote(scope=sym, code=f"{what}:{type(e).__name__}", detail=str(e)[:300])
        )
        return default


SymbolProgress = Callable[[str, int, int, int, float], None]
"""progress(symbol, done, total, n_option_quotes, seconds_for_symbol)"""


def build_from_view(
    source: MarketDataSource,
    *,
    as_of: datetime,
    session: Session,
    mode: Mode,
    universe_version: str,
    underlyings: Sequence[str],
    provenance: Sequence[Provenance] = (),
    rule: QuoteAgeRule = QAGE_1,
    max_workers: int = 4,
    progress: SymbolProgress | None = None,
) -> MarketState:
    """Build a board by reading every input through ``PointInTimeView(source, as_of)``.

    Symbols are fetched ``max_workers`` at a time; the board does not depend on it."""
    view = PointInTimeView(source, as_of)
    symbols = sorted(set(underlyings))

    def fetch(sym: str) -> tuple[str, _SymbolData, float]:
        t0 = time.perf_counter()
        errors: list[QualityNote] = []
        empty_q: tuple[OptionQuote, ...] = ()
        empty_d: tuple[DividendEvent, ...] = ()
        empty_a: tuple[CorporateAction, ...] = ()
        out = _SymbolData(
            chain=_read(sym, "OPTION_QUOTES", lambda: view.chain_quotes(sym), empty_q, errors),
            underlying=_read(
                sym, "UNDERLYING_QUOTE", lambda: view.underlying_quote(sym), None, errors
            ),
            dividends=_read(sym, "DIVIDENDS", lambda: view.dividends(sym), empty_d, errors),
            actions=_read(
                sym, "CORPORATE_ACTIONS", lambda: view.corporate_actions(sym), empty_a, errors
            ),
            errors=tuple(errors),
        )
        return sym, out, time.perf_counter() - t0

    results: dict[str, _SymbolData] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(fetch, sym) for sym in symbols]
        try:
            for fut in as_completed(futures):
                sym, out, secs = fut.result()  # re-raises LookaheadError
                results[sym] = out
                if progress is not None:
                    n = len(out.chain)
                    progress(sym, len(results), len(symbols), n, secs)
        except BaseException:
            for f in futures:
                f.cancel()
            raise

    return _assemble(
        results,
        view,
        as_of=as_of,
        session=session,
        mode=mode,
        universe_version=universe_version,
        underlyings=underlyings,
        provenance=provenance,
        rule=rule,
    )


def _assemble(
    results: dict[str, _SymbolData],
    view: PointInTimeView,
    *,
    as_of: datetime,
    session: Session,
    mode: Mode,
    universe_version: str,
    underlyings: Sequence[str],
    provenance: Sequence[Provenance],
    rule: QuoteAgeRule,
    extra_notes: Sequence[QualityNote] = (),
) -> MarketState:
    """Shared by the replay and prospective builders: one set of per-read rules."""
    options: list[OptionQuote] = []
    unders: list[UnderlyingQuote] = []
    divs: list[DividendEvent] = []
    acts: list[CorporateAction] = []
    notes: list[QualityNote] = list(extra_notes)
    for sym in sorted(results):  # fixed order: the board is independent of completion order
        out = results[sym]
        notes.extend(out.errors)
        failed = {n.code.split(":")[0] for n in out.errors}
        divs.extend(out.dividends)
        acts.extend(out.actions)
        if not out.chain and "OPTION_QUOTES" not in failed:
            notes.append(QualityNote(scope=sym, code="NO_OPTION_QUOTES"))
        if out.underlying is not None:
            unders.append(flag_underlying_quote(out.underlying, as_of, rule))
        elif "UNDERLYING_QUOTE" not in failed:
            notes.append(QualityNote(scope=sym, code="NO_UNDERLYING_QUOTE"))
        options.extend(flag_option_quote(q, as_of, rule) for q in out.chain)
    rate_errors: list[QualityNote] = []
    empty_r: tuple[RatePoint, ...] = ()
    rates = _read("*", "RATES", view.rates, empty_r, rate_errors)
    notes.extend(rate_errors)
    return MarketState(
        session=session,
        as_of=as_of,
        mode=mode,
        universe_version=universe_version,
        underlyings=tuple(sorted(set(underlyings))),
        underlying_quotes=tuple(unders),
        option_quotes=tuple(options),
        dividends=tuple(divs),
        corporate_actions=tuple(acts),
        rates=tuple(rates),
        provenance=(
            *provenance,
            Provenance(source="builder", details=(("quote_age_rule", rule.version),)),
        ),
        quality_notes=tuple(notes),
    )


def build_replay(
    source: MarketDataSource,
    session_date: date,
    session: Session,
    *,
    kind: SnapshotKind = SnapshotKind.DECISION,
    universe_version: str,
    underlyings: Sequence[str],
    provenance: Sequence[Provenance] = (),
    max_workers: int = 4,
    progress: SymbolProgress | None = None,
) -> MarketState:
    return build_from_view(
        source,
        as_of=snapshot_time(session_date, session, kind),
        session=session,
        mode=Mode.REPLAY,
        universe_version=universe_version,
        underlyings=underlyings,
        provenance=provenance,
        max_workers=max_workers,
        progress=progress,
    )


class LiveQuoteSource(Protocol):
    def live_chain_quotes(self, underlying: str) -> tuple[OptionQuote, ...]: ...
    def live_underlying_quote(self, symbol: str) -> UnderlyingQuote | None: ...


def build_prospective(
    live: LiveQuoteSource,
    events: MarketDataSource,
    session: Session,
    *,
    universe_version: str,
    underlyings: Sequence[str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    provenance: Sequence[Provenance] = (),
    rule: QuoteAgeRule = QAGE_1,
) -> MarketState:
    """Fetch live quotes, then stamp ``as_of`` *after* the fetch so nothing post-dates it.
    Dividends, corporate actions and rates are read through the view at that ``as_of``.

    Every read is guarded on its own (a failure becomes a note naming the read and the
    other reads are kept); a ``LookaheadError`` from any read always aborts (DR-0022)."""
    symbols = sorted(set(underlyings))
    live_errors: dict[str, list[QualityNote]] = {sym: [] for sym in symbols}
    empty_q: tuple[OptionQuote, ...] = ()
    chains: dict[str, tuple[OptionQuote, ...]] = {}
    uqs: dict[str, UnderlyingQuote | None] = {}
    for sym in symbols:
        errs = live_errors[sym]
        chains[sym] = _read(
            sym, "OPTION_QUOTES", partial(live.live_chain_quotes, sym), empty_q, errs
        )
        uqs[sym] = _read(
            sym, "UNDERLYING_QUOTE", partial(live.live_underlying_quote, sym), None, errs
        )
    as_of = clock()
    view = PointInTimeView(events, as_of)
    empty_d: tuple[DividendEvent, ...] = ()
    empty_a: tuple[CorporateAction, ...] = ()
    results: dict[str, _SymbolData] = {}
    for sym in symbols:
        errs = live_errors[sym]
        dividends = _read(sym, "DIVIDENDS", partial(view.dividends, sym), empty_d, errs)
        actions = _read(
            sym, "CORPORATE_ACTIONS", partial(view.corporate_actions, sym), empty_a, errs
        )
        results[sym] = _SymbolData(
            chain=tuple(chains[sym]),
            underlying=uqs[sym],
            dividends=tuple(dividends),
            actions=tuple(actions),
            errors=tuple(errs),
        )
    return _assemble(
        results,
        view,
        as_of=as_of,
        session=session,
        mode=Mode.PROSPECTIVE,
        universe_version=universe_version,
        underlyings=underlyings,
        provenance=provenance,
        rule=rule,
    )
