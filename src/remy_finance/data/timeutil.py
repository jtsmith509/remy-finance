"""US/Eastern <-> UTC helpers. Vendors stamp times as naive ET wall-clock strings."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


def require_aware(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"timezone-aware datetime required, got {ts!r}")
    return ts


def et_naive_to_utc(text: str) -> datetime:
    """Parse a vendor ET wall-clock timestamp (e.g. ``2026-11-06T09:59:55.898``) to UTC."""
    naive = datetime.fromisoformat(text)
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    return naive.replace(tzinfo=NEW_YORK).astimezone(UTC)


def et_date(ts: datetime) -> date:
    return require_aware(ts).astimezone(NEW_YORK).date()


def et_time_of_day(ts: datetime) -> str:
    """``HH:MM:SS.mmm`` in ET, the ``time_of_day`` format ThetaData v3 expects."""
    t = require_aware(ts).astimezone(NEW_YORK)
    return f"{t:%H:%M:%S}.{t.microsecond // 1000:03d}"


def et_at(d: date, hh: int, mm: int = 0) -> datetime:
    """A wall-clock time on date ``d`` in New York, as aware UTC (DST-aware)."""
    return datetime.combine(d, time(hh, mm), tzinfo=NEW_YORK).astimezone(UTC)


@lru_cache(maxsize=1)
def xnys() -> Any:
    """The NYSE calendar (holidays, half-days), built once per process."""
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def next_session(d: date) -> date:
    """The first XNYS session strictly after ``d``."""
    nxt = xnys().date_to_session((d + timedelta(days=1)).isoformat(), direction="next")
    return date(nxt.year, nxt.month, nxt.day)
