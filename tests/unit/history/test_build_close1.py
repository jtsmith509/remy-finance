"""HIST-1 build under DR-0046: never stores before public time; absent closes; deadline."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from remy_finance.data.timeutil import et_at
from remy_finance.history import HistSeries, HistStore
from remy_finance.history.build import build
from remy_finance.history.closes import StoreFirstCloses
from tests.unit.history.test_build import END, START, Fake

QUIET: Any = lambda _: None  # noqa: E731


class Intraday(Fake):
    """A vendor that serves a (provisional) row for today before 17:15."""

    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        rows = super().stock_eod(symbol, start, end)
        return [{**r, "close": 99} if r["created"].startswith(str(END)) else r for r in rows]


def test_nothing_is_stored_before_its_public_time(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    at = et_at(END, 14)
    s = build(store, Intraday(), ["AAA"], START, END, log=QUIET, clock=lambda: at)
    assert s.not_public == 1 and ("AAA", END) not in s.closes_absent
    assert END not in {o.session for o in store.observations(HistSeries.CLOSE, "AAA")}
    assert END not in store.sessions_present(HistSeries.ATMIV30, "AAA")
    assert all(o.ingest_time is None or o.ingest_time >= o.public_time
               for series in HistSeries for o in store.observations(series, "UST"))  # fmt: skip
    # After 17:15 the official value is stored, and readers see it, not the provisional one.
    after = et_at(END, 18)
    s2 = build(store, Fake(), ["AAA"], START, END, log=QUIET, clock=lambda: after)
    assert s2.not_public == 0 and s2.closes_added == 1 and s2.conflicts == 0
    got = StoreFirstCloses(store, None, clock=lambda: after).official_close("AAA", END)
    assert got is not None and got.price == 100


class Late(Fake):
    def __init__(self) -> None:
        super().__init__()
        self.late = True

    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        rows = super().stock_eod(symbol, start, end)
        if symbol == "LATE" and self.late:
            rows = [r for r in rows if not r["created"].startswith(str(END))]
        if symbol == "LATE" and not self.late:
            rows = []  # the vendor no longer serves stored closes: still not absent
        return rows


def test_closes_absent_is_read_from_the_store(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = Late()
    at = et_at(END, 18)
    s1 = build(store, fake, ["AAA", "LATE"], START, END, with_iv=False, log=QUIET,
               clock=lambda: at)  # fmt: skip
    assert s1.closes_absent == [("LATE", END)]
    store.add_observations(
        o.model_copy(update={"session": END, "ingest_time": None})
        for o in store.observations(HistSeries.CLOSE, "LATE")[-1:]
    )
    fake.late = False
    s2 = build(store, fake, ["AAA", "LATE"], START, END, with_iv=False, log=QUIET,
               clock=lambda: at)  # fmt: skip
    assert s2.closes_absent == []


def test_a_deadline_stops_the_run_cleanly(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    t = [et_at(END, 18)]

    def clock() -> datetime:
        t[0] += timedelta(minutes=1)
        return t[0]

    s = build(store, Fake(), ["AAA", "BBB", "CCC"], START, END, log=QUIET, clock=clock,
              deadline=et_at(END, 18, 3))  # fmt: skip
    assert s.stopped_at_deadline and any(e.startswith("deadline") for e in s.fetch_errors)
    assert s.iv_added == 0  # stopped before the IV stage
    s2 = build(store, Fake(), ["AAA", "BBB", "CCC"], START, END, log=QUIET)
    assert not s2.stopped_at_deadline and s2.iv_added > 0  # the next run finishes the work
