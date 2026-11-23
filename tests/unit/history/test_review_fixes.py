"""Regressions for the HIST-1 adversarial review (docs/review/HIST-1-findings.md)."""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from remy_finance.core import CorporateAction, CorporateActionType, DividendEvent
from remy_finance.data.massive import MassiveSource
from remy_finance.data.timeutil import et_at
from remy_finance.history import HistError, HistoryView, HistSeries, HistStore
from remy_finance.history.atm_iv import atm_iv30, rows_from_vendor
from remy_finance.history.build import build
from remy_finance.history.store import _SCHEMA, SCHEMA_VERSION
from tests.unit.history.helpers import chain, obs, rates
from tests.unit.history.test_build import END, START, Fake

D1, D2, D3, D4, D5 = (date(2026, 11, d) for d in (2, 3, 4, 5, 6))
QUIET: Any = lambda _: None  # noqa: E731


class EventFake(Fake):
    """Flat closes of 100 with a $1 dividend ex D3 and a 2:1 split on D4; every call to
    ``events`` stamps a new ``ingest_time``, as MassiveSource does."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.amount = Decimal("1")
        self.closes = {D1: 100, D2: 100, D3: 99, D4: 49.5, D5: 49.5}
        self.unusable: list[tuple[str, date, str]] = []
        self.failures: list[str] = []

    def stock_eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        return [{"created": f"{d}T17:15:00", "close": c} for d, c in self.closes.items()]

    def events(self, symbols: list[str]) -> list[DividendEvent | CorporateAction]:
        self.calls += 1
        now = datetime(2026, 11, 23, tzinfo=UTC) + timedelta(days=self.calls)
        decl = et_at(D1, 16)
        split = et_at(D4, 9, 30)
        return [
            DividendEvent(symbol="AAA", ex_date=D3, amount=self.amount, declaration_date=D1,
                          event_time=decl, public_time=decl, ingest_time=now, source="m"),
            CorporateAction(symbol="AAA", action_type=CorporateActionType.SPLIT,
                            effective_date=D4, details=(("split_from", "1"), ("split_to", "2")),
                            event_time=split, public_time=split, ingest_time=now, source="m"),
        ]  # fmt: skip

    def unusable_dividends(self) -> list[tuple[str, date, str]]:
        return list(self.unusable)

    def event_failures(self) -> list[str]:
        return list(self.failures)


def _returns(store: HistStore) -> dict[date, float]:
    rets, _ = HistoryView(store, et_at(D5, 18)).total_returns("AAA", D2, D5)
    return {r.session: r.log_return for r in rets}


# ---------------------------------------------------------------- HIST1-PIT-01 (and dupes)


def test_rebuild_does_not_duplicate_events_or_double_returns(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = EventFake()
    s1 = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    first = _returns(store)
    s2 = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    assert (s1.events_added, s2.events_added, s2.conflicts) == (2, 0, 0)
    assert len(store.dividends("AAA")) == 1 and len(store.actions("AAA")) == 1
    assert _returns(store) == first
    assert first[D3] == pytest.approx(math.log((99 + 1) / 100))
    assert first[D4] == pytest.approx(math.log(2 * 49.5 / 99))


def test_revised_dividend_is_a_conflict_not_a_second_cash_flow(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = EventFake()
    build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    fake.amount = Decimal("1.25")
    s2 = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    s3 = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    assert (s2.events_added, s2.conflicts, s3.conflicts) == (0, 1, 0)  # logged once
    assert [d.amount for d in store.dividends("AAA")] == [Decimal("1")]
    assert _returns(store)[D3] == pytest.approx(math.log(100 / 100))
    assert [(c.series, c.session) for c in store.conflicts()] == [("DIVIDEND", D3)]


def test_ordinary_and_special_on_one_ex_date_are_both_kept(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    t = et_at(D1, 16)
    common: dict[str, Any] = dict(symbol="AAA", ex_date=D3, event_time=t, public_time=t,
                                  source="m")  # fmt: skip
    special = DividendEvent(amount=Decimal("5"), dividend_type="SPECIAL", **common)
    assert store.add_events([DividendEvent(amount=Decimal("1"), **common), special]) == (2, 0)


# ------------------------------------------------------------------------------ H4


def test_observation_conflict_is_logged_once(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D1, "100")])
    got = [store.add_observations([obs(HistSeries.CLOSE, "AAA", D1, "100.5")]) for _ in range(3)]
    assert got == [(0, 1), (0, 0), (0, 0)]
    assert store.add_observations([obs(HistSeries.CLOSE, "AAA", D1, "101")]) == (0, 1)
    assert len(store.conflicts()) == 2


# -------------------------------------------------------------------- HIST1-PIT-02 / H2


class NoYields(Fake):
    def __init__(self) -> None:
        super().__init__()
        self.yields_down = True

    def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]:
        if self.yields_down:
            raise ConnectionError("503 from /fed/v1/treasury-yields")
        return super().treasury_yields(start, end)


def test_yield_fetch_failure_is_retried_not_recorded_missing(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = NoYields()
    s1 = build(store, fake, ["AAA"], START, END, workers=2, log=QUIET)
    assert store.missing(HistSeries.ATMIV30) == [] and s1.iv_added == 0
    assert s1.iv_missing == 0 and len(s1.fetch_errors) == 1 + 5
    assert fake.chain_calls == 0  # no rates, no chain request
    fake.yields_down = False
    s2 = build(store, fake, ["AAA"], START, END, workers=2, log=QUIET)
    assert s2.iv_added == 5 and s2.fetch_errors == []


def test_stale_rates_count_as_missing_input(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    old = date(2026, 10, 1)
    store.add_observations(
        [
            obs(s, "UST", old, "0.04", public=et_at(date(2026, 10, 2), 9, 30))
            for s in (HistSeries.YIELD_1M, HistSeries.YIELD_3M)
        ]
    )

    class Stale(NoYields):
        def treasury_yields(self, start: date, end: date) -> list[dict[str, Any]]:
            return []

    s = build(store, Stale(), ["AAA"], START, END, log=QUIET)
    assert s.iv_added == 0 and store.missing() == []
    assert all("no public YIELD_1M" in e for e in s.fetch_errors)


# ------------------------------------------------------------------------------ H3


class EmptyOnce(Fake):
    def __init__(self) -> None:
        super().__init__()
        self.empty = {("AAA", D3)}

    def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]:
        if (symbol, session) in self.empty:
            self.empty.discard((symbol, session))
            return []
        return super().option_eod_chain(symbol, session)


def test_empty_chain_is_a_fetch_error_and_retried(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = EmptyOnce()
    s1 = build(store, fake, ["AAA"], START, END, log=QUIET)
    assert store.missing() == [] and s1.iv_added == 4
    assert [e for e in s1.fetch_errors if "EMPTY_CHAIN" in e] == s1.fetch_errors != []
    s2 = build(store, fake, ["AAA"], START, END, log=QUIET)
    assert s2.iv_added == 1


# ------------------------------------------------------------------- IV-01 / NONSTD-ROOT


def test_nonstandard_root_does_not_contaminate_the_chain() -> None:
    def vendor(root: str, scale: float) -> list[dict[str, Any]]:
        return [
            {"symbol": root, "expiration": r.expiration.isoformat(), "strike": r.strike,
             "right": "C" if r.call else "P", "bid": r.bid * scale, "ask": r.ask * scale}
            for r in chain(D1, 100.0, {14: 0.3, 45: 0.3})
        ]  # fmt: skip

    clean = atm_iv30(rows_from_vendor(vendor("XYZ", 1.0), "XYZ"), 100.0, D1, rates())
    mixed_raw = vendor("XYZ", 1.0) + vendor("XYZ1", 0.8)
    mixed = atm_iv30(rows_from_vendor(mixed_raw, "XYZ"), 100.0, D1, rates())
    assert clean.iv30 == pytest.approx(0.3, abs=1e-4)
    assert mixed.iv30 == clean.iv30
    assert rows_from_vendor(vendor("XYZ1", 1.0), "XYZ") == []
    assert rows_from_vendor(vendor("XY", 1.0), "XYZ") == []


# ------------------------------------------------------------------ TR-2 / H1-DIV-SKIP


def test_unusable_dividend_excludes_the_ex_date_from_its_open(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    fake = EventFake()
    fake.unusable = [("AAA", D2, "NO_DECLARATION_DATE")]
    fake.closes[D2] = 95  # the undeclared dividend's price drop
    s = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    again = build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    assert (s.unusable_added, again.unusable_added) == (1, 0)
    rets, excl = HistoryView(store, et_at(D5, 18)).total_returns("AAA", D2, D2)
    assert rets == [] and [e.reason for e in excl] == ["DIVIDEND_UNUSABLE:NO_DECLARATION_DATE"]
    rets, excl = HistoryView(store, et_at(D2, 9, 29)).total_returns("AAA", D2, D2)
    assert rets == [] and excl == []  # before D2's open: neither the close nor the flag


def test_event_fetch_failures_are_reported_for_retry(tmp_path: Path) -> None:
    fake = EventFake()
    fake.failures = ["AAA: ticker events unavailable (503)"]
    s = build(HistStore(tmp_path / "h.sqlite"), fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    assert s.fetch_errors == ["events: AAA: ticker events unavailable (503)"]


def test_massive_records_unusable_dividends_in_structured_form() -> None:
    src = MassiveSource(api_key="x", sleep=lambda _: None)
    rows = [
        {"ticker": "HYG", "ex_dividend_date": "2026-11-03", "cash_amount": "0.38"},
        {"ticker": "HYG", "ex_dividend_date": "2026-11-04", "cash_amount": "0.38",
         "declaration_date": "2026-10-20", "currency": "CAD"},
    ]  # fmt: skip
    assert src._parse_dividends("HYG", rows) == ()  # ASSIGN-1: both skipped (DR-0027)
    assert src.unusable_dividends == []
    kept = src._parse_dividends("HYG", rows, keep_undeclared=True)  # HIST-1 (DR-0045)
    assert [(d.ex_date, d.declaration_date) for d in kept] == [(date(2026, 11, 3), None)]
    assert kept[0].public_time == et_at(date(2026, 11, 3), 9, 30)
    assert src.unusable_dividends == [("HYG", date(2026, 11, 4), "CURRENCY_CAD")]


# ------------------------------------------------------------------------------ H5


def test_store_without_triggers_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "h.sqlite"
    with sqlite3.connect(p) as c:  # a crash after the meta row, before the triggers
        for stmt in _SCHEMA:
            c.execute(stmt)
        c.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    with pytest.raises(HistError, match="triggers missing"):
        HistStore(p)


def test_file_without_meta_table_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "h.sqlite"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE other (x)")
    with pytest.raises(HistError):
        HistStore(p)


# ---------------------------------------------------------------------- H1-POOL-DRAIN


def test_a_store_error_cancels_queued_chain_requests(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")

    class Slow(Fake):
        def option_eod_chain(self, symbol: str, session: date) -> list[dict[str, Any]]:
            time.sleep(0.01)
            return super().option_eod_chain(symbol, session)

    fake = Slow()
    start, end = date(2026, 9, 1), date(2026, 10, 30)  # 43 sessions
    build(store, fake, ["AAA"], start, end, with_iv=False, log=QUIET)

    def boom(*_: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    store.add_observations = boom  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        build(store, fake, ["AAA"], start, end, workers=1, log=QUIET)
    assert fake.chain_calls <= 3


def test_an_undeclared_dividend_supersedes_its_old_exclusion(tmp_path: Path) -> None:
    """DR-0045 as amended: the ex-date return counts the cash, public from the ex-date
    open; an earlier NO_DECLARATION_DATE row no longer excludes the session."""
    store = HistStore(tmp_path / "h.sqlite")
    fake = EventFake()
    fake.unusable = [("AAA", D2, "NO_DECLARATION_DATE")]  # recorded by the first backfill
    fake.closes[D2] = 99
    build(store, fake, ["AAA"], D1, D5, with_iv=False, log=QUIET)
    _, excl = HistoryView(store, et_at(D5, 18)).total_returns("AAA", D2, D2)
    assert [e.reason for e in excl] == ["DIVIDEND_UNUSABLE:NO_DECLARATION_DATE"]
    t = et_at(D2, 9, 30)
    kept = DividendEvent(
        symbol="AAA", ex_date=D2, amount=Decimal("1"), event_time=t, public_time=t, source="m"
    )
    store.add_events([kept])
    rets, excl = HistoryView(store, et_at(D5, 18)).total_returns("AAA", D2, D2)
    assert excl == [] and rets[0].log_return == pytest.approx(math.log((99 + 1) / 100))
