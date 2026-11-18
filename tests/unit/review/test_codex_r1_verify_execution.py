"""Regression tests from Codex's verification of the R1 fixes (docs/review/R1-fixes-findings.md).

Ported from its offline probes at aa53762 (execution). Each failing probe is the regression
test for its V-finding; the passing probes guard the fixes they verified.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from remy_finance.baselines.mkt1 import return_bounds, score_board
from remy_finance.core import Mode
from remy_finance.core.payoff import payoff_extremes
from remy_finance.execution.engine import PaperEngine
from remy_finance.movegen.generator import generate
from remy_finance.registry import EventKind as E
from remy_finance.registry import Registry, registry_path, store
from tests.unit.baselines import synthetic as S
from tests.unit.execution.test_engine import (
    NOW,
    ExitAtOpen,
    board,
    close,
    et,
    execution_of,
    open_trade,
    summary,
)
from tests.unit.registry.helpers import recommendation


class Crash(BaseException):
    pass


@pytest.mark.parametrize(
    ("path", "crash_index"),
    [
        ("entry", 1),
        ("entry", 2),
        ("exit", 1),
        ("exit", 2),
        ("exit", 3),
        ("settlement", 1),
        ("settlement", 2),
        ("unfilled", 1),
        ("unfilled", 2),
    ],
)
def test_atomic_cash_batches_reopen_retry_and_preserve_outcomes(
    tmp_path, monkeypatch, path, crash_index
):
    regpath = registry_path(tmp_path, Mode.PROSPECTIVE)
    engine = PaperEngine(
        Registry.create(regpath, Mode.PROSPECTIVE), exit_policies={"HOLD-1": ExitAtOpen()}
    )
    if path in ("entry", "unfilled"):
        rec = recommendation()
        engine.registry.add_recommendation(rec)
        engine.submit(rec.trade_id, NOW)
        tid = rec.trade_id
    else:
        tid = open_trade(engine)
        rec = engine.position(tid).rec
    execution = board(rec.decision_time + timedelta(minutes=5))
    decision = board(et(date(2026, 11, 9), 10, 0))

    def action():
        if path == "entry":
            return engine.fill_entry(tid, execution, NOW)
        if path == "unfilled":
            return engine.fill_entry(tid, None, NOW)
        if path == "exit":
            return engine.on_snapshot(tid, decision, execution_of(decision), NOW)
        return engine.settle(tid, close("103"), NOW)

    original = store._insert
    count = 0

    def crash_after_actual_insert(conn, table, trade_id, text, kind=None):
        nonlocal count
        row = original(conn, table, trade_id, text, kind=kind)
        if table == "journal":
            count += 1
            if count == crash_index:
                raise Crash()
        return row

    with monkeypatch.context() as patch:
        patch.setattr(store, "_insert", crash_after_actual_insert)
        with pytest.raises(Crash):
            action()
    engine.registry.close()
    engine = PaperEngine(
        Registry.open(regpath, Mode.PROSPECTIVE), exit_policies={"HOLD-1": ExitAtOpen()}
    )
    action()
    events = [e for _, e in engine.registry.events(tid)]
    if path == "entry":
        assert len([e for e in events if e.kind is E.LEG_FILLED]) == 2
        assert engine.position(tid).cash == Decimal("-302.60")
    else:
        assert len([e for e in events if e.kind is E.TRADE_CLOSED]) == 1
        expected = {
            "exit": Decimal("-85.20"),
            "settlement": Decimal("257.40"),
            "unfilled": Decimal(0),
        }[path]
        assert summary(engine, tid).realized_pnl == expected
    n = len(events)
    assert action() == []
    assert len(engine.registry.events(tid)) == n
    assert engine.registry.verify().ok


def expensive_spread():
    b = S.chain(half_spread="0.02", tick="0.01")
    candidates, _ = generate(b)
    score = score_board(b, candidates)
    cand = next(
        c
        for c in candidates
        if c.structure is not None
        and tuple(leg.contract.strike for leg in c.legs) == (Decimal("75"), Decimal("80"))
        and c.legs[0].contract.right.value == "C"
        and payoff_extremes(c.structure.legs, c.structure.net_premium)[1] is not None
        and payoff_extremes(c.structure.legs, c.structure.net_premium)[1] < 0
    )
    st = cand.structure
    _low, high = payoff_extremes(st.legs, st.net_premium)
    assert high < 0  # executable debit is greater than the spread width
    true_hi = float((high - st.entry_fees - st.exit_fees) / st.capital_at_risk)
    return score, cand, true_hi


def test_return_bounds_use_signed_payoff_extremes():
    _, cand, true_hi = expensive_spread()
    assert return_bounds(cand)[1] == pytest.approx(true_hi, abs=1e-10)


def test_infeasible_mean_does_not_generate_unattainable_quantiles():
    # Codex V04: the anchored mean (about -1.017%) lies above the true maximum (about
    # -1.049%), so the candidate is skipped as infeasible instead of getting quantiles
    # beyond its possible payoff. Every scored forecast stays within its true bounds.
    score, cand, _ = expensive_spread()
    assert all(s.candidate is not cand for s in score.scored)
    assert score.infeasible >= 1
    assert any(f.startswith("INFEASIBLE_MEAN_SKIPPED:") for f in score.flags)
    for s in score.scored:
        lo, hi = return_bounds(s.candidate)
        values = [float(value) for _, value in score.forecast(s).quantiles]
        assert min(values) >= lo - 1e-8 and max(values) <= hi + 1e-8
