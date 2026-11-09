"""FixtureSource: an offline, file-based MarketDataSource. The test suite runs on it.

Storage is a JSON-lines file of canonical records (``$type``-tagged): option contracts,
option and underlying quote time series, dividends, corporate actions and rates.
Semantics mirror the ThetaData adapter: ``chain_quotes_at`` returns, per contract, the
latest quote at or before ``ts`` on the same ET session date.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    OfficialClose,
    OptionContract,
    OptionQuote,
    OptionVolume,
    RatePoint,
    Record,
    UnderlyingQuote,
)
from remy_finance.core.canonical import TYPE_TAG, type_tag
from remy_finance.data.timeutil import et_date

_TYPES: dict[str, type[Record]] = {
    type_tag(t): t
    for t in (
        OptionContract,
        OptionQuote,
        UnderlyingQuote,
        DividendEvent,
        CorporateAction,
        RatePoint,
        OptionVolume,
    )
}


class FixtureSource:
    def __init__(self, records: Iterable[Record] = ()) -> None:
        self._contracts: dict[str, dict[str, OptionContract]] = defaultdict(dict)
        self._oq: dict[str, list[OptionQuote]] = defaultdict(list)
        self._uq: dict[str, list[UnderlyingQuote]] = defaultdict(list)
        self._div: list[DividendEvent] = []
        self._ca: list[CorporateAction] = []
        self._rates: list[RatePoint] = []
        self._volume: dict[tuple[str, date], OptionVolume] = {}
        self._close: dict[tuple[str, date], OfficialClose] = {}
        for r in records:
            self.add(r)

    def add(self, record: Record) -> None:
        if isinstance(record, OptionContract):
            self._contracts[record.underlying][record.osi_symbol] = record
        elif isinstance(record, OptionQuote):
            c = record.contract
            self._contracts[c.underlying][c.osi_symbol] = c
            self._oq[c.underlying].append(record)
        elif isinstance(record, UnderlyingQuote):
            self._uq[record.symbol].append(record)
        elif isinstance(record, DividendEvent):
            self._div.append(record)
        elif isinstance(record, CorporateAction):
            self._ca.append(record)
        elif isinstance(record, RatePoint):
            self._rates.append(record)
        elif isinstance(record, OptionVolume):
            self._volume[(record.symbol, record.session_date)] = record
        elif isinstance(record, OfficialClose):
            self._close[(record.symbol, record.session_date)] = record
        else:
            raise TypeError(f"FixtureSource cannot store {type(record).__name__}")

    # ------------------------------------------------------------ persistence

    def records(self) -> list[Record]:
        out: list[Record] = []
        for by_osi in self._contracts.values():
            out.extend(by_osi.values())
        for qs in self._oq.values():
            out.extend(qs)
        for us in self._uq.values():
            out.extend(us)
        out.extend(self._div)
        out.extend(self._ca)
        out.extend(self._rates)
        out.extend(self._volume.values())
        return sorted(out, key=lambda r: r.canonical_json())

    def dump(self, path: Path) -> None:
        path.write_text(
            "".join(r.canonical_json() + "\n" for r in self.records()), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> FixtureSource:
        records: list[Record] = []
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            data = json.loads(line)
            t = _TYPES.get(data.get(TYPE_TAG))
            if t is None:
                raise ValueError(f"{path}:{n}: unknown record type {data.get(TYPE_TAG)!r}")
            records.append(t.model_validate(data))
        return cls(records)

    # ------------------------------------------------------------ the protocol

    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        today = et_date(as_of)
        return tuple(
            sorted(
                (c for c in self._contracts[underlying].values() if c.expiration >= today),
                key=lambda c: c.osi_symbol,
            )
        )

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        session = et_date(ts)
        latest: dict[str, OptionQuote] = {}
        for q in self._oq[underlying]:
            if q.quote_time <= ts and et_date(q.quote_time) == session:
                prev = latest.get(q.contract.osi_symbol)
                if prev is None or q.quote_time > prev.quote_time:
                    latest[q.contract.osi_symbol] = q
        return tuple(latest[k] for k in sorted(latest))

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None:
        session = et_date(ts)
        candidates = [
            q for q in self._uq[symbol] if q.quote_time <= ts and et_date(q.quote_time) == session
        ]
        return max(candidates, key=lambda q: q.quote_time, default=None)

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        return tuple(d for d in self._div if d.symbol == symbol and d.public_time <= known_as_of)

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        return tuple(a for a in self._ca if a.symbol == symbol and a.public_time <= known_as_of)

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]:
        return tuple(r for r in self._rates if r.public_time <= known_as_of)

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        return self._volume.get((underlying, session_date))

    def official_close(self, symbol: str, session_date: date) -> OfficialClose | None:
        return self._close.get((symbol, session_date))
