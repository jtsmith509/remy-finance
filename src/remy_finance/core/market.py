"""Point-in-time observations and the MarketState board (handoff Section 4).

Time model (DR-0017):
- Quotes carry ``quote_time``; they become available at that instant.
- Other observations carry ``event_time`` (when it happened), ``public_time`` (when it
  became public) and ``ingest_time`` (when we received it); they become available at
  ``public_time``.
- ``available_time`` is the single property the look-ahead guard checks. The fourth
  timestamp, the consuming decision's ``as_of``, lives on ``MarketState``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Self

from pydantic import Field, field_validator, model_validator

from remy_finance.core import canonical
from remy_finance.core.enums import (
    CorporateActionType,
    DividendType,
    Mode,
    QualityFlag,
    Session,
)
from remy_finance.core.errors import LookaheadError
from remy_finance.core.instruments import OptionContract
from remy_finance.core.record import Record
from remy_finance.core.types import Dec, NonNegDec, PosDec, UtcDatetime


def _sorted_unique_flags(flags: tuple[QualityFlag, ...]) -> tuple[QualityFlag, ...]:
    return tuple(sorted(set(flags), key=lambda f: f.value))


class _Quote(Record):
    bid: NonNegDec
    ask: NonNegDec
    bid_size: int = Field(ge=0)
    ask_size: int = Field(ge=0)
    quote_time: UtcDatetime
    ingest_time: UtcDatetime | None = None
    source: str = Field(min_length=1)
    quality_flags: tuple[QualityFlag, ...] = ()

    _normalize_flags = field_validator("quality_flags")(_sorted_unique_flags)

    @property
    def available_time(self) -> datetime:
        return self.quote_time

    @property
    def is_two_sided(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.bid_size > 0 and self.ask_size > 0


class OptionQuote(_Quote):
    """NBBO for one option contract. Sizes are in contracts."""

    contract: OptionContract


class UnderlyingQuote(_Quote):
    """NBBO for the underlying stock/ETF. Sizes are in shares."""

    symbol: str = Field(min_length=1)
    last: NonNegDec | None = None


class _PublicObservation(Record):
    event_time: UtcDatetime
    public_time: UtcDatetime
    ingest_time: UtcDatetime | None = None
    source: str = Field(min_length=1)

    @property
    def available_time(self) -> datetime:
        return self.public_time


class DividendEvent(_PublicObservation):
    """A declared cash dividend. ``event_time`` is the declaration (when known)."""

    symbol: str = Field(min_length=1)
    ex_date: date
    amount: PosDec
    dividend_type: DividendType = DividendType.ORDINARY
    declaration_date: date | None = None
    pay_date: date | None = None


class CorporateAction(_PublicObservation):
    """A corporate action (split, merger, ...). ``details`` are sorted key/value pairs."""

    symbol: str = Field(min_length=1)
    action_type: CorporateActionType
    effective_date: date
    details: tuple[tuple[str, str], ...] = ()

    @field_validator("details")
    @classmethod
    def _sort_details(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        keys = [k for k, _ in value]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate keys in details")
        return tuple(sorted(value))


class RatePoint(_PublicObservation):
    """An interest-rate observation. ``rate`` is an annualized decimal fraction (0.0392)."""

    symbol: str = Field(min_length=1)
    tenor: str = Field(min_length=1)
    observation_date: date
    rate: Dec


class OptionVolume(_PublicObservation):
    """Total option contracts traded for an underlying on one session (from an EOD report).

    ``public_time`` is when the vendor's end-of-day report is available (ThetaData: 17:15 ET).
    """

    symbol: str = Field(min_length=1)
    session_date: date
    contracts: int = Field(ge=0)


class OfficialClose(_PublicObservation):
    """An underlying's official closing price for one session (EXP-1 settlement input).

    ``event_time`` is the close (16:00 ET, earlier on half days); ``public_time`` is when
    the vendor publishes it (DR-0026: ThetaData end-of-day, 17:15 ET).
    """

    symbol: str = Field(min_length=1)
    session_date: date
    price: PosDec


class Provenance(Record):
    """Where data in a snapshot came from (vendor, endpoint, terminal version, ...)."""

    source: str = Field(min_length=1)
    details: tuple[tuple[str, str], ...] = ()


class QualityNote(Record):
    """A gap or quality issue recorded with a snapshot (scope: a symbol, or ``*``)."""

    scope: str = Field(min_length=1)
    code: str = Field(min_length=1)
    detail: str = ""


def _by_canonical_text[T: Record](items: tuple[T, ...]) -> tuple[T, ...]:
    return tuple(sorted(items, key=lambda r: r.canonical_json()))


class MarketState(Record):
    """The immutable board: what could be known at ``as_of``. No forecasts, no scores.

    Collections are stored in canonical order, so the same content always has the same
    ``snapshot_id`` regardless of the order it was supplied in. Construction raises
    ``LookaheadError`` if any observation became available after ``as_of``.
    """

    session: Session
    as_of: UtcDatetime
    mode: Mode
    universe_version: str = Field(min_length=1)
    underlyings: tuple[str, ...]
    underlying_quotes: tuple[UnderlyingQuote, ...] = ()
    option_quotes: tuple[OptionQuote, ...] = ()
    dividends: tuple[DividendEvent, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    rates: tuple[RatePoint, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    quality_notes: tuple[QualityNote, ...] = ()

    @field_validator("underlyings")
    @classmethod
    def _sort_underlyings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate underlyings")
        return tuple(sorted(value))

    @field_validator("underlying_quotes")
    @classmethod
    def _sort_uq(cls, value: tuple[UnderlyingQuote, ...]) -> tuple[UnderlyingQuote, ...]:
        symbols = [q.symbol for q in value]
        if len(set(symbols)) != len(symbols):
            raise ValueError("more than one underlying quote per symbol")
        return tuple(sorted(value, key=lambda q: q.symbol))

    @field_validator("option_quotes")
    @classmethod
    def _sort_oq(cls, value: tuple[OptionQuote, ...]) -> tuple[OptionQuote, ...]:
        symbols = [q.contract.osi_symbol for q in value]
        if len(set(symbols)) != len(symbols):
            raise ValueError("more than one quote per option contract")
        return tuple(sorted(value, key=lambda q: q.contract.osi_symbol))

    _sort_dividends = field_validator("dividends")(_by_canonical_text)
    _sort_cas = field_validator("corporate_actions")(_by_canonical_text)
    _sort_rates = field_validator("rates")(_by_canonical_text)
    _sort_prov = field_validator("provenance")(_by_canonical_text)
    _sort_notes = field_validator("quality_notes")(_by_canonical_text)

    @model_validator(mode="after")
    def _check_board(self) -> Self:
        universe = set(self.underlyings)
        for uq in self.underlying_quotes:
            if uq.symbol not in universe:
                raise ValueError(f"underlying quote for {uq.symbol} outside the universe")
        for oq in self.option_quotes:
            if oq.contract.underlying not in universe:
                raise ValueError(f"option quote {oq.contract.osi_symbol} outside the universe")
        for item, label in self._observations():
            if item.available_time > self.as_of:
                raise LookaheadError(
                    f"{label} available at {item.available_time.isoformat()} is after "
                    f"as_of {self.as_of.isoformat()}"
                )
        return self

    def _observations(
        self,
    ) -> Iterable[tuple[_Quote | _PublicObservation, str]]:
        for uq in self.underlying_quotes:
            yield uq, f"underlying quote {uq.symbol}"
        for oq in self.option_quotes:
            yield oq, f"option quote {oq.contract.osi_symbol.strip()}"
        for d in self.dividends:
            yield d, f"dividend {d.symbol} ex {d.ex_date}"
        for ca in self.corporate_actions:
            yield ca, f"corporate action {ca.symbol} {ca.action_type.value}"
        for r in self.rates:
            yield r, f"rate {r.symbol} {r.observation_date}"

    @property
    def snapshot_id(self) -> str:
        """The content hash of the board (not a stored field, so it can't be circular)."""
        return canonical.sha256_hex(self.canonical_json())
