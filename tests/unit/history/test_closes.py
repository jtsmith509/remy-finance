"""CLOSE-1 (DR-0046): official closes from HIST-1, store first, gaps filled once public."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import LookaheadError, OfficialClose
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.data.view import official_close_as_of
from remy_finance.history import HistSeries, HistStore
from remy_finance.history.closes import HistCloseSource, StoreFirstCloses
from tests.unit.history.helpers import obs

D = date(2026, 11, 20)


class Vendor:
    def __init__(self, price: str = "101.25") -> None:
        self.price = Decimal(price)
        self.calls = 0

    def official_close(self, symbol: str, session_date: date) -> OfficialClose | None:
        self.calls += 1
        if symbol == "NONE":
            return None
        return OfficialClose(
            symbol=symbol,
            session_date=session_date,
            price=self.price,
            event_time=session_close(session_date),
            public_time=et_at(session_date, 17, 15),
            ingest_time=datetime(2026, 11, 23, tzinfo=UTC),
            source="thetadata:stock/history/eod",
        )


def test_store_only_source(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D, "100.5")])
    src = HistCloseSource(store)
    got = src.official_close("AAA", D)
    assert got is not None and got.price == Decimal("100.5")
    assert got.public_time == et_at(D, 17, 15) and got.source.startswith("CLOSE-1:")
    assert src.official_close("AAA", date(2026, 11, 19)) is None


def test_a_gap_is_filled_from_the_vendor_once_public_and_stored(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    vendor = Vendor()
    early = StoreFirstCloses(store, vendor, clock=lambda: et_at(D, 17, 0))
    assert early.official_close("AAA", D) is not None  # returned, but not stored
    assert early.stats.filled == 0 and HistCloseSource(store).official_close("AAA", D) is None
    assert official_close_as_of(early, "AAA", D, et_at(D, 17, 0)) is None  # not public yet
    late = StoreFirstCloses(store, vendor, clock=lambda: et_at(D, 18))
    got = official_close_as_of(late, "AAA", D, et_at(D, 18))
    assert got is not None and got.price == Decimal("101.25") and late.stats.filled == 1
    again = official_close_as_of(late, "AAA", D, et_at(D, 18))
    assert again == got and late.stats.from_store == 1 and vendor.calls == 3  # 2 early, 1 fill


def test_the_stored_close_wins_over_a_differing_vendor_value(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D, "100")])
    vendor = Vendor("999")
    src = StoreFirstCloses(store, vendor, clock=lambda: et_at(D, 18))
    got = src.official_close("AAA", D)
    assert got is not None and got.price == Decimal("100") and vendor.calls == 0


def test_not_found_and_no_fallback(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    src = StoreFirstCloses(store, Vendor(), clock=lambda: et_at(D, 18))
    assert src.official_close("NONE", D) is None and src.stats.not_found == 1
    alone = StoreFirstCloses(store, None, clock=lambda: et_at(D, 18))
    assert alone.official_close("AAA", D) is None and alone.stats.not_found == 1


def test_point_in_time_rule_still_applies(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D, "100")])
    src = HistCloseSource(store)
    assert official_close_as_of(src, "AAA", D, et_at(D, 17, 14)) is None
    with pytest.raises(LookaheadError):
        official_close_as_of(src, "AAA", D, et_at(date(2026, 11, 19), 18))


class DownVendor:
    calls = 0

    def official_close(self, symbol: str, session_date: date) -> OfficialClose | None:
        self.calls += 1
        raise ConnectionError("Theta Terminal unreachable")


def test_a_vendor_failure_never_stops_store_reads(tmp_path: Path) -> None:
    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D, "100")])
    vendor = DownVendor()
    src = StoreFirstCloses(store, vendor, clock=lambda: et_at(D, 18))
    assert src.official_close("GAP", D) is None
    assert src.official_close("GAP2", D) is None
    got = src.official_close("AAA", D)
    assert got is not None and got.price == Decimal("100")
    assert (src.stats.fetch_failed, src.stats.from_store, vendor.calls) == (2, 1, 1)
    assert "ConnectionError" in src.stats.last_error


def test_f09_scores_stored_closes_when_the_vendor_is_down(tmp_path: Path) -> None:
    from remy_finance.baselines.common import BaselineRun, ExpirationFit
    from remy_finance.core import Mode
    from remy_finance.reports.render import _underlying_section
    from remy_finance.reports.underlying import underlying_scores

    store = HistStore(tmp_path / "h.sqlite")
    store.add_observations([obs(HistSeries.CLOSE, "AAA", D, "100")])
    q = tuple((Decimal(lv), Decimal(v)) for lv, v in (("0.1", "0.9"), ("0.5", "1"), ("0.9", "1.1")))
    fits = tuple(
        ExpirationFit(symbol=sym, expiration=D, ok=True, forward=Decimal("100"), rn_quantiles=q)
        for sym in ("GAP", "AAA")  # the gap is read first
    )
    t = et_at(date(2026, 11, 2), 10)
    runs = [
        BaselineRun(engine="MKT-1", engine_version="3", artifact_hash="x", mode=Mode.PROSPECTIVE,
                    snapshot_id="s" * 64, decision_time=t, recorded_at=t, movegen_list_hash="h",
                    n_candidates=0, n_recommendable=0, n_scored=0, fits=fits, code_commit="c")
    ]  # fmt: skip
    src = StoreFirstCloses(store, DownVendor(), clock=lambda: et_at(D, 18))
    res = underlying_scores(runs, src, et_at(D, 18))
    assert [s.symbol for s in res.scores] == ["AAA"] and res.unavailable == 0
    text = "\n".join(_underlying_section(runs, src, et_at(D, 18)))
    assert "read from the store" in text and "not fetched because ThetaData failed" in text


def test_a_lazily_opened_store_fails_on_first_read_not_on_construction(tmp_path: Path) -> None:
    from remy_finance.history import HistError

    bad = tmp_path / "h.sqlite"
    bad.write_bytes(b"garbage" * 200)
    src = StoreFirstCloses(lambda: HistStore(bad), Vendor())
    with pytest.raises(HistError):
        src.official_close("AAA", D)
