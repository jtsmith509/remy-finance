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
    QUANTILE_PROBS,
    RN_QUANTILE_PROBS,
    Mkt1Config,
    expected_return,
    fit_expiration,
    run_mkt1,
    score_board,
)
from remy_finance.baselines.pricing import black76, implied_vol
from remy_finance.baselines.smile import LEE_MAX_WING_SLOPE, fit_svi
from remy_finance.core import Candidate, MarketState, Mode, Right, StructureType
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
    dist = r.predicted_return
    assert [q.probability for q in dist.quantiles] == list(QUANTILE_PROBS)
    assert len(QUANTILE_PROBS) == 15
    assert dist.bins_version == "EVAL-1-BINS" and len(dist.bin_probabilities) == 9
    assert sum(dist.bin_probabilities) == 1
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


def test_v2_mean_is_immune_to_a_mispriced_quote() -> None:
    """v1 valued a 115C quoted at 0.01 (true value ~0.05) from the fitted smile and saw a
    huge return; v2 values every leg at its own market mid, so it predicts -fees/CaR."""
    board = S.chain(half_spread="0.02", tick="0.01")
    bad = []
    for q in board.option_quotes:
        if q.contract.right is Right.CALL and q.contract.strike == D("115"):
            q = q.model_copy(update={"bid": D("0.01"), "ask": D("0.01")})
        bad.append(q)
    board = board.model_copy(update={"option_quotes": tuple(bad)})
    cands, _ = generate(board)
    score = score_board(board, cands)
    assert score.bug_signals == ()
    for s in score.scored:
        if s.candidate.structure_type is StructureType.LONG_CALL and (
            s.candidate.legs[0].contract.strike == D("115")
        ):
            st = s.candidate.structure
            assert st is not None
            fees = float(st.entry_fees)
            carry = 0.01 * 100 * (1 / S.DF - 1)
            assert s.mean == pytest.approx((carry - fees) / float(st.capital_at_risk), abs=1e-9)


def test_bug_signal_path_logs_and_excludes() -> None:
    """With a threshold below every prediction, everything is a bug signal: all logged,
    none selected (in v2 only a crossed or stale quote can trigger it for real)."""
    recs, log = run(
        S.chain(half_spread="0.02", tick="0.01"), cfg=Mkt1Config(bug_signal_return=D(-5))
    )
    assert recs == [] and log.bug_signals
    assert all(mean > D(-5) for _, mean in log.bug_signals)


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


# ------------------------------------------------------------------ EVAL-1 §6 records (DR-0032)


def test_bin_probabilities_agree_with_the_distribution() -> None:
    board = S.chain(half_spread="0.02", tick="0.01")
    cands, _ = generate(board)
    score = score_board(board, cands)
    for s in score.scored[:200]:
        f = score.forecast(s)
        assert sum(f.bin_probabilities) == 1  # exactly
        assert all(p >= 0 for p in f.bin_probabilities)
        tails = dict(f.tails)
        p_le_0 = sum(f.bin_probabilities[:4])  # bins up to (-0.25, 0]
        assert abs(p_le_0 - tails["P_loss"]) < D("0.02")  # P(r <= 0) vs P(r < 0)
        assert f.bin_probabilities[0] >= tails["P_max_loss"] - D("1E-7")


def test_run_log_records_risk_neutral_quantiles_of_s_over_f() -> None:
    _, log = run(S.chain())
    fit = log.fits[0]
    levels = [p for p, _ in fit.rn_quantiles]
    values = [v for _, v in fit.rn_quantiles]
    assert levels == list(RN_QUANTILE_PROBS) and len(levels) == 17
    assert values == sorted(values)
    median = float(dict(fit.rn_quantiles)[D("0.5")])
    lognormal_median = math.exp(-0.5 * S.VOL**2 * S.YEARS)
    assert median == pytest.approx(lognormal_median, abs=0.002)
    lo, hi = float(dict(fit.rn_quantiles)[D("0.05")]), float(dict(fit.rn_quantiles)[D("0.95")])
    sd = S.VOL * math.sqrt(S.YEARS)
    assert lo == pytest.approx(lognormal_median * math.exp(-1.6449 * sd), abs=0.003)
    assert hi == pytest.approx(lognormal_median * math.exp(1.6449 * sd), abs=0.003)


