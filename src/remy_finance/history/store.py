"""HIST-1 storage (DR-0045): an append-only SQLite file of frozen records.

- ``obs``: one ``HistObservation`` per (series, symbol, session).
- ``events``: Massive ``DividendEvent`` and ``CorporateAction`` records.
- ``missing``: deterministic derivation failures (e.g. no expirations either side of 30
  days), so a rebuild doesn't retry them. Fetch errors are never recorded here.
- ``conflicts``: a *different* value arriving for an existing key (a vendor revision). The
  original is kept; the newcomer is logged here and reported, never written over.

Triggers refuse UPDATE and DELETE on every table, and refuse an INSERT that would collide
with an existing key (``INSERT OR REPLACE`` deletes without firing DELETE triggers).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from remy_finance.core import CorporateAction, DividendEvent
from remy_finance.core.canonical import sha256_hex
from remy_finance.history.records import HistObservation, HistSeries

SCHEMA_VERSION = "HIST-1"

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
CREATE TABLE obs (
    series TEXT NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
    public_time TEXT NOT NULL, record_json TEXT NOT NULL, record_hash TEXT NOT NULL,
    PRIMARY KEY (series, symbol, session)
) STRICT;
CREATE TABLE events (
    kind TEXT NOT NULL, symbol TEXT NOT NULL, key_date TEXT NOT NULL,
    public_time TEXT NOT NULL, record_json TEXT NOT NULL, record_hash TEXT NOT NULL,
    PRIMARY KEY (kind, symbol, key_date, record_hash)
) STRICT;
CREATE TABLE missing (
    series TEXT NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
    reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY (series, symbol, session)
) STRICT;
CREATE TABLE conflicts (
    seq INTEGER PRIMARY KEY, series TEXT NOT NULL, symbol TEXT NOT NULL,
    session TEXT NOT NULL, kept_hash TEXT NOT NULL, offered_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;
"""
_TABLES = ("meta", "obs", "events", "missing", "conflicts")
_KEYS = {
    "obs": "series = NEW.series AND symbol = NEW.symbol AND session = NEW.session",
    "events": "kind = NEW.kind AND symbol = NEW.symbol AND key_date = NEW.key_date "
    "AND record_hash = NEW.record_hash",
    "missing": "series = NEW.series AND symbol = NEW.symbol AND session = NEW.session",
}


def _triggers() -> str:
    parts = [
        f"CREATE TRIGGER {t}_no_{op.lower()} BEFORE {op} ON {t} "
        f"BEGIN SELECT RAISE(ABORT, 'HIST-1 is append-only: {op} on {t} refused'); END;"
        for t in _TABLES
        for op in ("UPDATE", "DELETE")
    ]
    parts += [
        f"CREATE TRIGGER {t}_no_overwrite BEFORE INSERT ON {t} "
        f"WHEN EXISTS (SELECT 1 FROM {t} WHERE {key}) "
        f"BEGIN SELECT RAISE(ABORT, 'HIST-1 is append-only: {t} key exists'); END;"
        for t, key in _KEYS.items()
    ]
    return "\n".join(parts)


class HistError(RuntimeError):
    pass


@dataclass(frozen=True)
class Conflict:
    series: str
    symbol: str
    session: date
    kept_hash: str
    offered: str


def hist_path(data_dir: Path) -> Path:
    return data_dir / "hist" / "hist1.sqlite"


