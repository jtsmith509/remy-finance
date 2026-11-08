"""Immutable, content-addressed snapshot store (handoff R0.4, DR-0012).

Layout under ``root`` (normally ``data/snapshots``)::

    prospective/2026-11-06/OPEN-DECISION-<id16>/{options.parquet, board.json, manifest.json}
    replay/...
    prospective/gaps.jsonl

- ``options.parquet``: one row per option quote, every number as a canonical string
  (no floats). ``board.json``: the canonical JSON of the board without option quotes.
- ``manifest.json``: snapshot id, labels, file SHA-256s. Loading recomputes both.
- Write-once: a snapshot for the same (mode, date, session, kind) is never overwritten.
- A missed prospective snapshot gets a gap record; once a gap is recorded, that slot can
  never be filled (no backfill into the prospective record).
- Replay and prospective live in separate trees and never mix.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import Field

from remy_finance.core import (
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    QualityFlag,
    Record,
    Session,
)
from remy_finance.core.canonical import canonical_datetime, canonical_decimal
from remy_finance.core.types import UtcDatetime
from remy_finance.data.timeutil import et_date
from remy_finance.snapshots.schedule import SNAPTIMES_1, SnapshotKind, snapshot_time

STORE_VERSION = "SNAPSTORE-1"


class SnapshotExistsError(RuntimeError):
    pass


class GapRecordedError(RuntimeError):
    pass


class SnapshotIntegrityError(RuntimeError):
    pass


class SnapshotManifest(Record):
    store_version: str
    snapshot_id: str
    mode: Mode
    session: Session
    kind: SnapshotKind
    session_date: date
    as_of: UtcDatetime
    scheduled_time: UtcDatetime
    universe_version: str
    decision_snapshot_id: str | None = None
    n_option_quotes: int = Field(ge=0)
    n_underlyings: int = Field(ge=0)
    n_quality_notes: int = Field(ge=0)
    files: tuple[tuple[str, str], ...]  # (name, sha256)
    schedule_version: str
    written_at: UtcDatetime


class GapRecord(Record):
    session_date: date
    session: Session
    kind: SnapshotKind
    reason: str = Field(min_length=1)
    recorded_at: UtcDatetime


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _options_frame(quotes: tuple[OptionQuote, ...]) -> pl.DataFrame:
    def opt(dt: datetime | None) -> str | None:
        return None if dt is None else canonical_datetime(dt)

    return pl.DataFrame(
        {
            "osi_symbol": [q.contract.osi_symbol for q in quotes],
            "underlying": [q.contract.underlying for q in quotes],
            "expiration": [q.contract.expiration.isoformat() for q in quotes],
            "strike": [canonical_decimal(q.contract.strike) for q in quotes],
            "right": [q.contract.right.value for q in quotes],
            "style": [q.contract.style.value for q in quotes],
            "multiplier": [q.contract.multiplier for q in quotes],
            "standard_deliverable": [q.contract.standard_deliverable for q in quotes],
            "bid": [canonical_decimal(q.bid) for q in quotes],
            "ask": [canonical_decimal(q.ask) for q in quotes],
            "bid_size": [q.bid_size for q in quotes],
            "ask_size": [q.ask_size for q in quotes],
            "quote_time": [canonical_datetime(q.quote_time) for q in quotes],
            "ingest_time": [opt(q.ingest_time) for q in quotes],
            "source": [q.source for q in quotes],
            "quality_flags": [",".join(f.value for f in q.quality_flags) for q in quotes],
        },
        schema={
            "osi_symbol": pl.String,
            "underlying": pl.String,
            "expiration": pl.String,
            "strike": pl.String,
            "right": pl.String,
            "style": pl.String,
            "multiplier": pl.Int64,
            "standard_deliverable": pl.Boolean,
            "bid": pl.String,
            "ask": pl.String,
            "bid_size": pl.Int64,
            "ask_size": pl.Int64,
            "quote_time": pl.String,
            "ingest_time": pl.String,
            "source": pl.String,
            "quality_flags": pl.String,
        },
    )


def _quotes_from_frame(df: pl.DataFrame) -> tuple[OptionQuote, ...]:
    contracts: dict[str, OptionContract] = {}
    out: list[OptionQuote] = []
    for r in df.iter_rows(named=True):
        c = contracts.get(r["osi_symbol"])
        if c is None:
            c = OptionContract(
                osi_symbol=r["osi_symbol"],
                underlying=r["underlying"],
                expiration=r["expiration"],
                strike=r["strike"],
                right=r["right"],
                style=r["style"],
                multiplier=r["multiplier"],
                standard_deliverable=r["standard_deliverable"],
            )
            contracts[r["osi_symbol"]] = c
        flags = tuple(QualityFlag(f) for f in r["quality_flags"].split(",") if f)
        out.append(
            OptionQuote(
                contract=c,
                bid=r["bid"],
                ask=r["ask"],
                bid_size=r["bid_size"],
                ask_size=r["ask_size"],
                quote_time=r["quote_time"],
                ingest_time=r["ingest_time"],
                source=r["source"],
                quality_flags=flags,
            )
        )
    return tuple(out)


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------ paths

    def _mode_dir(self, mode: Mode) -> Path:
        return self.root / mode.value.lower()

    def _gap_file(self) -> Path:
        return self._mode_dir(Mode.PROSPECTIVE) / "gaps.jsonl"

    def find(
        self, mode: Mode, session_date: date, session: Session, kind: SnapshotKind
    ) -> Path | None:
        day = self._mode_dir(mode) / session_date.isoformat()
        hits = sorted(day.glob(f"{session.value}-{kind.value}-*")) if day.exists() else []
        if len(hits) > 1:
            raise SnapshotIntegrityError(
                f"more than one snapshot for {mode} {session_date} {session} {kind}"
            )
        return hits[0] if hits else None

    # ------------------------------------------------------------------- gaps

    def gaps(self) -> list[GapRecord]:
        path = self._gap_file()
        if not path.exists():
            return []
        return [
            GapRecord.model_validate(json.loads(line))
            for line in path.read_text().splitlines()
            if line
        ]

    def has_gap(self, session_date: date, session: Session, kind: SnapshotKind) -> bool:
        return any(
            (g.session_date, g.session, g.kind) == (session_date, session, kind)
            for g in self.gaps()
        )

    def record_gap(
        self,
        session_date: date,
        session: Session,
        kind: SnapshotKind,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> GapRecord:
        if self.find(Mode.PROSPECTIVE, session_date, session, kind) is not None:
            raise SnapshotExistsError(
                "a prospective snapshot exists for this slot; it is not a gap"
            )
        if self.has_gap(session_date, session, kind):
            return next(
                g
                for g in self.gaps()
                if (g.session_date, g.session, g.kind) == (session_date, session, kind)
            )
        gap = GapRecord(
            session_date=session_date,
            session=session,
            kind=kind,
            reason=reason,
            recorded_at=now or datetime.now(UTC),
        )
        path = self._gap_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(gap.canonical_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return gap

    # ------------------------------------------------------------------ write

    def write(
        self,
        state: MarketState,
        *,
        kind: SnapshotKind = SnapshotKind.DECISION,
        decision_snapshot_id: str | None = None,
        max_delay: timedelta = timedelta(minutes=10),
        now: datetime | None = None,
    ) -> SnapshotManifest:
        session_date = et_date(state.as_of)
        scheduled = snapshot_time(session_date, state.session, kind)
        if state.mode is Mode.PROSPECTIVE:
            if self.has_gap(session_date, state.session, kind):
                raise GapRecordedError(
                    "a gap is recorded for this slot; prospective snapshots are never backfilled"
                )
            delay = state.as_of - scheduled
            if not timedelta(0) <= delay <= max_delay:
                raise ValueError(
                    f"prospective as_of is {delay} from the scheduled time (allowed 0..{max_delay})"
                )
        elif state.as_of != scheduled:
            raise ValueError(
                f"replay as_of {state.as_of} must equal the scheduled time {scheduled}"
            )
        if (kind is SnapshotKind.EXECUTION) != (decision_snapshot_id is not None):
            raise ValueError("execution snapshots, and only they, must link a decision snapshot")
        if decision_snapshot_id is not None:
            decision = self.find(state.mode, session_date, state.session, SnapshotKind.DECISION)
            if decision is None or self.load_manifest(decision).snapshot_id != decision_snapshot_id:
                raise ValueError("linked decision snapshot not found in this store")
        if self.find(state.mode, session_date, state.session, kind) is not None:
            raise SnapshotExistsError(
                f"snapshot already stored for {state.mode} {session_date} {state.session} {kind}"
            )

        sid = state.snapshot_id
        final = (
            self._mode_dir(state.mode)
            / session_date.isoformat()
            / f"{state.session.value}-{kind.value}-{sid[:16]}"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=final.parent))
        try:
            _options_frame(state.option_quotes).write_parquet(
                tmp / "options.parquet", compression="zstd"
            )
            rest = state.model_copy(update={"option_quotes": ()})
            (tmp / "board.json").write_text(rest.canonical_json(), encoding="utf-8")
            files = tuple((name, _sha256(tmp / name)) for name in ("board.json", "options.parquet"))
            manifest = SnapshotManifest(
                store_version=STORE_VERSION,
                snapshot_id=sid,
                mode=state.mode,
                session=state.session,
                kind=kind,
                session_date=session_date,
                as_of=state.as_of,
                scheduled_time=scheduled,
                universe_version=state.universe_version,
                decision_snapshot_id=decision_snapshot_id,
                n_option_quotes=len(state.option_quotes),
                n_underlyings=len(state.underlyings),
                n_quality_notes=len(state.quality_notes),
                files=files,
                schedule_version=SNAPTIMES_1.version,
                written_at=now or datetime.now(UTC),
            )
            (tmp / "manifest.json").write_text(manifest.canonical_json(), encoding="utf-8")
            for p in tmp.iterdir():
                p.chmod(0o444)  # read-only on disk as a guard against accidents
            os.rename(tmp, final)  # atomic; fails if ``final`` exists
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return manifest

    # ------------------------------------------------------------------- read

    @staticmethod
    def load_manifest(path: Path) -> SnapshotManifest:
        return SnapshotManifest.from_canonical_json(
            (path / "manifest.json").read_text(encoding="utf-8")
        )

    def load(self, path: Path) -> MarketState:
        """Load and verify: file hashes and the recomputed snapshot id must match."""
        manifest = self.load_manifest(path)
        for name, digest in manifest.files:
            if _sha256(path / name) != digest:
                raise SnapshotIntegrityError(f"{path / name}: SHA-256 mismatch")
        rest: dict[str, Any] = json.loads((path / "board.json").read_text(encoding="utf-8"))
        rest["option_quotes"] = _quotes_from_frame(pl.read_parquet(path / "options.parquet"))
        state = MarketState.model_validate(rest)
        if state.snapshot_id != manifest.snapshot_id:
            raise SnapshotIntegrityError(f"{path}: snapshot id mismatch")
        return state
