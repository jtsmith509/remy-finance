"""EVAL-1 §2.2: scores of MKT-1's risk-neutral distributions of S_T / F (Codex R1.6 F09,
DR-0040).

- **Observations:** one per fitted expiration per MKT-1 decision board. Each run log's
  ``ExpirationFit`` records the forward and the quantiles of S_T / F. RAND-1's run logs
  carry the same fits (DR-0031) and are not counted twice.
- **Outcome:** the official close on the expiration date, read through
  ``official_close_as_of`` at the report time (public from 17:15 ET; no look-ahead). The
  realized value is close / forward.
- **Scores:** CRPS by the quantile (pinball) form, as for strategy returns (§2.1), in units
  of S_T / F. The log score is -log of the density at the realized value, from the recorded
  quantile grid: the CDF is linear between quantiles, so the density is Δlevel / Δvalue on
  the segment holding the realized value. Beyond the outermost quantiles it is floored at
  1e-4, the same floor as §2.1.
- **Cohorts:** the EVAL-1 cohort applies §1.4 to the forecast's decision date (61 days
  before the cutoff); the operational view takes every observation whose close is public.
- **Intervals:** the §4 cluster bootstrap, clustered by the ISO week of the expiration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from remy_finance.baselines.common import BaselineRun
from remy_finance.core import OfficialClose
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.data.timeutil import et_date
from remy_finance.data.view import official_close_as_of
from remy_finance.reports.outcomes import EVAL1_CENSOR_DAYS
from remy_finance.reports.scores import crps

DENSITY_FLOOR = 1e-4
ENGINE = "MKT-1"


@dataclass(frozen=True)
class UnderlyingScore:
    group: str  # engine and version, e.g. "MKT-1 v3"
    symbol: str
    expiration: date
    decision_time: datetime
    realized: float  # close / forward
    crps: float
    log_score: float

    def eval1_eligible(self, cutoff: date) -> bool:
        return et_date(self.decision_time) <= cutoff - timedelta(days=EVAL1_CENSOR_DAYS)


@dataclass
class UnderlyingResult:
    scores: list[UnderlyingScore] = field(default_factory=list)
    not_expired: int = 0
    waiting: int = 0  # expired, but no public official close yet
    unavailable: int = 0  # the close source failed


def density_at(levels: Sequence[float], values: Sequence[float], x: float) -> float:
    """Density of the piecewise-linear CDF through (value, level) at ``x``, floored."""
    for i in range(len(values) - 1):
        lo, hi = values[i], values[i + 1]
        if hi > lo and lo <= x < hi:
            return max((levels[i + 1] - levels[i]) / (hi - lo), DENSITY_FLOOR)
    return DENSITY_FLOOR


def score_fit(
    levels: Sequence[float], values: Sequence[float], realized: float
) -> tuple[float, float]:
    """(CRPS, log score) of one S_T / F forecast."""
    return crps(levels, values, realized), -math.log(density_at(levels, values, realized))


class _Closes:
    """Official closes for the report, each fetched once; a source that fails stops being
    asked, and the rest count as unavailable."""

    def __init__(self, source: OfficialCloseSource, now: datetime) -> None:
        self.source, self.now = source, now
        self.cache: dict[tuple[str, date], OfficialClose | None] = {}
        self.broken = False

    def get(self, symbol: str, day: date) -> tuple[OfficialClose | None, bool]:
        """(close or None, available?)"""
        key = (symbol, day)
        if key not in self.cache:
            if self.broken:
                return None, False
            try:
                self.cache[key] = official_close_as_of(self.source, symbol, day, self.now)
            except Exception:  # the report degrades to "unavailable", never fails
                self.broken = True
                return None, False
        return self.cache[key], True


def underlying_scores(
    runs: Sequence[BaselineRun], closes: OfficialCloseSource, now: datetime
) -> UnderlyingResult:
    out = UnderlyingResult()
    source = _Closes(closes, now)
    today = et_date(now)
    for run in sorted(runs, key=lambda r: (r.decision_time, r.snapshot_id)):
        if run.engine != ENGINE or run.decision_time > now:
            continue
        group = f"{run.engine} v{run.engine_version}"
        for fit in run.fits:
            if not fit.ok or not fit.rn_quantiles or fit.forward is None:
                continue
            if fit.expiration > today:
                out.not_expired += 1
                continue
            close, available = source.get(fit.symbol, fit.expiration)
            if not available:
                out.unavailable += 1
                continue
            if close is None:
                out.waiting += 1
                continue
            levels = [float(t) for t, _ in fit.rn_quantiles]
            values = [float(v) for _, v in fit.rn_quantiles]
            realized = float(close.price / fit.forward)
            c, ls = score_fit(levels, values, realized)
            out.scores.append(
                UnderlyingScore(
                    group, fit.symbol, fit.expiration, run.decision_time, realized, c, ls
                )
            )
    return out