class HistStore:
    """Thread-safe for writers through one connection per call (SQLite serializes)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        with self._conn() as c:
            if fresh:
                c.executescript(_SCHEMA)
                c.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
                c.executescript(_triggers())
            row = c.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise HistError(f"{path}: not a {SCHEMA_VERSION} store")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=60)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            yield c
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------------ writes

    def add_observations(self, obs: Iterable[HistObservation]) -> tuple[int, int]:
        """Insert new observations. Returns (added, conflicts). An identical record already
        stored is a no-op; a different one for the same key is logged as a conflict."""
        added = conflicts = 0
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for o in obs:
                text = o.canonical_json()
                digest = sha256_hex(text)
                key = (o.series.value, o.symbol, o.session.isoformat())
                row = c.execute(
                    "SELECT record_hash, record_json FROM obs "
                    "WHERE series = ? AND symbol = ? AND session = ?",
                    key,
                ).fetchone()
                if row is not None:
                    if not _same_value(row[1], o):
                        c.execute(
                            "INSERT INTO conflicts (series, symbol, session, kept_hash, "
                            "offered_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (*key, row[0], text, now),
                        )
                        conflicts += 1
                    continue
                c.execute(
                    "INSERT INTO obs VALUES (?, ?, ?, ?, ?, ?)",
                    (*key, o.public_time.isoformat(), text, digest),
                )
                added += 1
        return added, conflicts

    def add_events(self, events: Iterable[DividendEvent | CorporateAction]) -> int:
        added = 0
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for e in events:
                text = e.canonical_json()
                digest = sha256_hex(text)
                if isinstance(e, DividendEvent):
                    key = ("DIVIDEND", e.symbol, e.ex_date.isoformat(), digest)
                else:
                    key = (e.action_type.value, e.symbol, e.effective_date.isoformat(), digest)
                exists = c.execute(
                    "SELECT 1 FROM events WHERE kind = ? AND symbol = ? AND key_date = ? "
                    "AND record_hash = ?",
                    key,
                ).fetchone()
                if exists:
                    continue
                c.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (key[0], key[1], key[2], e.public_time.isoformat(), text, digest),
                )
                added += 1
        return added

    def add_missing(self, series: HistSeries, symbol: str, session: date, reason: str) -> bool:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            key = (series.value, symbol, session.isoformat())
            if c.execute(
                "SELECT 1 FROM missing WHERE series = ? AND symbol = ? AND session = ?", key
            ).fetchone():
                return False
            c.execute(
                "INSERT INTO missing VALUES (?, ?, ?, ?, ?)",
                (*key, reason[:200], datetime.now(UTC).isoformat()),
            )
            return True

    # ------------------------------------------------------------------ reads

    def observations(
        self,
        series: HistSeries,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        public_by: datetime | None = None,
    ) -> list[HistObservation]:
        q = "SELECT record_json FROM obs WHERE series = ? AND symbol = ?"
        args: list[str] = [series.value, symbol]
        if start is not None:
            q += " AND session >= ?"
            args.append(start.isoformat())
        if end is not None:
            q += " AND session <= ?"
            args.append(end.isoformat())
        q += " ORDER BY session"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        out = [HistObservation.from_canonical_json(r[0]) for r in rows]
        if public_by is not None:
            out = [o for o in out if o.public_time <= public_by]
        return out

    def has(self, series: HistSeries, symbol: str, session: date) -> bool:
        with self._conn() as c:
            key = (series.value, symbol, session.isoformat())
            return bool(
                c.execute(
                    "SELECT 1 FROM obs WHERE series = ? AND symbol = ? AND session = ? "
                    "UNION ALL SELECT 1 FROM missing WHERE series = ? AND symbol = ? "
                    "AND session = ?",
                    (*key, *key),
                ).fetchone()
            )

    def sessions_present(self, series: HistSeries, symbol: str) -> set[date]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT session FROM obs WHERE series = ? AND symbol = ? "
                "UNION SELECT session FROM missing WHERE series = ? AND symbol = ?",
                (series.value, symbol, series.value, symbol),
            ).fetchall()
        return {date.fromisoformat(r[0]) for r in rows}

    def dividends(self, symbol: str) -> list[DividendEvent]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT record_json FROM events WHERE kind = 'DIVIDEND' AND symbol = ? "
                "ORDER BY key_date",
                (symbol,),
            ).fetchall()
        return [DividendEvent.from_canonical_json(r[0]) for r in rows]

    def actions(self, symbol: str) -> list[CorporateAction]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT record_json FROM events WHERE kind != 'DIVIDEND' AND symbol = ? "
                "ORDER BY key_date",
                (symbol,),
            ).fetchall()
        return [CorporateAction.from_canonical_json(r[0]) for r in rows]

    def missing(self, series: HistSeries | None = None) -> list[tuple[str, str, date, str]]:
        q = "SELECT series, symbol, session, reason FROM missing"
        args: Sequence[str] = ()
        if series is not None:
            q += " WHERE series = ?"
            args = (series.value,)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY series, symbol, session", args).fetchall()
        return [(r[0], r[1], date.fromisoformat(r[2]), r[3]) for r in rows]

    def conflicts(self) -> list[Conflict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT series, symbol, session, kept_hash, offered_json FROM conflicts "
                "ORDER BY seq"
            ).fetchall()
        return [Conflict(r[0], r[1], date.fromisoformat(r[2]), r[3], r[4]) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._conn() as c:
            return {
                t: int(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for t in ("obs", "events", "missing", "conflicts")
            }


def _same_value(stored_json: str, offered: HistObservation) -> bool:
    """Same market fact: value, public time and details. A re-fetch differs only in
    ``ingest_time``, which is not a revision."""
    stored = HistObservation.from_canonical_json(stored_json)
    return (
        stored.value == offered.value
        and stored.public_time == offered.public_time
        and stored.details == offered.details
    )
