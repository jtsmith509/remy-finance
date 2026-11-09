"""Official closes (adapter + look-ahead guard), the doctor, and pipeline edge rules (R1.6)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from remy_finance.core import LookaheadError, Mode, OfficialClose, Session
from remy_finance.data import FixtureSource
from remy_finance.data.thetadata import ThetaDataSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.data.view import official_close_as_of
from remy_finance.registry import EventKind, Registry, registry_path
from remy_finance.scheduler.doctor import Status, latest_due_slot, run_checks
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from tests.unit.baselines import synthetic as S

FRI = date(2026, 11, 27)  # day after Thanksgiving: a half day
NOW = datetime(2026, 11, 30, 15, 0, tzinfo=UTC)


def test_theta_official_close_reads_stock_eod(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        assert request.url.path == "/v3/stock/history/eod"
        body = {"response": [{"close": 101.37, "bid": 101.9, "ask": 102.0}]}
        return httpx.Response(200, text=json.dumps(body))

    src = ThetaDataSource("http://t", transport=httpx.MockTransport(handler), clock=lambda: NOW)
    c = src.official_close("XYZ", FRI)
    assert c is not None and c.price == Decimal("101.37")  # close, never the EOD bid/ask
    assert seen["start_date"] == seen["end_date"] == "20261127"
    assert c.event_time == session_close(FRI) == et_at(FRI, 13, 0)  # half day
    assert c.public_time == et_at(FRI, 17, 15)


def test_official_close_is_never_read_early() -> None:
    t = session_close(S.EXPIRY)
    close = OfficialClose(
        symbol="XYZ",
        session_date=S.EXPIRY,
        price=Decimal("100"),
        event_time=t,
        public_time=et_at(S.EXPIRY, 17, 15),
        source="f",
    )
    src = FixtureSource([close])
    assert official_close_as_of(src, "XYZ", S.EXPIRY, et_at(S.EXPIRY, 17, 0)) is None
    assert official_close_as_of(src, "XYZ", S.EXPIRY, et_at(S.EXPIRY, 17, 15)) == close
    with pytest.raises(LookaheadError):
        official_close_as_of(src, "XYZ", S.EXPIRY, et_at(S.EXPIRY - timedelta(days=1), 18, 0))


def test_recommendation_recorded_after_the_execution_quote_is_unfilled(tmp_path: Path) -> None:
    decision = S.chain(half_spread="0.02", tick="0.01")
    execution = decision.model_copy(update={"as_of": decision.as_of + timedelta(minutes=5)})
    late = decision.as_of + timedelta(minutes=7)  # receipt after the execution quote
    reg = Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY)
    p = Pipeline(reg, RunLogStore(tmp_path / "runs"), "abcdef0123", lambda: late)
    picks = p.recommend(decision)
    assert picks
    p.fill_entries(decision, execution)
    for tid in picks:
        kinds = [e.kind for _, e in reg.events(tid)]
        assert kinds == [
            EventKind.ORDER_SUBMITTED,
            EventKind.ORDER_UNFILLED,
            EventKind.TRADE_CLOSED,
        ]
    assert p.recommend(decision) == picks  # the run logs make it a no-op
    assert len(reg.recommendations()) == len(picks)


def test_latest_due_slot() -> None:
    mon = date(2026, 11, 9)
    # Monday 10:05: the open window is still running, so Friday's close is the latest due
    assert latest_due_slot(et_at(mon, 10, 5)) == (et_at(date(2026, 11, 6), 15, 45), Session.CLOSE)
    assert latest_due_slot(et_at(mon, 10, 11)) == (et_at(mon, 10, 0), Session.OPEN)


def test_doctor_reports_without_printing_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MASSIVE_API_KEY", "sk-test-not-a-real-key")
    Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE).close()
    checks = run_checks(
        tmp_path,
        et_at(date(2026, 11, 9), 12, 0),
        terminal_probe=lambda: None,
        tree_clean=lambda: True,
    )
    by = {c.name: c for c in checks}
    assert by["Theta Terminal"].status is Status.OK
    assert by["Massive key"].status is Status.OK
    assert by["Massive key"].detail == "found in environment"
    assert all("sk-test" not in c.detail for c in checks)
    assert by["Universe"].status is Status.FAIL
    assert by["Registry (prospective)"].status is Status.OK
    assert by["Registry (replay)"].status is Status.WARN
    assert by["Last snapshot"].status is Status.WARN
    assert by["Code tree"].status is Status.OK
