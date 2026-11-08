"""MarketState builder (handoff R0.4): replay boards through PointInTimeView, prospective
boards from live quotes with ``as_of`` stamped after the fetch.

Per-symbol data problems become ``QualityNote`` gap records on the board rather than
aborting the snapshot. A ``LookaheadError`` always propagates: it is a defect.
Quote quality flags follow QAGE-1 (DR-0015): STALE_QUOTE when older than 5 minutes,
plus ONE_SIDED, CROSSED and ZERO_SIZE.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
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
) -> MarketState:
    """Build a board by reading every input through ``PointInTimeView(source, as_of)``."""
    view = PointInTimeView(source, as_of)
    options: list[OptionQuote] = []
    unders: list[UnderlyingQuote] = []
    divs: list[DividendEvent] = []
    acts: list[CorporateAction] = []
    notes: list[QualityNote] = []
    for sym in sorted(set(underlyings)):
        try:
            chain = view.chain_quotes(sym)
            uq = view.underlying_quote(sym)
            divs.extend(view.dividends(sym))
            acts.extend(view.corporate_actions(sym))
        except LookaheadError:
            raise
        except Exception as e:  # a vendor/data problem for one symbol is a gap, not a crash
            notes.append(_note(sym, e))
            continue
        if not chain:
            notes.append(QualityNote(scope=sym, code="NO_OPTION_QUOTES"))
        if uq is None:
            notes.append(QualityNote(scope=sym, code="NO_UNDERLYING_QUOTE"))
        else:
            unders.append(flag_underlying_quote(uq, as_of, rule))
        options.extend(flag_option_quote(q, as_of, rule) for q in chain)
    try:
        rates = list(view.rates())
    except LookaheadError:
        raise
    except Exception as e:
        rates = []
        notes.append(_note("*", e))
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
) -> MarketState:
    return build_from_view(
        source,
        as_of=snapshot_time(session_date, session, kind),
        session=session,
        mode=Mode.REPLAY,
        universe_version=universe_version,
        underlyings=underlyings,
        provenance=provenance,
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
    Dividends, corporate actions and rates are read through the view at that ``as_of``."""
    chains: dict[str, tuple[OptionQuote, ...]] = {}
    uqs: dict[str, UnderlyingQuote | None] = {}
    notes: list[QualityNote] = []
    for sym in sorted(set(underlyings)):
        try:
            chains[sym] = live.live_chain_quotes(sym)
            uqs[sym] = live.live_underlying_quote(sym)
        except Exception as e:
            notes.append(_note(sym, e))
    as_of = clock()
    view = PointInTimeView(events, as_of)
    options: list[OptionQuote] = []
    unders: list[UnderlyingQuote] = []
    divs: list[DividendEvent] = []
    acts: list[CorporateAction] = []
    for sym in sorted(chains):
        try:
            divs.extend(view.dividends(sym))
            acts.extend(view.corporate_actions(sym))
        except LookaheadError:
            raise
        except Exception as e:
            notes.append(_note(sym, e))
        if not chains[sym]:
            notes.append(QualityNote(scope=sym, code="NO_OPTION_QUOTES"))
        options.extend(flag_option_quote(q, as_of, rule) for q in chains[sym])
        uq = uqs.get(sym)
        if uq is None:
            notes.append(QualityNote(scope=sym, code="NO_UNDERLYING_QUOTE"))
        else:
            unders.append(flag_underlying_quote(uq, as_of, rule))
    try:
        rates = list(view.rates())
    except LookaheadError:
        raise
    except Exception as e:
        rates = []
        notes.append(_note("*", e))
    return MarketState(
        session=session,
        as_of=as_of,
        mode=Mode.PROSPECTIVE,
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
