"""MKT-1 (handoff R1.4): the numerical kernel, the sanity property, selection and logging."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from remy_finance.baselines.common import BaselineRun
from remy_finance.baselines.mkt1 import (
    MKT_1,
    Mkt1Config,
    expected_return,
    fit_expiration,
    run_mkt1,
)
from remy_finance.baselines.pricing import black76, implied_vol
from remy_finance.baselines.smile import LEE_MAX_WING_SLOPE, fit_svi
from remy_finance.core import Candidate, MarketState, Mode, Right, Side
from remy_finance.movegen.generator import generate
from remy_finance.registry import Recommendation, Registry, registry_path
from tests.unit.baselines import synthetic as S

D = Decimal
RECEIPT = S.AS_OF + timedelta(seconds=30)


def run(board: MarketState, **kw: Any) -> tuple[list[Recommendation], BaselineRun]:
    cands, _ = generate(board)
    return run_mkt1(board, cands, code_commit="abcdef0", receipt_time=RECEIPT, **kw)


def two_underlyings(**kw: Any) -> MarketState:
    a, b = S.chain(sym="XYZ", **kw), S.chain(sym="ABC", **kw)
    return a.model_copy(
        update={
            "underlyings": ("ABC", "XYZ"),
            "underlying_quotes": a.underlying_quotes + b.underlying_quotes,
            "option_quotes": a.option_quotes + b.option_quotes,
        }
    )


def model_structure_value(c: Candidate) -> float:
    """Undiscounted model expectation of the structure's terminal payoff, per unit."""
    return sum(
        int(lg.side)
        * lg.ratio
        * lg.contract.multiplier
        * S.model_price(float(lg.contract.strike), lg.contract.right is Right.CALL)
        / S.DF
        for lg in c.legs
    )


# ------------------------------------------------------------------ kernel


def test_black76_parity_and_implied_vol_round_trip() -> None:
    for k in (80.0, 100.0, 120.0):
        c, p = S.model_price(k, True), S.model_price(k, False)
        assert c - p == pytest.approx(S.DF * (S.FORWARD - k), abs=1e-12)
        iv = implied_vol(c, S.FORWARD, k, S.YEARS, S.DF, call=True)
        assert iv == pytest.approx(S.VOL, abs=1e-8)
    assert implied_vol(1e-9, S.FORWARD, 100.0, S.YEARS, S.DF, call=True) is None
    assert implied_vol(200.0, S.FORWARD, 100.0, S.YEARS, S.DF, call=True) is None


def test_svi_recovers_a_skewed_smile_and_respects_lee() -> None:
    k = np.linspace(-0.3, 0.3, 25)
    a, b, rho, m, sig = 0.002, 0.04, -0.6, 0.02, 0.1
    w = a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sig**2))
    fit = fit_svi(k, w)
    assert fit is not None
    svi, rmse = fit
    assert rmse < 1e-6
    assert svi.b * (1 + abs(svi.rho)) <= LEE_MAX_WING_SLOPE
    assert fit_svi(k[:4], w[:4]) is None  # too few points


# ------------------------------------------------------------------ the sanity property


def test_zero_spread_expected_payoffs_match_model_prices() -> None:
    model, fit = fit_expiration(S.chain(), "XYZ", S.EXPIRY)
    assert model is not None and fit.ok
    assert float(fit.forward) == pytest.approx(S.FORWARD, rel=1e-6)
    assert float(fit.discount_factor) == pytest.approx(S.DF, abs=1e-6)
    assert model.dist.mean() == pytest.approx(S.FORWARD, rel=1e-5)
    assert model.dist.probs.sum() == pytest.approx(1.0, abs=1e-9)
    for k in np.arange(75.0, 125.1, 2.5):
        for call in (True, False):
            got = model.vanilla(Right.CALL if call else Right.PUT, float(k)) * S.DF
            assert got == pytest.approx(S.model_price(float(k), call), abs=2e-4)


def test_zero_spread_predicted_returns_are_only_fees_and_carry() -> None:
    board = S.chain()
    cands, _ = generate(board)
    model, _ = fit_expiration(board, "XYZ", S.EXPIRY)
    assert model is not None
    n = 0
    for c in cands:
        if c.structure is None or c.quantity < 1:
            continue
        s = c.structure
        expected = (model_structure_value(c) - float(s.net_premium + s.entry_fees)) / float(
            s.capital_at_risk
        )
        assert expected_return(model, c) * float(s.capital_at_risk) == pytest.approx(
            expected * float(s.capital_at_risk),
            abs=0.05,  # within 5 cents per unit
        )
        n += 1
    assert n > 400


