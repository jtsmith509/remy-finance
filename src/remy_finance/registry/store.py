"""The paper-trade registry: an append-only SQLite store with a hash chain (handoff R1.1, D12).

Two tables, ``recommendations`` and ``journal``. Each row stores the record's canonical
JSON, its SHA-256 (``record_hash``), the previous row's chain hash (``prev_chain``) and its
own chain hash ``chain_hash = sha256(prev_chain + "\\n" + record_json)``. The first row of
each table chains from ``GENESIS``. SQLite triggers abort any UPDATE or DELETE on every
table, including the ``meta`` table that stamps the database's mode.

Prospective and replay records live in separate database files (``registry_path``); a
database refuses records of the other mode.

The chain makes edits detectable, but someone with file access could drop the triggers,
truncate the newest rows and leave a valid shorter chain. The nightly export (``export``)
is the external anchor against that: ``verify(..., export_dir=...)`` checks that every
exported row is still in the database, byte for byte.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from remy_finance.core import Mode
from remy_finance.core.canonical import sha256_hex
from remy_finance.registry.lifecycle import LifecycleError, check_next
from remy_finance.registry.records import JournalEvent, Recommendation

SCHEMA_VERSION = "REGISTRY-1"
GENESIS = "0" * 64
Table = Literal["recommendations", "journal"]
TABLES: tuple[Table, ...] = ("recommendations", "journal")

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE recommendations (
    seq         INTEGER PRIMARY KEY,
    trade_id    TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    prev_chain  TEXT NOT NULL,
    chain_hash  TEXT NOT NULL UNIQUE
) STRICT;
CREATE TABLE journal (
    seq         INTEGER PRIMARY KEY,
    trade_id    TEXT NOT NULL REFERENCES recommendations(trade_id),
    kind        TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    prev_chain  TEXT NOT NULL,
    chain_hash  TEXT NOT NULL UNIQUE
) STRICT;
CREATE INDEX journal_trade ON journal(trade_id, seq);
"""

IMMUTABLE_TABLES = ("meta", "recommendations", "journal")
CHAINED_TABLES = ("recommendations", "journal")
TRIGGERS = (
    *(f"{t}_no_{op}" for t in IMMUTABLE_TABLES for op in ("update", "delete")),
    "meta_no_insert",
    *(f"{t}_next_row_only" for t in CHAINED_TABLES),
)


def _trigger_sql() -> str:
    """UPDATE and DELETE always abort. INSERT must be exactly the next row of the chain:
    ``INSERT OR REPLACE`` deletes a conflicting row *without* firing DELETE triggers, so the
    insert trigger refuses any row that would collide with an existing one."""
    parts = [
        f"CREATE TRIGGER {t}_no_{op.lower()} BEFORE {op} ON {t} "
        f"BEGIN SELECT RAISE(ABORT, 'registry is append-only: {op} on {t} refused'); END;"
        for t in IMMUTABLE_TABLES
        for op in ("UPDATE", "DELETE")
    ]
    parts.append(
        "CREATE TRIGGER meta_no_insert BEFORE INSERT ON meta "
        "BEGIN SELECT RAISE(ABORT, 'registry is append-only: INSERT on meta refused'); END;"
    )
    for t in CHAINED_TABLES:
        unique_trade = f" OR EXISTS (SELECT 1 FROM {t} WHERE trade_id = NEW.trade_id)"
        parts.append(
            f"CREATE TRIGGER {t}_next_row_only BEFORE INSERT ON {t} WHEN "
            f"NEW.seq IS NOT (SELECT COALESCE(MAX(seq), 0) + 1 FROM {t}) "
            f"OR NEW.prev_chain IS NOT COALESCE("
            f"(SELECT chain_hash FROM {t} ORDER BY seq DESC LIMIT 1), '{GENESIS}') "
            f"OR EXISTS (SELECT 1 FROM {t} WHERE chain_hash = NEW.chain_hash)"
            f"{unique_trade if t == 'recommendations' else ''} "
            f"BEGIN SELECT RAISE(ABORT, 'registry is append-only: "
            f"{t} insert is not the next row of the chain'); END;"
        )
    return "\n".join(parts)


