"""HistoryView: as-of reads, no look-ahead, total returns with as-of actions (DR-0045)."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import CorporateAction, CorporateActionType, DividendEvent, LookaheadError
from remy_finance.data.timeutil import et_at
from remy_finance.history import RATE_SYMBOL, HistoryView, HistSeries, HistStore
from tests.unit.history.helpers import obs

D1, D2, D3, D4 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)


def _store(tmp_path: Path) -> HistStore:
    s = HistStore(tmp_path / "h.sqlite")
    s.add_observations(
        [
            obs(HistSeries.CLOSE, "X", D1, "100"),
            obs(HistSeries.CLOSE, "X", D2, "102"),
            obs(HistSeries.CLOSE, "X", D3, "25.25"),  # after a 4:1 split
            obs(HistSeries.CLOSE, "X", D4, "24.75"),  # ex-dividend 0.50
        ]
    )
    return s


def _split(public_day: date) -> CorporateAction:
    t = et_at(public_day, 9, 30)
    return CorporateAction(
        symbol="X",
        action_type=CorporateActionType.SPLIT,
        effective_date=D3,
        details=(("adjustment_type", "forward_split"), ("split_from", "1"), ("split_to", "4")),
        event_time=t,
        public_time=t,
        source="test",
    )


def _div(public_day: date) -> DividendEvent:
    t = et_at(public_day, 16)
    return DividendEvent(
        symbol="X", ex_date=D4, amount=Decimal("0.5"), event_time=t, public_time=t, source="t"
    )


def test_total_returns_apply_splits_and_dividends_public_by_as_of(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_events([_split(D3), _div(D2)])
    rets, excl = HistoryView(s, et_at(D4, 18)).total_returns("X", D2, D4)
    got = {r.session: r.log_return for r in rets}
    assert got[D2] == pytest.approx(math.log(102 / 100))
    assert got[D3] == pytest.approx(math.log(4 * 25.25 / 102))
    assert got[D4] == pytest.approx(math.log((24.75 + 0.5) / 25.25))
    assert excl == []


def test_an_action_public_later_does_not_change_an_earlier_fit(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add_events([_div(date(2026, 11, 20))])  # declared after the ex-date: not yet public
    rets, _ = HistoryView(s, et_at(D4, 18)).total_returns("X", D4, D4)
    assert rets[0].log_return == pytest.approx(math.log(24.75 / 25.25))


def test_missing_previous_close_and_unsupported_actions_are_excluded(tmp_path: Path) -> None:
    s = HistStore(tmp_path / "h.sqlite")
    s.add_observations([obs(HistSeries.CLOSE, "Y", D1, "10"), obs(HistSeries.CLOSE, "Y", D3, "11")])
    t = et_at(D3, 9, 30)
    s.add_events(
        [
            CorporateAction(
                symbol="Y",
                action_type=CorporateActionType.SYMBOL_CHANGE,
                effective_date=D3,
                event_time=t,
                public_time=t,
                source="t",
            )
        ]
    )
    rets, excl = HistoryView(s, et_at(D4, 18)).total_returns("Y", D1, D4)
    assert rets == [] and {e.reason for e in excl} == {"PREVIOUS_CLOSE_MISSING"}
    s.add_observations([obs(HistSeries.CLOSE, "Y", D2, "10.5")])
    rets, excl = HistoryView(s, et_at(D4, 18)).total_returns("Y", D1, D4)
    assert [r.session for r in rets] == [D2]
    assert [(e.session, e.reason) for e in excl] == [
        (D1, "PREVIOUS_CLOSE_MISSING"),  # the session before D1 is not stored
        (D3, "UNSUPPORTED_ACTION:SYMBOL_CHANGE"),
    ]


def test_no_look_ahead(tmp_path: Path) -> None:
    s = _store(tmp_path)
    v = HistoryView(s, et_at(D2, 17))  # before D2's 17:15 publication
    assert v.value(HistSeries.CLOSE, "X", D1) == Decimal("100")
    assert v.value(HistSeries.CLOSE, "X", D2) is None
    with pytest.raises(LookaheadError):
        v.value(HistSeries.CLOSE, "X", D3)
    with pytest.raises(LookaheadError):
        v.total_returns("X", D1, D4)


def test_rates_are_the_latest_public_yields(tmp_path: Path) -> None:
    s = HistStore(tmp_path / "h.sqlite")
    s.add_observations(
        [
            obs(HistSeries.YIELD_1M, RATE_SYMBOL, D1, "0.040", public=et_at(D2, 9, 30)),
            obs(HistSeries.YIELD_1M, RATE_SYMBOL, D2, "0.041", public=et_at(D3, 9, 30)),
            obs(HistSeries.YIELD_3M, RATE_SYMBOL, D2, "0.042", public=et_at(D3, 9, 30)),
        ]
    )
    assert HistoryView(s, et_at(D2, 17, 15)).rates() == {1 / 12: 0.040}
    assert HistoryView(s, et_at(D3, 17, 15)).rates() == {1 / 12: 0.041, 0.25: 0.042}