# ------------------------------------------------------------------ MKT-1 v2 (DR-0037)


def test_v2_mean_is_mid_anchored_for_every_candidate() -> None:
    from remy_finance.baselines.mkt1 import board_mids

    board = S.chain(half_spread="0.02", tick="0.01")
    cands, _ = generate(board)
    score = score_board(board, cands)
    mids = board_mids(board)
    for s in score.scored[:500]:
        st = s.candidate.structure
        assert st is not None
        m = score.model_for(s.candidate)
        payoff = sum(
            int(lg.side)
            * lg.ratio
            * lg.contract.multiplier
            * mids[(m.symbol, m.expiration, lg.contract.right, lg.contract.strike)]
            / m.df
            for lg in st.legs
        )
        expected = (payoff - float(st.net_premium + st.entry_fees)) / float(st.capital_at_risk)
        assert s.mean == pytest.approx(expected, abs=1e-12)
        carry = abs(payoff) * (1 - m.df) / float(st.capital_at_risk)
        assert s.mean <= carry + 1e-12  # crossing a spread can't be positive beyond carry


def test_discount_factor_from_rates_and_no_rate_rejection() -> None:
    from remy_finance.baselines.mkt1 import discount_factor_from_rates

    board = S.chain()
    assert discount_factor_from_rates(board, S.YEARS) == pytest.approx(S.DF, rel=1e-12)
    bare = board.model_copy(update={"rates": ()})
    assert discount_factor_from_rates(bare, S.YEARS) is None
    model, fit = fit_expiration(bare, "XYZ", S.EXPIRY)
    assert model is None and fit.reason == "NO_RATE"
    model, fit = fit_expiration(board, "XYZ", S.EXPIRY)
    assert model is not None and float(fit.forward) == pytest.approx(S.FORWARD, rel=1e-6)


def test_v2_distribution_is_shifted_to_the_mean_and_stays_in_bounds() -> None:
    board = S.chain(half_spread="0.02", tick="0.01")
    cands, _ = generate(board)
    score = score_board(board, cands)
    for s in score.scored[:300]:
        f = score.forecast(s)
        values = [v for _, v in f.quantiles]
        assert values == sorted(values)
        assert float(values[0]) >= -1.000001  # never worse than losing all capital at risk
        assert sum(f.bin_probabilities) == 1


# ------------------------------------------------------------------ arbitrage-free fit (DR-0038)


def test_durrleman_condition_detects_butterfly_arbitrage() -> None:
    from remy_finance.baselines.smile import ARB_K, SVI

    calm = SVI(a=0.004, b=0.05, rho=-0.5, m=0.0, sigma=0.1)
    assert calm.butterfly_free() and np.min(calm.durrleman_g(ARB_K)) > 0
    # within Lee's bound and with positive variance, but a kinked, near-vertical left wing
    steep = SVI(a=0.0001, b=1.0, rho=-0.99, m=0.0, sigma=0.005)
    assert steep.b * (1 + abs(steep.rho)) <= 2 and steep.min_total_variance > 0
    assert not steep.butterfly_free()


def test_steep_skew_that_v2_rejected_now_fits_arbitrage_free() -> None:
    """A moderate equity-style put skew: v2's fit matched the quotes but its extrapolated
    wing implied call-spread arbitrage, so the expiration was rejected (as in 250 of 376
    expirations on the 3 August 2026 board). v3 only accepts arbitrage-free smiles."""
    strikes = tuple(60 + 2.5 * i for i in range(33))

    def vol(k: float) -> float:
        return max(0.12, 0.25 - 0.3 * math.log(k / 100.0))

    board = S.chain(half_spread="0.02", tick="0.01", strikes=strikes, vol_of=vol)
    model, fit = fit_expiration(board, "XYZ", S.EXPIRY)
    assert fit.ok, fit.reason
    assert model is not None and model.svi.butterfly_free()
    assert float(fit.rmse_vol) < 0.005  # still a close fit to the quotes  # type: ignore[arg-type]
    assert np.all(model.dist.probs >= 0)