@pytest.mark.parametrize(("half_spread", "tick"), [("0.02", "0.01"), ("0.05", "0.05")])
def test_with_spreads_predicted_returns_are_negative_by_the_spread_cost(
    half_spread: str, tick: str
) -> None:
    """Handoff R1.4: predicted net returns cluster near -(half-spreads + fees) / CaR."""
    board = S.chain(half_spread=half_spread, tick=tick)
    cands, _ = generate(board)
    model, fit = fit_expiration(board, "XYZ", S.EXPIRY)
    assert model is not None, fit.reason
    gaps = []
    for c in cands:
        s = c.structure
        if s is None or c.quantity < 1:
            continue
        spread_cost = sum(  # what crossing the spread costs versus the true model value
            lg.ratio
            * lg.contract.multiplier
            * abs(
                float(p) - S.model_price(float(lg.contract.strike), lg.contract.right is Right.CALL)
            )
            for lg, p in zip(s.legs, s.entry_prices, strict=True)
        )
        carry = model_structure_value(c) * (1 - S.DF)
        car = float(s.capital_at_risk)
        analytic = (carry - spread_cost - float(s.entry_fees)) / car
        got = expected_return(model, c)
        gaps.append((got - analytic) * car)  # $ per unit
        assert got < float(MKT_1.bug_signal_return)
    gaps_arr = np.abs(np.array(gaps))
    assert np.median(gaps_arr) < 0.5  # typically within 50 cents per unit of the truth
    assert gaps_arr.max() < 3.0


# ------------------------------------------------------------------ selection and logging


def test_run_selects_top_k_with_one_per_underlying() -> None:
    board = two_underlyings(half_spread="0.02", tick="0.01")
    recs, log = run(board)
    assert [r.rank for r in recs] == [1, 2]
    assert {r.candidate.legs[0].contract.underlying for r in recs} == {"ABC", "XYZ"}
    assert recs[0].expected_return_pct >= recs[1].expected_return_pct
    assert "2 of 5" in log.shortfall
    assert log.selected == tuple(r.trade_id for r in recs)
    assert "AMERICAN_EXERCISE_PREMIUM_IGNORED" in log.flags
    recs2, _ = run(board, excluded_underlyings=frozenset({"XYZ"}))
    assert [r.candidate.legs[0].contract.underlying for r in recs2] == ["ABC"]


def test_recommendations_are_complete_and_registrable(tmp_path: Path) -> None:
    board = S.chain(half_spread="0.02", tick="0.01")
    recs, _ = run(board)
    r = recs[0]
    assert r.engine == "MKT-1" and r.mode is Mode.REPLAY
    assert r.artifact_hash == MKT_1.content_hash()
    assert r.probability_of_fill is None and r.probability_of_fill_reason
    assert len(r.predicted_return.quantiles) == 5
    assert {name for name, _ in r.tail_measures} == {"CVaR_5pct", "P_loss", "P_max_loss"}
    assert r.capital_at_risk <= D(2000)
    reg = Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY)
    for rec in recs:
        reg.add_recommendation(rec)
    assert reg.verify().ok


def test_deterministic() -> None:
    board = two_underlyings(half_spread="0.02", tick="0.01")
    a, log_a = run(board)
    b, log_b = run(board)
    assert [r.canonical_json() for r in a] == [r.canonical_json() for r in b]
    assert log_a.content_hash() == log_b.content_hash()


def test_large_positive_prediction_is_a_logged_bug_signal_not_a_pick() -> None:
    board = S.chain(half_spread="0.02", tick="0.01")
    bad = []
    for q in board.option_quotes:
        if q.contract.right is Right.CALL and q.contract.strike == D("115"):
            q = q.model_copy(update={"bid": D("0.01"), "ask": D("0.01")})  # true value ~0.05
        bad.append(q)
    board = board.model_copy(update={"option_quotes": tuple(bad)})
    recs, log = run(board)
    assert log.bug_signals
    for r in recs:
        legs = r.candidate.legs
        assert not any(
            lg.contract.strike == D("115")
            and lg.side is Side.LONG
            and lg.contract.right is Right.CALL
            for lg in legs
        )


def test_unfittable_expiration_is_rejected_and_logged() -> None:
    board = S.chain(strikes=(95.0, 100.0, 105.0, 110.0))
    recs, log = run(board)
    assert recs == []  # a null decision
    assert log.fits and not log.fits[0].ok
    assert log.fits[0].reason == "TOO_FEW_IV_POINTS"
    assert log.selected == ()


def test_vrp_adjustment_must_stay_off() -> None:
    board = S.chain()
    with pytest.raises(NotImplementedError):
        run(board, cfg=Mkt1Config(vrp_adjustment="ON"))


def test_time_value_sign() -> None:
    assert S.DF < 1 and math.isclose(black76(100, 100, 0.2, 0, 0.99, True), 0)
