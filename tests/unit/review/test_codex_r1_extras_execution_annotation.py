"""Regression tests from Codex's review of the R1 extras (docs/review/R1-extras-findings.md).

Ported from its offline probes at fc8b424 (execution and annotations). The failing probes
are the regression tests for X01-X06; the passing ones guard behaviour Codex verified.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from remy_finance.baselines import mkt1
from remy_finance.baselines.rand1 import run_rand1
from remy_finance.core import MarketState, Mode, Right
from remy_finance.core.payoff import payoff_extremes
from remy_finance.execution.engine import PaperEngine
from remy_finance.movegen.generator import generate
from remy_finance.registry import EventKind, Registry, RegistryError, registry_path
from remy_finance.registry.notes import annotate, defect_ids, defect_note
from tests.unit.baselines import synthetic as S
from tests.unit.execution.test_engine import NOW, close, open_trade
from tests.unit.registry.helpers import recommendation

D = Decimal


def test_v04_skip_count_exact_and_shared_by_both_engines():
    board = S.chain(half_spread="0.02", tick="0.01")
    candidates, _ = generate(board)
    score = mkt1.score_board(board, candidates)
    mids = mkt1.board_mids(board)
    rejected = []
    for cand in score.usable:
        model = score.models[(cand.legs[0].contract.underlying, cand.legs[0].contract.expiration)]
        if model is None:
            continue
        mean = mkt1.anchored_return(model, cand, mids)
        if mean is not None and not mkt1.anchored_mean_feasible(cand, mean):
            rejected.append(cand.content_hash())
    assert rejected and score.infeasible == len(rejected)
    assert all(s.candidate.content_hash() not in rejected for s in score.scored)
    for run in (mkt1.run_mkt1, run_rand1):
        recs, log = run(
            board,
            candidates,
            code_commit="a" * 40,
            receipt_time=board.as_of + timedelta(seconds=30),
            score=score,
        )
        assert log.flags.count(f"INFEASIBLE_MEAN_SKIPPED:{len(rejected)}") == 1
        assert log.n_scored == len(score.scored)
        assert all(r.candidate.content_hash() not in rejected for r in recs)
    # Every retained distribution has the same anchored mean and true payoff bounds.
    for selected in score.scored:
        cand = selected.candidate
        st = cand.structure
        low, high = payoff_extremes(st.legs, st.net_premium)
        lo = float((low - st.entry_fees - st.exit_fees) / st.capital_at_risk)
        hi = (
            float("inf")
            if high is None
            else float((high - st.entry_fees - st.exit_fees) / st.capital_at_risk)
        )
        assert mkt1.return_bounds(cand) == pytest.approx((lo, hi))
        forecast = score.forecast(selected)
        assert all(lo - 1e-8 <= float(value) <= hi + 1e-8 for _, value in forecast.quantiles)


def test_annotation_keeps_cash_position_mode_and_existing_bytes(tmp_path):
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    engine = PaperEngine(reg)
    tid = open_trade(engine)
    engine.settle(tid, close("103"), NOW)
    previous = [r.export_line() for r in reg.rows("journal")]
    before = engine.position(tid)
    note = defect_note("V04", "known forecast issue")
    event = annotate(reg, tid, note, NOW)
    assert event.kind is EventKind.ANNOTATION and event.mode is Mode.PROSPECTIVE
    assert event.cash == 0 and annotate(reg, tid, note, NOW + timedelta(seconds=1)) is None
    assert engine.position(tid) == before
    assert [r.export_line() for r in reg.rows("journal")][:-1] == previous
    assert defect_ids([e for _, e in reg.events(tid)]) == ("V04",)
    assert reg.verify().ok
    replay = Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY)
    with pytest.raises(RegistryError):
        annotate(replay, tid, note, NOW)
    assert replay.rows("journal") == []


def guaranteed_profit_board():
    board = S.chain()
    quotes = []
    for quote in board.option_quotes:
        if quote.contract.right is Right.CALL and quote.contract.strike in (D(75), D(80)):
            price = D(20) if quote.contract.strike == 75 else D(21)
            quote = quote.model_copy(update={"bid": price, "ask": price})
        quotes.append(quote)
    # Public, zero rates make the anchored payoff exactly the quoted mid. Deep ITM
    # quotes do not affect the OTM smile fit or near-ATM parity pairs.
    return MarketState.model_validate(
        {
            **board.model_dump(),
            "option_quotes": tuple(quotes),
            "rates": tuple(rate.model_copy(update={"rate": D(0)}) for rate in board.rates),
        }
    )


def test_signed_lower_bound_is_used_for_generated_guaranteed_profit_candidate():
    board = guaranteed_profit_board()
    candidates, _ = generate(board)
    score = mkt1.score_board(board, candidates)
    # Adapted (X01 fix): the guaranteed-profit candidate's mean (about -1) is now below its
    # true floor, so it is skipped as infeasible; find it among the generated candidates.
    chosen = next(
        c
        for c in candidates
        if c.structure is not None
        and tuple(leg.contract.strike for leg in c.legs) == (D(75), D(80))
        and c.legs[0].contract.right is Right.CALL
        and payoff_extremes(c.structure.legs, c.structure.net_premium)[0] > 0
    )
    st = chosen.structure
    low, _ = payoff_extremes(st.legs, st.net_premium)
    true_lo = float((low - st.entry_fees - st.exit_fees) / st.capital_at_risk)
    assert true_lo == pytest.approx(75.92307692307692)
    assert mkt1.return_bounds(chosen)[0] == pytest.approx(true_lo)
    assert all(s.candidate is not chosen for s in score.scored)


def test_infeasible_below_minimum_mean_is_not_scored():
    board = guaranteed_profit_board()
    candidates, _ = generate(board)
    chosen = next(
        c
        for c in candidates
        if c.structure is not None
        and tuple(leg.contract.strike for leg in c.legs) == (D(75), D(80))
        and c.legs[0].contract.right is Right.CALL
        and c.legs[0].side.value == 1
    )
    score = mkt1.score_board(board, [chosen])
    assert score.scored == (), "infeasible mean -1 currently produces all quantiles -1"


def test_feasible_mean_forecast_changes_under_upper_bound_fix(monkeypatch):
    board = S.chain(half_spread="0.02", tick="0.01")
    candidates, _ = generate(board)
    score = mkt1.score_board(board, candidates)
    chosen = next(
        s
        for s in score.scored
        if tuple(leg.contract.strike for leg in s.candidate.legs) == (D(85), D("87.5"))
        and s.candidate.legs[0].contract.right is Right.CALL
        and s.candidate.legs[0].side.value == 1
    )
    lo, hi = mkt1.return_bounds(chosen.candidate)
    assert lo < chosen.mean < hi  # genuinely feasible; not an excluded candidate
    corrected = score.forecast(chosen)

    def old_bounds(candidate):
        st = candidate.structure
        return -1.0, float(st.max_gain - st.entry_fees - st.exit_fees) / float(st.capital_at_risk)

    with monkeypatch.context() as patch:
        patch.setattr(mkt1, "return_bounds", old_bounds)
        old = score.forecast(chosen)
    assert old.mean == corrected.mean
    assert dict(old.quantiles)[D("0.01")] == D("-0.22236799")
    assert dict(corrected.quantiles)[D("0.01")] == D("-0.09184190")
    assert old.quantiles != corrected.quantiles


def test_concurrent_exact_annotation_race_duplicates_notes_but_not_defect_ids(
    tmp_path, monkeypatch
):
    path = registry_path(tmp_path, Mode.PROSPECTIVE)
    first = Registry.create(path, Mode.PROSPECTIVE)
    rec = recommendation()
    first.add_recommendation(rec)
    second = Registry.open(path, Mode.PROSPECTIVE)
    note = defect_note("V04", "upper quantiles")
    original = Registry.add_event
    interleaved = False

    def interleave(self, event):
        nonlocal interleaved
        if self is first and not interleaved:
            interleaved = True
            # A has completed its duplicate check, then B checks and appends before A.
            annotate(second, rec.trade_id, note, NOW + timedelta(seconds=1))
        return original(self, event)

    monkeypatch.setattr(Registry, "add_event", interleave)
    annotate(first, rec.trade_id, note, NOW)
    events = [e for _, e in first.events(rec.trade_id)]
    assert len(events) == 2
    assert defect_ids(events) == ("V04",)
    assert sum((e.cash for e in events), D(0)) == 0
    assert first.verify().ok


def test_best_return_exactly_zero_after_fees_is_feasible():
    from remy_finance.core import FEES_1, Candidate, Structure, StructureType
    from tests.factories import bull_call_spread

    base = bull_call_spread()
    # $5 width: a $498.70 debit plus $1.30 fees leaves exactly zero best P&L.
    st = Structure.price(base.legs, [D("5.987"), D(1)], StructureType.VERTICAL_DEBIT, FEES_1)
    candidate = Candidate.priced(st, quantity=1, entry_rule="FILL-1", exit_policy_id="HOLD-1")
    assert mkt1.return_bounds(candidate) == (-1.0, 0.0)
    assert mkt1.anchored_mean_feasible(candidate, 0.0)
