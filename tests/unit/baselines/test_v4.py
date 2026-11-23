"""MKT-1 v4 and RAND-1 v4 (DR-0048): DR-0041 eligibility and DR-0042 ADJ-1 ranking, with v3
left exactly as it was."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pytest

from remy_finance.baselines.mkt1 import (
    MKT_1,
    MKT_1_V4,
    can_only_lose,
    return_bounds,
    run_mkt1,
    score_board,
)
from remy_finance.baselines.pricing import black76
from remy_finance.baselines.rand1 import RAND_1, RAND_1_V4, run_rand1
from remy_finance.core import MarketState, OptionContract, OptionQuote, Right
from remy_finance.data.timeutil import NEW_YORK
from remy_finance.movegen.generator import generate
from remy_finance.registry import ADJ_0, ADJ_1
from tests.unit.baselines import synthetic as S
from tests.unit.review.test_codex_r1_verify_execution import expensive_spread

COMMIT = "0123456789abcdef0123456789abcdef01234567"
V3_MKT_HASH = "af271cc757a6e34de1e546f21871f4e0721fe3a59eb633b7a5a4a8bb8226be07"
V3_RAND_HASH = "08923e90027061218ec3f974a4deadce9ac9d5ddea7f1cfb834c202a7eb827a0"
LONG_EXPIRY = date(2026, 12, 31)  # 55 days (movegen allows 7-60): more carry than 21 days


def test_v3_configs_are_unchanged() -> None:
    """The live R1 baselines keep their artifact hashes (R2 brief §0.5: R1's record is
    never disturbed). These are the hashes v3 has recorded since DR-0038."""
    assert MKT_1.engine_version == "3" and RAND_1.engine_version == "3"
    assert MKT_1.content_hash() == V3_MKT_HASH
    assert RAND_1.content_hash() == V3_RAND_HASH
    assert MKT_1_V4.engine_version == RAND_1_V4.engine_version == "4"
    assert RAND_1_V4.forecast_model == MKT_1_V4


def two_expiry_board(half_spread: str = "0.01") -> MarketState:
    """S.chain plus a 55-day expiration priced from the same lognormal."""
    base = S.chain(half_spread=half_spread, tick="0.01")
    close = datetime.combine(LONG_EXPIRY, time(16), tzinfo=NEW_YORK)
    years = (close - S.AS_OF).total_seconds() / (365 * 86400)
    df = math.exp(-S.RATE * years)
    fwd = S.SPOT / df
    hs, tk = Decimal(half_spread), Decimal("0.01")
    extra = []
    for q in base.option_quotes:
        k = float(q.contract.strike)
        px = Decimal(repr(black76(fwd, k, S.VOL, years, df, q.contract.right is Right.CALL)))
        extra.append(
            OptionQuote(
                contract=OptionContract.build(
                    "XYZ", LONG_EXPIRY, q.contract.strike, q.contract.right
                ),
                bid=max(((px - hs) / tk).to_integral_value(ROUND_FLOOR) * tk, Decimal(0)),
                ask=max(((px + hs) / tk).to_integral_value(ROUND_CEILING) * tk, tk),
                bid_size=100,
                ask_size=100,
                quote_time=q.quote_time,
                source="synthetic",
            )
        )
    return base.model_copy(update={"option_quotes": (*base.option_quotes, *extra)})


def test_dr0041_a_candidate_that_can_only_lose_is_never_eligible_in_v4() -> None:
    """DR-0041's required regression: a debit above the spread's width."""
    _, cand, _ = expensive_spread()
    assert can_only_lose(cand) and return_bounds(cand)[1] < 0
    b = S.chain(half_spread="0.02", tick="0.01")
    candidates, _ = generate(b)
    v3, v4 = score_board(b, candidates, MKT_1), score_board(b, candidates, MKT_1_V4)
    losers = [c for c in v3.usable if can_only_lose(c)]
    assert losers and v4.cannot_win == len(losers)
    assert not any(can_only_lose(c) for c in v4.usable)
    assert all(not can_only_lose(s.candidate) for s in v4.scored)
    assert f"CANNOT_WIN_EXCLUDED:{len(losers)}" in v4.flags
    assert not any(f.startswith("CANNOT_WIN") for f in v3.flags)
    assert len(v4.usable) == len(v3.usable) - len(losers)


def test_adj1_ranks_on_the_excess_return_and_records_it() -> None:
    b = two_expiry_board()
    candidates, _ = generate(b)
    v3, v4 = score_board(b, candidates, MKT_1), score_board(b, candidates, MKT_1_V4)
    for s in v4.scored:
        df = v4.model_for(s.candidate).df
        assert s.key == pytest.approx(s.mean - (1 / df - 1), abs=1e-12)
    assert all(s.key == s.mean for s in v3.scored)
    dfs = {v4.model_for(s.candidate).df for s in v4.scored}
    assert len(dfs) == 2  # two expirations, two discount factors
    t = S.AS_OF
    recs3, _ = run_mkt1(b, candidates, code_commit=COMMIT, receipt_time=t, cfg=MKT_1)
    recs4, log4 = run_mkt1(b, candidates, code_commit=COMMIT, receipt_time=t, cfg=MKT_1_V4)
    assert recs3 and recs4
    assert {r.ranking_adjustment for r in recs3} == {ADJ_0}
    assert {r.ranking_adjustment for r in recs4} == {ADJ_1}
    assert all(("eligibility", "DR-0041") in r.search_config for r in recs4)
    assert all(r.engine_version == "4" for r in recs4) and log4.engine_version == "4"
    # The recorded forecast is the same net return as v3's; only the key changes.
    by3 = {r.candidate.content_hash(): r for r in recs3}
    for r in recs4:
        if r.candidate.content_hash() in by3:
            assert r.expected_return_pct == by3[r.candidate.content_hash()].expected_return_pct
    # Carry no longer wins: v3's top pick is the long expiry, v4's ranking removes carry.
    assert recs3[0].expiration == LONG_EXPIRY
    best_key = max(v4.scored, key=lambda s: (s.key, -s.index))
    best_mean = max(v4.scored, key=lambda s: (s.mean, -s.index))
    assert best_key.candidate != best_mean.candidate  # the data separates the two keys
    assert recs4[0].candidate == best_key.candidate  # v4 picks on ADJ-1, not the mean
    assert recs3[0].candidate == max(v3.scored, key=lambda s: (s.mean, -s.index)).candidate


def test_rand1_v4_samples_the_v4_eligible_set_and_shares_the_score() -> None:
    b = S.chain(half_spread="0.02", tick="0.01")
    candidates, _ = generate(b)
    score = score_board(b, candidates, MKT_1_V4)
    recs, log = run_rand1(
        b, candidates, code_commit=COMMIT, receipt_time=S.AS_OF, cfg=RAND_1_V4, score=score
    )
    assert recs and log.engine_version == "4"
    assert all(not can_only_lose(r.candidate) for r in recs)
    assert {r.ranking_adjustment for r in recs} == {ADJ_0}  # random: no ranking key
    assert any(f.startswith("CANNOT_WIN_EXCLUDED:") for f in log.flags)
    assert all(("eligibility", "DR-0041") in r.search_config for r in recs)


def test_v4_bug_signal_is_on_the_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    """TESTS-3: a mean just above +5% is a bug signal even if its ADJ-1 key is below."""
    import remy_finance.baselines.mkt1 as mkt1

    b = two_expiry_board()
    cands, _ = generate(b)
    monkeypatch.setattr(mkt1, "anchored_return", lambda *_: 0.0500001)
    monkeypatch.setattr(mkt1, "anchored_mean_feasible", lambda *_: True)
    v4 = score_board(b, cands, MKT_1_V4)
    assert v4.bug_signals and not v4.scored
