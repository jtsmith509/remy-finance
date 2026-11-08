"""Snapshot times SNAPTIMES-1 (DR-0002) and execution offsets (DR-0003), DST-aware."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from pydantic import Field

from remy_finance.core import Record, Session
from remy_finance.data.timeutil import NEW_YORK, xnys


class SnapshotKind(StrEnum):
    DECISION = "DECISION"
    EXECUTION = "EXECUTION"


class SnapshotSchedule(Record):
    version: str = Field(min_length=1)
    open_hhmm: tuple[int, int]
    close_hhmm: tuple[int, int]
    half_day_close_hhmm: tuple[int, int]
    execution_offset_minutes: int = Field(ge=0)


SNAPTIMES_1 = SnapshotSchedule(
    version="SNAPTIMES-1",
    open_hhmm=(10, 0),
    close_hhmm=(15, 45),
    half_day_close_hhmm=(12, 45),
    execution_offset_minutes=5,
)


def is_session(d: date) -> bool:
    return bool(xnys().is_session(d.isoformat()))


def is_half_day(d: date) -> bool:
    cal = xnys()
    close = cal.session_close(d.isoformat()).tz_convert(NEW_YORK)
    return bool(close.hour < 16)


def snapshot_time(
    d: date,
    session: Session,
    kind: SnapshotKind = SnapshotKind.DECISION,
    schedule: SnapshotSchedule = SNAPTIMES_1,
) -> datetime:
    """Aware UTC time of the snapshot for session date ``d``."""
    if not is_session(d):
        raise ValueError(f"{d} is not an XNYS trading session")
    if session is Session.OPEN:
        hh, mm = schedule.open_hhmm
    else:
        hh, mm = schedule.half_day_close_hhmm if is_half_day(d) else schedule.close_hhmm
    local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=NEW_YORK)
    if kind is SnapshotKind.EXECUTION:
        local += timedelta(minutes=schedule.execution_offset_minutes)
    return local.astimezone(UTC)
