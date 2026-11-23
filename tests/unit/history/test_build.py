"""HIST-1 backfill against a fake source: resumable, errors retried, missing recorded."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from remy_finance.core import CorporateAction, DividendEvent
from remy_finance.data.timeutil import et_at
from remy_finance.history import RATE_SYMBOL, HistoryView, HistSeries, HistStore
from remy_finance.history.build import build, sessions_between
from tests.unit.history.helpers import chain

START, END = date(2026, 11, 2), date(2026, 11, 6)


class Fake:
    def __init__(self) -> None:
        self.chain_calls = 0
        self.fail: set[tuple[str, date]] = set()

    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        return [
            {"created": f"{d.isoformat()}T17:15:00", "close": 100}
            for d in sessions_between(start, end)
        ]

    def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]:
        self.chain_calls += 1
        if (symbol, session) in self.fail:
            raise ConnectionError("terminal hiccup")
        vols = {14: 0.3, 45: 0.3} if symbol == "AAA" else {40: 0.3, 50: 0.3}
        return [
            {"expiration": r.expiration.isoformat(), "strike": r.strike,
             "right": "C" if r.call else "P", "bid": r.bid, "ask": r.ask}
            for r in chain(session, 100.0, vols)
        ]  # fmt: skip

    def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]:
        return [
            {
                "date": d.isoformat(),
                "yield_1_month": 4.0,
                "yield_3_month": 4.0,
                "yield_10_year": 4.2,
            }
            for d in sessions_between(start, end)
        ]

    def events(self, symbols: list[str]) -> list[DividendEvent | CorporateAction]:
        return []


def test_build_is_resumable_and_retries_fetch_errors(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = Fake()
    fake.fail = {("AAA", date(2026, 11, 4))}
    s1 = build(store, fake, ["AAA", "BBB"], START, END, workers=2, log=lambda _: None)
    assert s1.closes_added == 10 and s1.yields_added > 0
    # Yields are fetched from 14 days before the start, so every session has a public rate;
    # AAA's 4 November chain fails to fetch and is retried on the next run.
    assert s1.iv_added == 4 and len(s1.fetch_errors) == 1
    assert {m[3] for m in store.missing(HistSeries.ATMIV30) if m[1] == "BBB"} == {"NO_BRACKET_30D"}
    iv = HistoryView(store, et_at(END, 18)).value(HistSeries.ATMIV30, "AAA", date(2026, 11, 5))
    assert iv is not None and abs(float(iv) - 0.3) < 1e-4
    assert HistoryView(store, et_at(END, 18)).latest(HistSeries.YIELD_10Y, RATE_SYMBOL)
    fake.fail.clear()
    calls = fake.chain_calls
    s2 = build(store, fake, ["AAA", "BBB"], START, END, workers=2, log=lambda _: None)
    assert s2.closes_added == 0 and s2.iv_added == 1 and s2.fetch_errors == []
    assert fake.chain_calls - calls == 1  # only the failed session is fetched again
