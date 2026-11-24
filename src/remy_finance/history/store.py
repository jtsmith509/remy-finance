"""HIST-1 storage (DR-0045): an append-only SQLite file of frozen records.

- ``obs``: one ``HistObservation`` per (series, symbol, session).
- ``events``: Massive ``DividendEvent`` and ``CorporateAction`` records, one per market fact.
- ``unusable``: vendor events that could not be used (excluded from returns, not ignored).
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

SCHEMA_VERSION = "HIST-1/2"

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
    """CREATE TABLE obs (
    series TEXT NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
    public_time TEXT NOT NULL, record_json TEXT NOT NULL, record_hash TEXT NOT NULL,
    PRIMARY KEY (series, symbol, session)
) STRICT""",
    # One row per market fact: (kind, symbol, date, ident), where ident separates distinct
    # events on one date (a dividend's type). ``fact_hash`` hashes the record without
    # ``ingest_time``, so a re-fetch is recognised as the same fact.
    """CREATE TABLE events (
    kind TEXT NOT NULL, symbol TEXT NOT NULL, key_date TEXT NOT NULL, ident TEXT NOT NULL,
    public_time TEXT NOT NULL, record_json TEXT NOT NULL, record_hash TEXT NOT NULL,
    fact_hash TEXT NOT NULL,
    PRIMARY KEY (kind, symbol, key_date, ident)
) STRICT""",
    """CREATE TABLE missing (
    series TEXT NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
    reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY (series, symbol, session)
) STRICT""",
    # Vendor events we could not turn into a usable record (e.g. a dividend without a
    # declaration date): the affected session is excluded from total returns, not ignored.
    """CREATE TABLE unusable (
    kind TEXT NOT NULL, symbol TEXT NOT NULL, key_date TEXT NOT NULL,
    reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
    PRIMARY KEY (kind, symbol, key_date, reason)
) STRICT""",
    # ``series``/``session`` hold the event kind and date for event conflicts.
    """CREATE TABLE conflicts (
    seq INTEGER PRIMARY KEY, series TEXT NOT NULL, symbol TEXT NOT NULL,
    session TEXT NOT NULL, kept_hash TEXT NOT NULL, offered_json TEXT NOT NULL,
    offered_fact_hash TEXT NOT NULL, recorded_at TEXT NOT NULL,
    UNIQUE (series, symbol, session, kept_hash, offered_fact_hash)
) STRICT""",
)
_TABLES = ("meta", "obs", "events", "missing", "unusable", "conflicts")
_KEYS = {
    "obs": "series = NEW.series AND symbol = NEW.symbol AND session = NEW.session",
    "events": "kind = NEW.kind AND symbol = NEW.symbol AND key_date = NEW.key_date "
    "AND ident = NEW.ident",
    "missing": "series = NEW.series AND symbol = NEW.symbol AND session = NEW.session",
    "unusable": "kind = NEW.kind AND symbol = NEW.symbol AND key_date = NEW.key_date "
    "AND reason = NEW.reason",
    "conflicts": "series = NEW.series AND symbol = NEW.symbol AND session = NEW.session "
    "AND kept_hash = NEW.kept_hash AND offered_fact_hash = NEW.offered_fact_hash",
}
_TRIGGER_NAMES = frozenset(
    {f"{t}_no_{op}" for t in _TABLES for op in ("update", "delete")}
    | {f"{t}_no_overwrite" for t in _KEYS}
)


def _triggers() -> list[str]:
    parts = [
        f"CREATE TRIGGER {t}_no_{op.lower()} BEFORE {op} ON {t} "
        f"BEGIN SELECT RAISE(ABORT, 'HIST-1 is append-only: {op} on {t} refused'); END"
        for t in _TABLES
        for op in ("UPDATE", "DELETE")
    ]
    parts += [
        f"CREATE TRIGGER {t}_no_overwrite BEFORE INSERT ON {t} "
        f"WHEN EXISTS (SELECT 1 FROM {t} WHERE {key}) "
        f"BEGIN SELECT RAISE(ABORT, 'HIST-1 is append-only: {t} key exists'); END"
        for t, key in _KEYS.items()
    ]
    return parts


def fact_hash(record: HistObservation | DividendEvent | CorporateAction) -> str:
    """SHA-256 of the record without ``ingest_time``: the market fact, not the fetch."""
    return sha256_hex(record.model_copy(update={"ingest_time": None}).canonical_json())


def event_key(e: DividendEvent | CorporateAction) -> tuple[str, str, str, str]:
    """(kind, symbol, date, ident): one market event. Two dividends on one ex-date are
    distinct only if their type differs (an ordinary and a special)."""
    if isinstance(e, DividendEvent):
        return ("DIVIDEND", e.symbol, e.ex_date.isoformat(), e.dividend_type.value)
    return (e.action_type.value, e.symbol, e.effective_date.isoformat(), "")


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
        try:
            with self._conn() as c:
                # Created in one transaction, meta row last: a crash leaves nothing or the
                # whole store; a concurrent opener waits on the lock.
                c.execute("BEGIN IMMEDIATE")
                tables = {
                    r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not tables:
                    for stmt in (*_SCHEMA, *_triggers()):
                        c.execute(stmt)
                    c.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
                row = c.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
                triggers = {
                    r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
                }
        except sqlite3.DatabaseError as e:
            raise HistError(f"{path}: not a {SCHEMA_VERSION} store ({e})") from e
        if row is None or row[0] != SCHEMA_VERSION:
            raise HistError(f"{path}: not a {SCHEMA_VERSION} store")
        absent = sorted(_TRIGGER_NAMES - triggers)
        if absent:
            raise HistError(f"{path}: append-only triggers missing: {absent}")

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
                        conflicts += _log_conflict(c, key, row[0], text, fact_hash(o), now)
                    continue
                c.execute(
                    "INSERT INTO obs VALUES (?, ?, ?, ?, ?, ?)",
                    (*key, o.public_time.isoformat(), text, digest),
                )
                added += 1
        return added, conflicts

    def add_events(self, events: Iterable[DividendEvent | CorporateAction]) -> tuple[int, int]:
        """Insert new events keyed on the market fact (``event_key``). A re-fetch (only
        ``ingest_time`` differs) is a no-op; a different record for the same key (a vendor
        revision) is logged as a conflict and the original kept. Returns (added, conflicts)."""
        added = conflicts = 0
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for e in events:
                text = e.canonical_json()
                fact = fact_hash(e)
                key = event_key(e)
                row = c.execute(
                    "SELECT record_hash, fact_hash FROM events "
                    "WHERE kind = ? AND symbol = ? AND key_date = ? AND ident = ?",
                    key,
                ).fetchone()
                if row is not None:
                    if row[1] != fact:
                        conflicts += _log_conflict(c, key[:3], row[0], text, fact, now)
                    continue
                c.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*key, e.public_time.isoformat(), text, sha256_hex(text), fact),
                )
                added += 1
        return added, conflicts

    def add_unusable(self, items: Iterable[tuple[str, str, date, str]]) -> int:
        """Record vendor events that could not be used: (kind, symbol, date, reason)."""
        added = 0
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for kind, symbol, d, reason in items:
                key = (kind, symbol, d.isoformat(), reason[:200])
                if c.execute(
                    "SELECT 1 FROM unusable WHERE kind = ? AND symbol = ? AND key_date = ? "
                    "AND reason = ?",
                    key,
                ).fetchone():
                    continue
                c.execute("INSERT INTO unusable VALUES (?, ?, ?, ?, ?)", (*key, now))
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

    def latest_observation(
        self, series: HistSeries, symbol: str, end: date, public_by: datetime
    ) -> HistObservation | None:
        """The latest observation with session <= ``end`` and public time <= ``public_by``
        (newest first, so usually the first row read)."""
        with self._conn() as c:
            cur = c.execute(
                "SELECT record_json FROM obs WHERE series = ? AND symbol = ? AND session <= ? "
                "ORDER BY session DESC",
                (series.value, symbol, end.isoformat()),
            )
            for (text,) in cur:
                o = HistObservation.from_canonical_json(text)
                if o.public_time <= public_by:
                    return o
        return None

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

    def sessions_present(
        self, series: HistSeries, symbol: str, retry: frozenset[str] = frozenset()
    ) -> set[date]:
        """Sessions stored or recorded as missing, except missing ones whose reason is in
        ``retry`` (a reason an amended method can now resolve)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT session, NULL FROM obs WHERE series = ? AND symbol = ? "
                "UNION SELECT session, reason FROM missing WHERE series = ? AND symbol = ?",
                (series.value, symbol, series.value, symbol),
            ).fetchall()
        return {date.fromisoformat(r[0]) for r in rows if r[1] is None or r[1] not in retry}

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

    def missing_resolved(self) -> set[tuple[str, str, date]]:
        """Missing keys that have since been derived (an amended method), as (series,
        symbol, session). The missing rows stay, append-only, as history."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT m.series, m.symbol, m.session FROM missing m JOIN obs o "
                "ON o.series = m.series AND o.symbol = m.symbol AND o.session = m.session"
            ).fetchall()
        return {(r[0], r[1], date.fromisoformat(r[2])) for r in rows}

    def unusable(self, symbol: str | None = None) -> list[tuple[str, str, date, str]]:
        q = "SELECT kind, symbol, key_date, reason FROM unusable"
        args: Sequence[str] = ()
        if symbol is not None:
            q += " WHERE symbol = ?"
            args = (symbol,)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY symbol, key_date, kind", args).fetchall()
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
                for t in ("obs", "events", "missing", "unusable", "conflicts")
            }


def _log_conflict(
    c: sqlite3.Connection,
    key: Sequence[str],
    kept_hash: str,
    offered_json: str,
    offered_fact: str,
    now: str,
) -> int:
    """Log a revision once: the same offered fact against the same kept record is not
    logged again on later runs. Returns 1 if newly logged."""
    if c.execute(
        "SELECT 1 FROM conflicts WHERE series = ? AND symbol = ? AND session = ? "
        "AND kept_hash = ? AND offered_fact_hash = ?",
        (*key, kept_hash, offered_fact),
    ).fetchone():
        return 0
    c.execute(
        "INSERT INTO conflicts (series, symbol, session, kept_hash, offered_json, "
        "offered_fact_hash, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (*key, kept_hash, offered_json, offered_fact, now),
    )
    return 1


def _same_value(stored_json: str, offered: HistObservation) -> bool:
    """Same market fact: value, public time and details. A re-fetch differs only in
    ``ingest_time``, which is not a revision."""
    stored = HistObservation.from_canonical_json(stored_json)
    return (
        stored.value == offered.value
        and stored.public_time == offered.public_time
        and stored.details == offered.details
    )
