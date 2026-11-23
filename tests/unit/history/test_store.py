"""HIST-1 store: append-only, idempotent, revisions logged as conflicts (DR-0045)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from remy_finance.history import HistError, HistSeries, HistStore
from tests.unit.history.helpers import obs

D = date(2026, 11, 2)


def test_append_only_and_idempotent(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    o = obs(HistSeries.CLOSE, "AAPL", D, "270.10", ingest=datetime(2026, 11, 3, tzinfo=UTC))
    assert store.add_observations([o]) == (1, 0)
    again = obs(HistSeries.CLOSE, "AAPL", D, "270.10", ingest=datetime(2026, 11, 9, tzinfo=UTC))
    assert store.add_observations([again]) == (0, 0)  # a re-fetch is not a revision
    revised = obs(HistSeries.CLOSE, "AAPL", D, "270.11")
    assert store.add_observations([revised]) == (0, 1)
    assert store.observations(HistSeries.CLOSE, "AAPL")[0].value == o.value  # original kept
    assert len(store.conflicts()) == 1
    with sqlite3.connect(store.path) as c:
        for sql in (
            "UPDATE obs SET record_json = 'x'",
            "DELETE FROM obs",
            "DELETE FROM conflicts",
            "INSERT OR REPLACE INTO obs SELECT * FROM obs",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                c.execute(sql)


def test_missing_is_recorded_once_and_counts_as_present(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    assert store.add_missing(HistSeries.ATMIV30, "KO", D, "NO_BRACKET_30D")
    assert not store.add_missing(HistSeries.ATMIV30, "KO", D, "OTHER")
    assert store.has(HistSeries.ATMIV30, "KO", D)
    assert D in store.sessions_present(HistSeries.ATMIV30, "KO")


def test_refuses_a_foreign_file(tmp_path: Path) -> None:
    p = tmp_path / "x.sqlite"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        c.execute("INSERT INTO meta VALUES ('schema', 'OTHER')")
    with pytest.raises(HistError):
        HistStore(p)
