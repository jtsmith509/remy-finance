"""EVAL-1 §4 cluster bootstrap for descriptive intervals (Codex R1.6 F08).

- **Clusters:** the ISO week of the trade's expiration (trades expiring the same week share
  the terminal shock).
- **Resampling:** clusters with replacement, 10,000 resamples, percentile intervals. The
  statistic is the pooled mean over the trades of the resampled clusters.
- **Seed:** fixed, from ``sha256("EVAL-1")`` (the first 16 hex digits as an integer), for
  numpy's PCG64 generator, so a report is reproducible.

Interim reports use it for descriptive intervals only, never for P1/P2 p-values.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

RESAMPLES = 10_000
SEED = int(hashlib.sha256(b"EVAL-1").hexdigest()[:16], 16)
MIN_CLUSTERS = 2


def iso_week(d: date) -> tuple[int, int]:
    year, week, _ = d.isocalendar()
    return year, week


@dataclass(frozen=True)
class Interval:
    mean: float
    clusters: int
    lo: float | None = None  # None: too few clusters to estimate
    hi: float | None = None

    def text(self, fmt: str = ".4f") -> str:
        if self.lo is None or self.hi is None:
            return (
                f"insufficient clusters for an interval ({self.clusters} expiration week"
                f"{'' if self.clusters == 1 else 's'}; need {MIN_CLUSTERS})"
            )
        return f"[{self.lo:{fmt}}, {self.hi:{fmt}}] ({self.clusters} clusters)"


def cluster_bootstrap(
    values: Sequence[float],
    expirations: Sequence[date],
    *,
    level: float = 0.95,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> Interval:
    """Percentile interval for the mean of ``values``, clustered by ISO expiration week."""
    if not values:
        raise ValueError("no values")
    groups: dict[tuple[int, int], list[float]] = defaultdict(list)
    for v, d in zip(values, expirations, strict=True):
        groups[iso_week(d)].append(v)
    keys = sorted(groups)
    sums = np.array([sum(groups[k]) for k in keys])
    counts = np.array([len(groups[k]) for k in keys])
    mean = float(sums.sum() / counts.sum())
    if len(keys) < MIN_CLUSTERS:
        return Interval(mean, len(keys))
    rng = np.random.Generator(np.random.PCG64(seed))
    idx = rng.integers(0, len(keys), size=(resamples, len(keys)))
    stats = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    alpha = (1 - level) / 2
    lo, hi = np.quantile(stats, [alpha, 1 - alpha])
    return Interval(mean, len(keys), float(lo), float(hi))
