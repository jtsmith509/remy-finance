"""RAND-1 (handoff R1.5): seeded, reproducible, uniform, same caps as MKT-1."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from remy_finance.baselines.common import BaselineRun
from remy_finance.baselines.mkt1 import run_mkt1
from remy_finance.baselines.rand1 import RAND_1, run_rand1, sample_key, seed_for
from remy_finance.core import MarketState, Mode
from remy_finance.movegen.generator import generate
from remy_finance.registry import Recommendation, Registry, registry_path
from tests.unit.baselines import synthetic as S

RECEIPT = S.AS_OF + timedelta(seconds=30)


def board(n: int = 7, **kw: Any) -> MarketState:
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"][:n]
    chains = [S.chain(sym=s, half_spread="0.02", tick="0.01", **kw) for s in syms]
    return chains[0].model_copy(
        update={
            "underlyings": tuple(syms),
            "underlying_quotes": tuple(q for c in chains for q in c.underlying_quotes),
            "option_quotes": tuple(q for c in chains for q in c.option_quotes),
        }
    )


def rand(b: MarketState, **kw: Any) -> tuple[list[Recommendation], BaselineRun]:
    cands, _ = generate(b)
    return run_rand1(b, cands, code_commit="abcdef0", receipt_time=RECEIPT, **kw)


def test_same_inputs_same_picks_and_seed_recorded() -> None:
    b = board()
    a, log_a = rand(b)
    c, log_c = rand(b)
    assert [r.canonical_json() for r in a] == [r.canonical_json() for r in c]
    assert log_a.content_hash() == log_c.content_hash()
    seed = hashlib.sha256(f"{b.snapshot_id}\nRAND-1".encode()).hexdigest()
    assert seed == seed_for(b.snapshot_id) == log_a.random_seed
    assert all(r.random_seed == seed for r in a)
    assert all(r.engine == "RAND-1" and r.artifact_hash == RAND_1.content_hash() for r in a)


def test_a_different_snapshot_draws_differently() -> None:
    b1 = board()
    b2 = b1.model_copy(update={"universe_version": "SYN-2"})  # same quotes, new snapshot id
    assert b1.snapshot_id != b2.snapshot_id
    p1 = [r.candidate.content_hash() for r in rand(b1)[0]]
    p2 = [r.candidate.content_hash() for r in rand(b2)[0]]
    assert p1 != p2


def test_caps_k_and_one_per_underlying() -> None:
    recs, log = rand(board())
    assert len(recs) == 5 and log.shortfall == ""
    syms = [r.candidate.legs[0].contract.underlying for r in recs]
    assert len(set(syms)) == 5
    recs2, log2 = rand(board(), excluded_underlyings=frozenset({"AAA", "BBB", "CCC"}))
    syms2 = {r.candidate.legs[0].contract.underlying for r in recs2}
    assert syms2 == {"DDD", "EEE", "FFF", "GGG"} and "4 of 5" in log2.shortfall


def test_forecasts_are_mkt1s_and_trade_ids_are_rand1s() -> None:
    b = board(n=1)
    cands, _ = generate(b)
    r_recs, _ = run_rand1(b, cands, code_commit="abcdef0", receipt_time=RECEIPT)
    m_recs, _ = run_mkt1(b, cands, code_commit="abcdef0", receipt_time=RECEIPT)
    pick = r_recs[0]
    # MKT-1's forecast for that candidate, computed by MKT-1 itself
    from remy_finance.baselines.mkt1 import score_board

    score = score_board(b, cands)
    s = next(x for x in score.scored if x.candidate.content_hash() == pick.candidate.content_hash())
    mean, quantiles, tails = score.forecast(s)
    assert pick.predicted_return.mean == mean
    assert [q.value for q in pick.predicted_return.quantiles] == [v for _, v in quantiles]
    assert list(pick.tail_measures) == tails
    assert pick.trade_id not in {m.trade_id for m in m_recs}


def test_sampler_is_uniform() -> None:
    """With no caps binding, the first draw is uniform over candidates (chi-square)."""
    items = [f"cand-{i}" for i in range(10)]
    firsts = Counter(
        min(items, key=lambda h: sample_key(seed_for(f"snap-{n}"), h)) for n in range(5000)
    )
    expected = 500
    chi2 = sum((firsts[i] - expected) ** 2 / expected for i in items)
    assert chi2 < 27.88  # df = 9, p = 0.001


def test_recommendations_register(tmp_path: Path) -> None:
    recs, _ = rand(board())
    with Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY) as reg:
        for r in recs:
            reg.add_recommendation(r)
        assert reg.verify().ok
