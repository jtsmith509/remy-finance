"""CLOSE-1: official closes read from HIST-1's ``CLOSE`` series (R2 brief P12, DR-0045,
DR-0046).

- ``HistCloseSource``: the store only. A close that isn't stored is ``None``.
- ``StoreFirstCloses``: the store first; a gap is fetched from the vendor (ThetaData,
  DR-0026) and written to the store, write-once with its real public time, but only once
  that time has passed. Gap fills are counted so reports can print them (EVAL-2 §7.1).

Both satisfy ``OfficialCloseSource``. Callers still read through ``official_close_as_of``,
which refuses a close that wasn't public at the reading time.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from remy_finance.core import Mode, OfficialClose
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.history.records import HistObservation, HistSeries
from remy_finance.history.store import HistStore


def to_official_close(o: HistObservation) -> OfficialClose:
    return OfficialClose(
        symbol=o.symbol,
        session_date=o.session,
        price=o.value,
        event_time=o.event_time,
        public_time=o.public_time,
        ingest_time=o.ingest_time,
        source=f"CLOSE-1:{o.source}",
    )


def to_observation(c: OfficialClose) -> HistObservation:
    return HistObservation(
        series=HistSeries.CLOSE,
        symbol=c.symbol,
        session=c.session_date,
        value=c.price,
        event_time=c.event_time,
        public_time=c.public_time,
        ingest_time=c.ingest_time,
        source=c.source,
    )


class HistCloseSource:
    """Official closes from the store only."""

    def __init__(self, store: HistStore) -> None:
        self.store = store

    def official_close(self, symbol: str, session_date: date) -> OfficialClose | None:
        got = self.store.observations(HistSeries.CLOSE, symbol, session_date, session_date)
        return to_official_close(got[0]) if got else None


@dataclass
class CloseStats:
    from_store: int = 0
    filled: int = 0  # gaps fetched from the vendor and written to the store
    not_found: int = 0  # neither stored nor available from the vendor
    fetch_failed: int = 0  # gaps not fetched because the vendor failed
    last_error: str = ""


class StoreFirstCloses:
    """The store first; a gap is fetched from ``fallback`` and stored once public.

    ``store`` may be a factory: the store is then opened on the first read, so a store that
    can't be opened fails the step that reads it (inside the tick's guard), not the caller
    that built this source. A vendor failure never stops store reads: the gap is counted
    in ``stats.fetch_failed`` and returned as ``None`` (not public), and the vendor isn't
    asked again by this source."""

    def __init__(
        self,
        store: HistStore | Callable[[], HistStore],
        fallback: OfficialCloseSource | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._hist: HistStore | None = store if isinstance(store, HistStore) else None
        self.fallback = fallback
        self.clock = clock
        self.stats = CloseStats()
        self._vendor_broken = False

    @property
    def hist(self) -> HistStore:
        if self._hist is None:
            factory = self._store
            assert not isinstance(factory, HistStore)
            self._hist = factory()
        return self._hist

    @property
    def stored(self) -> HistCloseSource:
        return HistCloseSource(self.hist)

    def official_close(self, symbol: str, session_date: date) -> OfficialClose | None:
        stored = self.stored.official_close(symbol, session_date)
        if stored is not None:
            self.stats.from_store += 1
            return stored
        if self.fallback is None:
            self.stats.not_found += 1
            return None
        if self._vendor_broken:
            self.stats.fetch_failed += 1
            return None
        try:
            fetched = self.fallback.official_close(symbol, session_date)
        except Exception as e:  # the store keeps being read; the gap waits
            self._vendor_broken = True
            self.stats.fetch_failed += 1
            self.stats.last_error = f"{type(e).__name__}: {e}"[:300]
            return None
        if fetched is None:
            self.stats.not_found += 1
            return None
        if fetched.public_time > self.clock():
            return fetched  # not public yet: never stored; official_close_as_of refuses it
        added, _ = self.hist.add_observations([to_observation(fetched)])
        self.stats.filled += added
        kept = self.stored.official_close(symbol, session_date)  # the stored value wins
        return kept if kept is not None else fetched


def needed_closes(
    data_dir: Path,
    last: date,
    modes: Iterable[Mode] = (Mode.PROSPECTIVE, Mode.REPLAY),
) -> set[tuple[str, date]]:
    """Every (symbol, session) close the records need up to ``last``: each recommendation's
    underlying at its expiration, and each MKT-1 fit's target (DR-0040)."""
    from remy_finance.registry import Registry, registry_path
    from remy_finance.scheduler.runlog import RunLogStore

    root = data_dir
    out: set[tuple[str, date]] = set()
    for mode in modes:
        path = registry_path(root, mode)
        if path.exists():
            with Registry.open(path) as reg:
                for rec in reg.recommendations():
                    if rec.expiration <= last and rec.candidate.legs:
                        out.add((rec.candidate.legs[0].contract.underlying, rec.expiration))
        for run in RunLogStore(root / "runs").all(mode):
            for fit in run.fits:
                if fit.ok and fit.rn_quantiles and fit.expiration <= last:
                    out.add((fit.symbol, fit.expiration))
    return out