def chain_hash(prev_chain: str, record_json: str) -> str:
    return sha256_hex(prev_chain + "\n" + record_json)


def registry_path(data_dir: Path, mode: Mode) -> Path:
    """``data/registry/prospective.sqlite`` or ``data/registry/replay.sqlite``."""
    return data_dir / "registry" / f"{mode.value.lower()}.sqlite"


class RegistryError(ValueError):
    """A record the registry refuses (wrong mode, unknown trade, bad link...)."""


@dataclass(frozen=True)
class Row:
    table: Table
    seq: int
    trade_id: str
    record_json: str
    record_hash: str
    prev_chain: str
    chain_hash: str

    def export_line(self) -> str:
        return json.dumps(
            {
                "table": self.table,
                "seq": self.seq,
                "trade_id": self.trade_id,
                "record_hash": self.record_hash,
                "prev_chain": self.prev_chain,
                "chain_hash": self.chain_hash,
                "record_json": self.record_json,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


@dataclass
class VerifyReport:
    mode: Mode | None
    rows: dict[str, int] = field(default_factory=dict)
    heads: dict[str, str] = field(default_factory=dict)
    exported_rows_checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class Registry:
    """One registry database (one mode). Use ``Registry.create`` once, then ``open``."""

    def __init__(self, path: Path, conn: sqlite3.Connection, mode: Mode) -> None:
        self.path = path
        self._conn = conn
        self.mode = mode

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def create(cls, path: Path, mode: Mode) -> Registry:
        if path.exists():
            raise FileExistsError(f"registry already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(path)
        with conn:
            meta = (
                f"INSERT INTO meta(key, value) VALUES ('mode', '{mode.value}'), "
                f"('schema', '{SCHEMA_VERSION}');"
            )
            conn.executescript("BEGIN;" + _SCHEMA + meta + _trigger_sql() + "COMMIT;")
        return cls(path, conn, mode)

    @classmethod
    def open(cls, path: Path, mode: Mode | None = None) -> Registry:
        if not path.exists():
            raise FileNotFoundError(f"no registry at {path}")
        conn = _connect(path)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != SCHEMA_VERSION:
            raise RegistryError(f"{path}: schema {meta.get('schema')!r}, need {SCHEMA_VERSION}")
        stored = Mode(meta["mode"])
        if mode is not None and stored is not mode:
            raise RegistryError(f"{path} is a {stored.value} registry, not {mode.value}")
        return cls(path, conn, stored)

    @classmethod
    def open_or_create(cls, path: Path, mode: Mode) -> Registry:
        return cls.open(path, mode) if path.exists() else cls.create(path, mode)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """A write transaction that takes the lock up front, so reading the chain head and
        inserting the next row can't interleave with another writer."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ appends

    def add_recommendation(self, rec: Recommendation) -> Row:
        if rec.mode is not self.mode:
            raise RegistryError(
                f"{rec.mode.value} record refused by the {self.mode.value} registry"
            )
        text = rec.canonical_json()
        with self._write() as conn:
            if conn.execute(
                "SELECT 1 FROM recommendations WHERE trade_id = ?", (rec.trade_id,)
            ).fetchone():
                raise RegistryError(f"trade {rec.trade_id} is already recorded")
            return _insert(conn, "recommendations", rec.trade_id, text)

    def add_event(self, event: JournalEvent) -> Row:
        if event.mode is not self.mode:
            raise RegistryError(
                f"{event.mode.value} event refused by the {self.mode.value} registry"
            )
        text = event.canonical_json()
        with self._write() as conn:
            if not conn.execute(
                "SELECT 1 FROM recommendations WHERE trade_id = ?", (event.trade_id,)
            ).fetchone():
                raise RegistryError(f"unknown trade {event.trade_id}")
            history = self._events(conn, event.trade_id)
            if event.corrects_seq is not None:
                target = conn.execute(
                    "SELECT trade_id FROM journal WHERE seq = ?", (event.corrects_seq,)
                ).fetchone()
                if target is None or target[0] != event.trade_id:
                    raise RegistryError(
                        f"CORRECTION links journal seq {event.corrects_seq}, "
                        f"which is not an event of trade {event.trade_id}"
                    )
            try:
                check_next([e for _, e in history], event)
            except LifecycleError as e:
                raise RegistryError(str(e)) from e
            return _insert(conn, "journal", event.trade_id, text, kind=event.kind.value)

    # ------------------------------------------------------------------ reads

    def recommendation(self, trade_id: str) -> Recommendation | None:
        row = self._conn.execute(
            "SELECT record_json FROM recommendations WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return None if row is None else Recommendation.from_canonical_json(row[0])

    def recommendations(self) -> list[Recommendation]:
        rows = self._conn.execute("SELECT record_json FROM recommendations ORDER BY seq")
        return [Recommendation.from_canonical_json(r[0]) for r in rows]

    def events(self, trade_id: str) -> list[tuple[int, JournalEvent]]:
        return self._events(self._conn, trade_id)

    @staticmethod
    def _events(conn: sqlite3.Connection, trade_id: str) -> list[tuple[int, JournalEvent]]:
        rows = conn.execute(
            "SELECT seq, record_json FROM journal WHERE trade_id = ? ORDER BY seq", (trade_id,)
        )
        return [(int(s), JournalEvent.from_canonical_json(j)) for s, j in rows]

    def rows(self, table: Table, after_seq: int = 0) -> list[Row]:
        cur = self._conn.execute(
            f"SELECT seq, trade_id, record_json, record_hash, prev_chain, chain_hash "
            f"FROM {table} WHERE seq > ? ORDER BY seq",
            (after_seq,),
        )
        return [Row(table, int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in cur]

    def head(self, table: Table) -> tuple[int, str]:
        return _head(self._conn, table)

    # ------------------------------------------------------------------ verify

    def verify(self, export_dir: Path | None = None) -> VerifyReport:
        """Recompute everything: record hashes, both chains, canonical round trips, modes,
        links, lifecycle order and the presence of the append-only triggers. With
        ``export_dir``, also check every exported row is still present, byte for byte."""
        report = VerifyReport(mode=self.mode)
        problems = report.problems
        meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("mode") != self.mode.value or meta.get("schema") != SCHEMA_VERSION:
            problems.append(f"meta table altered: {meta}")
        triggers = {
            r[0]
            for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
        for name in TRIGGERS:
            if name not in triggers:
                problems.append(f"append-only trigger {name} is missing")

        trade_ids: set[str] = set()
        by_seq: dict[int, str] = {}
        histories: dict[str, list[JournalEvent]] = defaultdict(list)
        for table in TABLES:
            prev = GENESIS
            rows = self.rows(table)
            report.rows[table] = len(rows)
            for expected_seq, row in enumerate(rows, start=1):
                where = f"{table} seq {row.seq}"
                if row.seq != expected_seq:
                    problems.append(f"{where}: sequence gap (expected {expected_seq})")
                if sha256_hex(row.record_json) != row.record_hash:
                    problems.append(f"{where}: record_hash does not match record_json")
                if row.prev_chain != prev:
                    problems.append(f"{where}: prev_chain does not link to the previous row")
                if chain_hash(row.prev_chain, row.record_json) != row.chain_hash:
                    problems.append(f"{where}: chain_hash is wrong")
                prev = row.chain_hash
                problems.extend(
                    f"{where}: {p}" for p in self._check_record(row, trade_ids, by_seq, histories)
                )
            report.heads[table] = prev
        if export_dir is not None:
            report.exported_rows_checked = self._check_export(export_dir, problems)
        return report

    def _check_record(
        self,
        row: Row,
        trade_ids: set[str],
        by_seq: dict[int, str],
        histories: dict[str, list[JournalEvent]],
    ) -> list[str]:
        out: list[str] = []
        record: Recommendation | JournalEvent
        try:
            if row.table == "recommendations":
                record = Recommendation.from_canonical_json(row.record_json)
            else:
                record = JournalEvent.from_canonical_json(row.record_json)
        except ValueError as e:
            return [f"record does not validate: {e}"]
        if record.canonical_json() != row.record_json:
            out.append("record_json is not canonical")
        if record.mode is not self.mode:
            out.append(f"{record.mode.value} record in a {self.mode.value} registry")
        if record.trade_id != row.trade_id:
            out.append("trade_id column differs from the record")
        if isinstance(record, Recommendation):
            if record.trade_id in trade_ids:
                out.append(f"duplicate trade {record.trade_id}")
            trade_ids.add(record.trade_id)
            return out
        if record.trade_id not in trade_ids:
            out.append(f"event for unknown trade {record.trade_id}")
        if record.corrects_seq is not None and by_seq.get(record.corrects_seq) != record.trade_id:
            out.append(f"CORRECTION links seq {record.corrects_seq} outside trade")
        history = histories[record.trade_id]
        try:
            check_next(history, record)
        except LifecycleError as e:
            out.append(str(e))
        history.append(record)
        by_seq[row.seq] = record.trade_id
        return out

    def _check_export(self, export_dir: Path, problems: list[str]) -> int:
        export_dir = export_dir / self.mode.value.lower()
        current = {(r.table, r.seq): r for t in TABLES for r in self.rows(t)}
        checked = 0
        for path in sorted(export_dir.glob("*/*/*.jsonl")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                data = json.loads(line)
                key = (data["table"], int(data["seq"]))
                row = current.get(key)
                if row is None or row.export_line() != line:
                    problems.append(f"{path.name}:{n}: exported {key} is missing or changed")
                checked += 1
        return checked

    # ------------------------------------------------------------------ export, backup

    def export(self, export_dir: Path, day: date) -> list[Row]:
        """Write rows not yet exported to ``export_dir/<mode>/YYYY/MM/DD.jsonl`` (one JSON line
        per row, recommendations then journal, in sequence order). Earlier lines are kept
        unchanged; the file is replaced atomically. Returns the rows written."""
        export_dir = export_dir / self.mode.value.lower()
        exported = _exported_heads(export_dir)
        new = [r for t in TABLES for r in self.rows(t, exported.get(t, 0))]
        if not new:
            return []
        path = export_dir / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}.jsonl"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        text = old + "".join(r.export_line() + "\n" for r in new)
        _replace_atomically(path, text)
        return new

    def backup(self, backup_dir: Path, now: datetime | None = None) -> Path:
        """A consistent copy via SQLite's online backup, named by UTC time (DR-0012)."""
        stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = backup_dir / f"{self.mode.value.lower()}-{stamp}.sqlite"
        if dest.exists():
            raise FileExistsError(dest)
        backup_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(dest) as target:
            self._conn.backup(target)
        target.close()
        return dest


# ---------------------------------------------------------------------- helpers


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def _head(conn: sqlite3.Connection, table: Table) -> tuple[int, str]:
    row = conn.execute(f"SELECT seq, chain_hash FROM {table} ORDER BY seq DESC LIMIT 1").fetchone()
    return (0, GENESIS) if row is None else (int(row[0]), str(row[1]))


def _insert(
    conn: sqlite3.Connection, table: Table, trade_id: str, text: str, kind: str | None = None
) -> Row:
    seq, prev = _head(conn, table)
    row = Row(table, seq + 1, trade_id, text, sha256_hex(text), prev, chain_hash(prev, text))
    cols = ["seq", "trade_id", "record_json", "record_hash", "prev_chain", "chain_hash"]
    values: list[object] = [row.seq, trade_id, text, row.record_hash, prev, row.chain_hash]
    if kind is not None:
        cols.append("kind")
        values.append(kind)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        values,
    )
    return row


def _exported_heads(export_dir: Path) -> dict[str, int]:
    heads: dict[str, int] = {}
    for path in export_dir.glob("*/*/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            heads[data["table"]] = max(heads.get(data["table"], 0), int(data["seq"]))
    return heads


def _replace_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def summarize(rows: Sequence[Row]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[r.table] += 1
    return dict(out)
