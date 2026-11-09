"""Measured inputs for the power-estimate update (power-estimate.md §5, EVAL-1 §4).

From a registry (normally the replay month), per baseline:

- recommendations per trading day and the fill rate;
- sigma, the standard deviation of realized net return on capital at risk;
- the design effect ``Var_cluster / Var_iid`` of the mean realized return, with clusters
  by ISO week of expiration (EVAL-1 §4) and the usual C / (C - 1) small-sample factor;
- effective observations per month (filled per month / design effect) and the implied
  pairwise correlation rho of the power model (Kish, inverted).

These are planning inputs only. A replay month is never evidence of edge.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from remy_finance.data.timeutil import et_date
from remy_finance.reports.outcomes import Status, TradeOutcome
from remy_finance.reports.power import Assumptions, months_needed, n_eff_for_mean

DAYS_PER_MONTH = 21.0
PRIOR = Assumptions()
DELTAS = (0.05, 0.1, 0.2)


@dataclass(frozen=True)
class Measured:
    group: str
    trading_days: int
    recommended: int
    filled: int
    resolved: int
    closed: int
    sigma: float | None
    design_effect: float | None
    clusters: int

    @property
    def recs_per_day(self) -> float:
        return self.recommended / self.trading_days if self.trading_days else 0.0

    @property
    def fill_rate(self) -> float | None:
        return self.filled / self.resolved if self.resolved else None

    @property
    def filled_per_month(self) -> float:
        return self.filled / self.trading_days * DAYS_PER_MONTH if self.trading_days else 0.0

    @property
    def effective_per_month(self) -> float | None:
        if self.design_effect is None:
            return None
        return self.filled_per_month / max(self.design_effect, 1.0)

    @property
    def implied_rho(self) -> float | None:
        n, eff = self.filled_per_month, self.effective_per_month
        if eff is None or n <= 1:
            return None
        return max(0.0, min(1.0, (n / eff - 1) / (n - 1)))

    def assumptions(self, base: Assumptions = PRIOR) -> Assumptions:
        """The power model with every measured value substituted (others unchanged)."""
        a = replace(base, recs_per_day=self.recs_per_day or base.recs_per_day)
        if self.fill_rate is not None:
            a = replace(a, fill_rate=self.fill_rate)
        if self.sigma is not None:
            a = replace(a, sigma=self.sigma)
        if self.implied_rho is not None:
            a = replace(a, rho=self.implied_rho)
        return a


def design_effect(returns: Sequence[float], clusters: Sequence[object]) -> tuple[float | None, int]:
    """Cluster-robust over iid variance of the mean (CR1). None if it can't be estimated."""
    n = len(returns)
    groups: dict[object, list[float]] = defaultdict(list)
    for r, c in zip(returns, clusters, strict=True):
        groups[c].append(r)
    k = len(groups)
    if n < 3 or k < 2:
        return None, k
    mean = statistics.fmean(returns)
    var_iid = statistics.variance(returns) / n
    if var_iid == 0:
        return None, k
    s = sum(sum(x - mean for x in g) ** 2 for g in groups.values())
    var_cluster = s / n**2 * k / (k - 1)
    return var_cluster / var_iid, k


def measure(group: str, items: Sequence[TradeOutcome], trading_days: int) -> Measured:
    filled = [o for o in items if o.filled]
    resolved = [o for o in items if o.filled or o.status is Status.UNFILLED]
    closed = [o for o in items if o.closed_filled and o.realized_return is not None]
    returns = [float(o.realized_return) for o in closed]  # type: ignore[arg-type]
    weeks = [o.rec.expiration.isocalendar()[:2] for o in closed]
    deff, k = design_effect(returns, weeks)
    return Measured(
        group=group,
        trading_days=trading_days,
        recommended=len(items),
        filled=len(filled),
        resolved=len(resolved),
        closed=len(closed),
        sigma=statistics.stdev(returns) if len(returns) >= 2 else None,
        design_effect=deff,
        clusters=k,
    )


def _months(delta: float, a: Assumptions) -> str:
    m = months_needed(n_eff_for_mean(delta, a), a)
    return "-" if math.isinf(m) else f"{m:.0f}"


def measured_report(items: Sequence[TradeOutcome], trading_days: int, mode_label: str) -> str:
    groups: dict[str, list[TradeOutcome]] = defaultdict(list)
    for o in items:
        groups[o.engine_group].append(o)
    base = PRIOR

    def f(x: float | None, fmt: str = "{:.3f}") -> str:
        return "-" if x is None or (isinstance(x, float) and math.isinf(x)) else fmt.format(x)

    days = sorted({et_date(o.rec.decision_time) for o in items})
    lines = [
        f"# Measured power inputs: {mode_label}",
        "",
        f"- **Trading days with decisions:** {trading_days} "
        f"({days[0] if days else '-'} to {days[-1] if days else '-'})",
        "- **Planning inputs only; not evidence of edge.** Design effect: clusters by ISO "
        "week of expiration (EVAL-1 §4).",
        "",
        "| baseline | recs/day | fill rate | filled/month | closed | sigma | clusters | "
        "design effect | effective/month | implied rho |",
        "|---|---|---|---|---|---|---|---|---|---|",
        f"| prior (power-estimate.md) | {base.recs_per_day:.1f} | {base.fill_rate:.0%} | "
        f"{base.filled_per_month:.0f} | - | {base.sigma} | - | - | "
        f"{base.effective_per_month:.1f} | {base.rho} |",
    ]
    measured = [measure(g, groups[g], trading_days) for g in sorted(groups)]
    for m in measured:
        lines.append(
            f"| {m.group} | {m.recs_per_day:.1f} | {f(m.fill_rate, '{:.0%}')} | "
            f"{m.filled_per_month:.0f} | {m.closed} | {f(m.sigma)} | {m.clusters} | "
            f"{f(m.design_effect, '{:.1f}')} | {f(m.effective_per_month, '{:.1f}')} | "
            f"{f(m.implied_rho)} |"
        )
    lines += [
        "",
        "## Months to detect a mean net return of delta, with the measured inputs",
        "",
        "| baseline | delta 5% | delta 10% | delta 20% |",
        "|---|---|---|---|",
        "| prior | "
        + " | ".join(
            f(months_needed(n_eff_for_mean(d, base), base), "{:.0f}") for d in (0.05, 0.1, 0.2)
        )
        + " |",
    ]
    for m in measured:
        a = m.assumptions(base)
        lines.append(
            f"| {m.group} | "
            + " | ".join(
                f(months_needed(n_eff_for_mean(d, a), a), "{:.0f}") for d in (0.05, 0.1, 0.2)
            )
            + " |"
        )
    lines += [
        "",
        "Where the design effect can't be estimated (fewer than 2 clusters), the table uses "
        "the prior rho; every other measured input is used.",
        "With one month of data the design effect rests on few clusters and is itself very "
        "uncertain; the update records it as measured, next to the prior, without replacing it.",
    ]
    return "\n".join(lines) + "\n"
