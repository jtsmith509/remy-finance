"""EVAL-1 §2.1 forecast scores for one recommendation against its realized return.

These are statistics, not cash, so they are floats. Every definition follows the frozen
protocol (`docs/evaluation/EVAL-1-scoring.md`):

- CRPS: ``2 x mean over levels tau of (1{r < q_tau} - tau)(q_tau - r)``.
- Log score: ``-ln p(bin of r)`` over the recorded EVAL-1 bins, with p floored at 1e-4.
- PIT: the forecast CDF at r, interpolated linearly between recorded quantiles and drawn
  uniformly where the CDF jumps (a point mass, or beyond the outermost quantiles). The
  draw is deterministic: seeded by the trade ID, so a report always shows the same PIT.
- Coverage: exceedance indicators at the 1%, 5%, 95% and 99% quantiles.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from remy_finance.registry import KNOWN_BINS, ReturnDistribution

LOG_SCORE_FLOOR = 1e-4
COVERAGE_LEVELS = (Decimal("0.01"), Decimal("0.05"), Decimal("0.95"), Decimal("0.99"))


def crps(levels: Sequence[float], values: Sequence[float], r: float) -> float:
    if not levels:
        raise ValueError("no quantiles")
    total = sum(
        ((1.0 if r < q else 0.0) - t) * (q - r) for t, q in zip(levels, values, strict=True)
    )
    return 2.0 * total / len(levels)


def log_score(dist: ReturnDistribution, r: Decimal) -> float | None:
    if dist.bins_version is None:
        return None
    bins = KNOWN_BINS[dist.bins_version]
    p = float(dist.bin_probabilities[bins.index(r)])
    return -math.log(max(p, LOG_SCORE_FLOOR))


def _uniform(seed: str) -> float:
    """A deterministic draw in [0, 1) from ``seed``."""
    return int(hashlib.sha256(seed.encode()).hexdigest()[:13], 16) / 16**13


def pit(levels: Sequence[float], values: Sequence[float], r: float, seed: str) -> float:
    below = [(t, q) for t, q in zip(levels, values, strict=True) if q < r]
    equal = [t for t, q in zip(levels, values, strict=True) if q == r]
    above = [(t, q) for t, q in zip(levels, values, strict=True) if q > r]
    lo_t = below[-1][0] if below else 0.0
    hi_t = above[0][0] if above else 1.0
    if equal or not below or not above:
        # a jump in the CDF: a point mass at r, or r beyond the outermost quantiles
        return lo_t + (hi_t - lo_t) * _uniform(seed)
    (t0, q0), (t1, q1) = below[-1], above[0]
    return t0 + (t1 - t0) * (r - q0) / (q1 - q0)


@dataclass(frozen=True)
class Scores:
    crps: float
    log_score: float | None
    pit: float
    exceed: dict[Decimal, bool]  # level -> realized beyond that quantile (below for < 0.5)
    bias: float  # realized - predicted mean


def score(dist: ReturnDistribution, realized: Decimal, trade_id: str) -> Scores:
    levels = [float(q.probability) for q in dist.quantiles]
    values = [float(q.value) for q in dist.quantiles]
    r = float(realized)
    by_level = {q.probability: q.value for q in dist.quantiles}
    exceed = {
        lv: (realized < by_level[lv]) if lv < Decimal("0.5") else (realized > by_level[lv])
        for lv in COVERAGE_LEVELS
        if lv in by_level
    }
    return Scores(
        crps=crps(levels, values, r),
        log_score=log_score(dist, realized),
        pit=pit(levels, values, r, seed=trade_id),
        exceed=exceed,
        bias=r - float(dist.mean),
    )
