"""The VRP estimate for MKTV-1 (R2 brief P9, B02; DR-0049).

Per name, a row for each session s with:

- ``ratio_s = RV_s / IV_s**2``: realized over implied 30-day variance, where ``IV_s`` is the
  name's ``ATMIV30`` on s and ``RV_s`` is the realized variance of its daily log total
  returns over the ``WINDOW`` sessions after s, ``252 * mean(r**2)`` (zero-mean; annualized
  variance, IV as a decimal);
- ``x_s = SPYIV30_s``: SPY's ``ATMIV30`` on s (DR-0044).

A row is admitted only if its whole window has ended and is public at the fit cutoff: its
response date (the window's last session) closes at or before the cutoff's 17:15 ET
public time. Everything is read through one ``HistoryView`` as of the cutoff, so nothing
stamped after it can reach a row (B02). A row whose window has an excluded or missing
return is dropped and counted. Rows are kept whose response date lies within
``LOOKBACK_YEARS`` of the cutoff.

The fit is OLS of ratio on x over daily, overlapping rows, with Newey-West standard errors
at lag ``WINDOW`` and an effective sample size of rows / ``WINDOW``. A name's (a, b) is
shrunk toward its sector's pooled fit with weight ``w = n_eff / (n_eff + SHRINK_K)``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from remy_finance.data.timeutil import et_at, et_date, previous_session
from remy_finance.history import HistoryView, HistSeries
from remy_finance.history.build import EOD_PUBLIC, sessions_between

WINDOW = 21  # sessions in the realized-variance window ("30 days")
ANNUAL = 252.0
LOOKBACK_YEARS = 3
SHRINK_K = 12.0  # w = n_eff / (n_eff + SHRINK_K)  (Josh, 2026-11-24)
MIN_EFFECTIVE = 12.0  # a fit needs at least 12 effective windows (about one year of rows)
CLIP = (0.3, 1.5)
SPY = "SPY"


@dataclass(frozen=True)
class Row:
    session: date  # s: the window's start
    response: date  # the window's last session
    ratio: float
    x: float  # SPYIV30 on s


@dataclass
class RowSet:
    symbol: str
    rows: list[Row] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)  # reason -> count

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1


@dataclass(frozen=True)
class OlsFit:
    a: float
    b: float
    se_a: float  # Newey-West, lag WINDOW
    se_b: float
    n_rows: int
    clipped_share: float  # share of rows whose fitted ratio falls outside CLIP
    median_ratio: float = 0.0  # diagnostics of the raw ratios (outliers vs units)
    share_above_1: float = 0.0

    @property
    def n_eff(self) -> float:
        return self.n_rows / WINDOW


def window_start(cutoff: datetime) -> date:
    d = et_date(cutoff)
    try:
        return d.replace(year=d.year - LOOKBACK_YEARS)
    except ValueError:  # 29 February
        return d.replace(year=d.year - LOOKBACK_YEARS, day=28)


def last_public_session(cutoff: datetime) -> date:
    """The latest session whose end-of-day data is public at ``cutoff``."""
    d = et_date(cutoff)
    sessions = sessions_between(previous_session(previous_session(d)), d)
    public = [s for s in sessions if et_at(s, *EOD_PUBLIC) <= cutoff]
    return public[-1]


def build_rows(
    view: HistoryView, symbol: str, spy_iv: Mapping[date, float] | None = None
) -> RowSet:
    """Every admitted row for ``symbol`` as of ``view.as_of`` (the fit cutoff)."""
    out = RowSet(symbol)
    last = last_public_session(view.as_of)
    first_response = window_start(view.as_of)
    sessions = sessions_between(first_response - timedelta(days=60), last)
    if len(sessions) <= WINDOW:
        return out
    iv = {o.session: float(o.value) for o in view.series(HistSeries.ATMIV30, symbol, None, last)}
    if spy_iv is None:
        spy_iv = spy_series(view)
    rets, excluded = view.total_returns(symbol, sessions[0], last)
    ret = {r.session: r.log_return for r in rets}
    bad = {e.session for e in excluded}
    for i, s in enumerate(sessions[: len(sessions) - WINDOW]):
        response = sessions[i + WINDOW]
        if response < first_response:
            continue
        if s not in iv:
            out.drop("NO_IV")
            continue
        if s not in spy_iv:
            out.drop("NO_SPYIV30")
            continue
        window = sessions[i + 1 : i + WINDOW + 1]
        if any(d in bad or d not in ret for d in window):
            out.drop("INCOMPLETE_WINDOW")
            continue
        if iv[s] <= 0:
            out.drop("NONPOSITIVE_IV")
            continue
        rv = ANNUAL * sum(ret[d] ** 2 for d in window) / WINDOW
        out.rows.append(Row(s, response, rv / iv[s] ** 2, spy_iv[s]))
    return out


def spy_series(view: HistoryView) -> dict[date, float]:
    last = last_public_session(view.as_of)
    return {o.session: float(o.value) for o in view.series(HistSeries.ATMIV30, SPY, None, last)}


def fit_ols(rows: Sequence[Row]) -> OlsFit | None:
    """OLS of ratio on SPYIV30; None if there are too few rows or x doesn't vary."""
    if len(rows) / WINDOW < MIN_EFFECTIVE:
        return None
    x = np.array([r.x for r in rows])
    y = np.array([r.ratio for r in rows])
    if float(np.ptp(x)) <= 1e-12 or not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return None
    X = np.column_stack([np.ones_like(x), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    dates = [r.session for r in rows]
    cov = xtx_inv @ newey_west_meat(X, u, WINDOW, dates) @ xtx_inv
    fitted = X @ beta
    clipped = float(np.mean((fitted < CLIP[0]) | (fitted > CLIP[1])))
    return OlsFit(
        float(beta[0]),
        float(beta[1]),
        math.sqrt(max(float(cov[0, 0]), 0.0)),
        math.sqrt(max(float(cov[1, 1]), 0.0)),
        len(rows),
        clipped,
        float(np.median(y)),
        float(np.mean(y > 1.0)),
    )


def newey_west_meat(
    X: np.ndarray,
    u: np.ndarray,
    lag: int,
    dates: Sequence[date],
) -> np.ndarray:
    """Bartlett-weighted autocovariances of the scores X_t u_t, summed per session date
    first, so the lag counts sessions even when a sector pools several names per date."""
    order = sorted(set(dates))
    index = {d: i for i, d in enumerate(order)}
    g = np.zeros((len(order), X.shape[1]))
    np.add.at(g, [index[d] for d in dates], X * u[:, None])
    s = g.T @ g
    for j in range(1, min(lag, len(u) - 1) + 1):
        w = 1.0 - j / (lag + 1.0)
        c = g[j:].T @ g[:-j]
        s += w * (c + c.T)
    return s


def pooled_rows(row_sets: Iterable[RowSet]) -> list[Row]:
    """The sector's rows, pooled over its names, ordered by date for the Newey-West sum."""
    return sorted((r for rs in row_sets for r in rs.rows), key=lambda r: (r.session, r.ratio))


def shrink_weight(n_eff: float) -> float:
    return n_eff / (n_eff + SHRINK_K)


def clip_ratio(value: float) -> float:
    return min(max(value, CLIP[0]), CLIP[1])
