"""Power estimate for the prospective paper-trade evaluation (spec §11, EVAL-1).

A deliberately simple, transparent model, so its assumptions can be argued with:

- Each baseline issues up to ``recs_per_day`` recommendations per trading day; a fraction
  ``fill_rate`` fills. Filled trades per month: ``n = recs_per_day x days x fill_rate``.
- Trades held to expiration overlap heavily and share market shocks. A month's cohort is
  treated as equicorrelated with pairwise correlation ``rho`` between trade returns, so its
  effective size is the Kish design effect ``n_eff = n / (1 + (n - 1) rho)``. Successive
  monthly cohorts are treated as independent (the typical holding period is about a month).
- Per-trade net return on capital at risk has standard deviation ``sigma``.
- One-sided tests at level ``alpha`` with power ``power``.

The numbers are planning estimates, not results. They are re-estimated from the replay
month (fill rate, sigma, rho) before the live run (EVAL-1 §6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

Z = NormalDist()


@dataclass(frozen=True)
class Assumptions:
    recs_per_day: float = 10.0  # SELECT-1: K = 5 per snapshot, two snapshots, one per name/day
    trading_days_per_month: float = 21.0
    fill_rate: float = 0.7
    sigma: float = 0.7  # sd of net return on capital at risk, per trade
    rho: float = 0.1  # pairwise correlation of trade returns within a monthly cohort
    alpha: float = 0.05
    power: float = 0.8

    @property
    def filled_per_month(self) -> float:
        return self.recs_per_day * self.trading_days_per_month * self.fill_rate

    @property
    def effective_per_month(self) -> float:
        return design_effect_n(self.filled_per_month, self.rho)

    @property
    def z_sum(self) -> float:
        return Z.inv_cdf(1 - self.alpha) + Z.inv_cdf(self.power)


def design_effect_n(n: float, rho: float) -> float:
    """Effective sample size of ``n`` equicorrelated observations (Kish)."""
    if n <= 0:
        return 0.0
    if not 0 <= rho <= 1:
        raise ValueError("rho must be in [0, 1]")
    return n / (1 + (n - 1) * rho)


def n_eff_for_mean(delta: float, a: Assumptions) -> float:
    """Effective observations to detect a mean return of ``delta`` (one sample)."""
    if delta <= 0:
        raise ValueError("delta must be > 0")
    return (a.z_sum * a.sigma / delta) ** 2


def n_eff_for_difference(delta: float, a: Assumptions) -> float:
    """Effective observations *per arm* to detect a difference ``delta`` between two
    independent arms (e.g. MKT-1 vs RAND-1) with equal sigma."""
    return 2 * n_eff_for_mean(delta, a)


def n_eff_for_coverage(p0: float, p1: float, a: Assumptions) -> float:
    """Effective observations to detect a tail exceedance rate ``p1`` when the forecast
    says ``p0`` (one-sided binomial, normal approximation)."""
    if not 0 < p0 < 1 or not 0 < p1 < 1 or p0 == p1:
        raise ValueError("need distinct p0, p1 in (0, 1)")
    za, zb = Z.inv_cdf(1 - a.alpha), Z.inv_cdf(a.power)
    num = za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))
    return (num / abs(p1 - p0)) ** 2


def months_needed(n_eff: float, a: Assumptions) -> float:
    per_month = a.effective_per_month
    return math.inf if per_month <= 0 else n_eff / per_month


def detectable_mean(months: float, a: Assumptions) -> float:
    """The smallest mean return detectable after ``months`` (one sample)."""
    n = months * a.effective_per_month
    return math.inf if n <= 0 else a.z_sum * a.sigma / math.sqrt(n)
