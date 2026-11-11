"""Codex R1 review, R1.2-R1.5 findings F01-F03 (regression tests; adapted from Codex's
reproductions at b38b9b1). See docs/review/R1.2-R1.5-findings.md.

F01/F02: the crash is injected inside the atomic batch's transaction (at the journal row
insert), the strongest place: the whole batch must roll back and a retry must write it all.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from remy_finance.baselines import mkt1
from remy_finance.baselines.mkt1 import ExpiryModel, return_distribution
from remy_finance.baselines.smile import SVI, Distribution
from remy_finance.core import Mode
from remy_finance.execution.engine import PaperEngine
from remy_finance.registry import EventKind, Registry, registry_path, store
from tests.unit.execution.test_engine import (
    NOW,
    ExitAtOpen,
    board,
    close,
    et,
    execution_of,
    open_trade,
)
from tests.unit.registry.helpers import recommendation

E = EventKind


class SimulatedCrash(RuntimeError):
    pass


def crash_at(monkeypatch: pytest.MonkeyPatch, target: EventKind, nth: int = 1) -> None:
    real = store._insert
    seen = 0

    def insert(conn: Any, table: Any, trade_id: str, text: str, kind: str | None = None) -> Any:
        nonlocal seen
        if kind == target.value:
            seen += 1
            if seen == nth:
                raise SimulatedCrash()
        return real(conn, table, trade_id, text, kind=kind)

    monkeypatch.setattr(store, "_insert", insert)


def make_engine(tmp_path: Path, *, discretionary: bool = False) -> PaperEngine:
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    if discretionary:
        return PaperEngine(reg, exit_policies={"HOLD-1": ExitAtOpen()})
    return PaperEngine(reg)


def test_f01_crash_between_leg_fills_rolls_back_the_whole_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path)
    rec = recommendation()
    engine.registry.add_recommendation(rec)
    engine.submit(rec.trade_id, NOW)
    execution = board(rec.decision_time + timedelta(minutes=5))
    with monkeypatch.context() as patch:
        crash_at(patch, E.LEG_FILLED, nth=2)
        with pytest.raises(SimulatedCrash):
            engine.fill_entry(rec.trade_id, execution, NOW)
    kinds = [e.kind for _, e in engine.registry.events(rec.trade_id)]
    assert kinds == [E.ORDER_SUBMITTED]  # nothing partial survived the crash
    engine.fill_entry(rec.trade_id, execution, NOW)
    fills = [e for _, e in engine.registry.events(rec.trade_id) if e.kind is E.LEG_FILLED]
    assert len(fills) == len(rec.candidate.legs)
    assert engine.position(rec.trade_id).cash == sum((e.cash for e in fills), Decimal(0))
    assert engine.registry.verify().ok


@pytest.mark.parametrize("path", ["exit", "settlement", "unfilled"])
def test_f02_crash_before_close_keeps_result_and_close_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    engine = make_engine(tmp_path, discretionary=True)
    if path == "unfilled":
        rec = recommendation()
        engine.registry.add_recommendation(rec)
        engine.submit(rec.trade_id, NOW)
        tid = rec.trade_id

        def action() -> object:
            return engine.fill_entry(tid, None, NOW)
    else:
        tid = open_trade(engine)
        decision = board(et(date(2026, 11, 9), 10, 0))
        if path == "exit":

            def action() -> object:
                return engine.on_snapshot(tid, decision, execution_of(decision), NOW)
        else:

            def action() -> object:
                return engine.settle(tid, close("103"), NOW)

    terminal = {"exit": E.EXIT_FILLED, "settlement": E.EXPIRATION_SETTLED,
                "unfilled": E.ORDER_UNFILLED}[path]  # fmt: skip
    with monkeypatch.context() as patch:
        crash_at(patch, E.TRADE_CLOSED)
        with pytest.raises(SimulatedCrash):
            action()
    kinds = [e.kind for _, e in engine.registry.events(tid)]
    assert terminal not in kinds  # the result rolled back with its close
    action()
    pos = engine.position(tid)
    assert pos.closed
    kinds = [e.kind for _, e in engine.registry.events(tid)]
    assert kinds[-2:] == [terminal, E.TRADE_CLOSED]
    assert engine.registry.verify().ok


def test_f02_repair_closes_a_resolved_history_written_before_the_fix(tmp_path: Path) -> None:
    """A history that already has ORDER_UNFILLED but no TRADE_CLOSED (only possible with
    the old non-atomic code) is closed by the next engine call."""
    engine = make_engine(tmp_path)
    rec = recommendation()
    engine.registry.add_recommendation(rec)
    engine.submit(rec.trade_id, NOW)
    from remy_finance.execution.fill import fill_order

    f = fill_order(rec.candidate.legs, rec.candidate.quantity, {}, opening=True)
    engine.registry.add_event(
        engine._event(rec, E.ORDER_UNFILLED, rec.receipt_time, NOW, evidence=f, note="gap")
    )
    assert not engine.position(rec.trade_id).closed
    out = engine.fill_entry(rec.trade_id, None, NOW)
    assert [e.kind for e in out] == [E.TRADE_CLOSED] and engine.position(rec.trade_id).closed


def test_f03_shift_and_clip_distribution_has_recorded_mean() -> None:
    c = recommendation(quantity=1).candidate
    model = ExpiryModel(
        "XYZ", c.legs[0].contract.expiration, 102.5, 1.0, 0.1,
        SVI(0.01, 0.0, 0.0, 0.0, 0.1),
        Distribution(np.array([100.0, 105.0]), np.array([0.5, 0.5])),
    )  # fmt: skip
    quantiles, _, _ = return_distribution(model, c, 8, mean=0.0)
    values = dict(quantiles)
    implied = (values[Decimal("0.5")] + values[Decimal("0.6")]) / 2  # two equal atoms
    assert float(implied) == pytest.approx(0.0, abs=1e-7)


def test_f03_realistic_skew_chain_forecast_bins_share_anchored_mean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remy_finance.movegen.generator import generate
    from tests.unit.baselines import synthetic as S

    b = S.chain(half_spread="0.02", tick="0.01",
                vol_of=lambda k: max(0.12, 0.25 - 0.3 * np.log(k / 100)))  # fmt: skip
    candidates, _ = generate(b)
    score = mkt1.score_board(b, candidates)
    original = mkt1.bin_probabilities
    means: list[float] = []

    def inspect(values: Any, probs: Any, bins: Any, places: int) -> Any:
        means.append(float(np.dot(values, probs)))
        return original(values, probs, bins, places)

    monkeypatch.setattr(mkt1, "bin_probabilities", inspect)
    for s in score.scored[:400]:  # every forecast, not just the one Codex picked
        means.clear()
        f = score.forecast(s)
        assert means[0] == pytest.approx(float(f.mean), abs=1e-7)
