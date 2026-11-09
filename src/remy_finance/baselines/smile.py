"""SVI smile fit and the discrete risk-neutral terminal distribution (MKT-1, DR-0030).

Smile: raw SVI in log-moneyness ``k = ln(K/F)``, total implied variance
``w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2))``, constrained to b >= 0,
|rho| <= 1, positive minimum variance, sigma >= 0.005 and Lee's wing bound
b (1 + |rho|) <= 2 (otherwise noisy far-out-of-the-money quotes produce near-vertical
wings). It is fitted with the quasi-explicit method: for fixed (m, sigma) the other three
parameters solve a linear least-squares problem; (m, sigma) are found by a grid search
refined with Nelder-Mead.
All steps are deterministic.

Distribution (Breeden-Litzenberger, discretised): on a strike grid, the digital
``P(S_T > K) = -dC/dK / DF`` is taken from finite differences of the fitted call prices;
adjacent differences give point masses. The two tails are single masses placed at their
conditional means, matched to the fitted put and call at the grid ends, so payoffs that
are linear beyond the grid get exact expectations.

No-arbitrage checks (reject and flag on failure): the digital stays in [0, 1] and never
increases (no negative density; call prices non-increasing and convex), total variance
stays positive, and the distribution's mean reproduces the forward.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from remy_finance.baselines.pricing import FloatArray, black76_calls

LEE_MAX_WING_SLOPE = 2.0  # Roger Lee: total variance grows at most 2|k| in the wings
MIN_SIGMA = 0.005


@dataclass(frozen=True)
class SVI:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def total_variance(self, k: FloatArray) -> FloatArray:
        x = k - self.m
        return self.a + self.b * (self.rho * x + np.sqrt(x * x + self.sigma**2))

    @property
    def min_total_variance(self) -> float:
        return self.a + self.b * self.sigma * math.sqrt(max(0.0, 1.0 - self.rho**2))


def _inner(k: FloatArray, w: FloatArray, m: float, sigma: float) -> tuple[SVI, float] | None:
    """Best (a, d, c) for fixed (m, sigma), with c >= 0 and |d| <= c (b >= 0, |rho| <= 1)."""
    y = (k - m) / sigma
    r = np.sqrt(y * y + 1.0)

    def solve(cols: list[FloatArray]) -> FloatArray:
        x = np.column_stack(cols)
        coef, *_ = np.linalg.lstsq(x, w, rcond=None)
        return np.asarray(coef, dtype=np.float64)

    a, d, c = solve([np.ones_like(y), y, r])
    if c < 0:
        (a,) = solve([np.ones_like(y)])
        d = c = 0.0
    elif abs(d) > c:
        s = 1.0 if d > 0 else -1.0
        a, c = solve([np.ones_like(y), s * y + r])
        c = max(c, 0.0)
        d = s * c
    if c <= 0:
        svi = SVI(a=float(a), b=0.0, rho=0.0, m=m, sigma=sigma)
    else:
        svi = SVI(a=float(a), b=float(c / sigma), rho=float(d / c), m=m, sigma=sigma)
    if svi.min_total_variance <= 0 or svi.b * (1 + abs(svi.rho)) > LEE_MAX_WING_SLOPE:
        return None
    resid = svi.total_variance(k) - w
    return svi, float(np.dot(resid, resid))


Point = tuple[float, float]


def _nelder_mead(fn: Callable[[Point], float], x0: Point, step: Point, iters: int = 300) -> Point:
    """Plain Nelder-Mead in 2-D (deterministic)."""
    pts = [x0, (x0[0] + step[0], x0[1]), (x0[0], x0[1] + step[1])]
    vals = [fn(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(3), key=lambda i: vals[i])
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[2] - vals[0]) < 1e-14:
            break
        cx = (pts[0][0] + pts[1][0]) / 2
        cy = (pts[0][1] + pts[1][1]) / 2
        xr = (2 * cx - pts[2][0], 2 * cy - pts[2][1])
        fr = fn(xr)
        if fr < vals[0]:
            xe = (3 * cx - 2 * pts[2][0], 3 * cy - 2 * pts[2][1])
            fe = fn(xe)
            pts[2], vals[2] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[1]:
            pts[2], vals[2] = xr, fr
        else:
            xc = ((cx + pts[2][0]) / 2, (cy + pts[2][1]) / 2)
            fc = fn(xc)
            if fc < vals[2]:
                pts[2], vals[2] = xc, fc
            else:
                for i in (1, 2):
                    pts[i] = ((pts[0][0] + pts[i][0]) / 2, (pts[0][1] + pts[i][1]) / 2)
                    vals[i] = fn(pts[i])
    best = min(range(3), key=lambda i: vals[i])
    return pts[best]


def fit_svi(k: FloatArray, w: FloatArray) -> tuple[SVI, float] | None:
    """Fit raw SVI to total variances ``w`` at log-moneyness ``k``. Returns the fit and
    its RMSE in total variance, or None if no admissible fit exists."""
    if len(k) < 5:
        return None
    lo, hi = float(k.min()), float(k.max())
    span = max(hi - lo, 1e-3)

    def objective(p: tuple[float, float]) -> float:
        m, sigma = p
        if sigma < MIN_SIGMA or sigma > 5 or not lo - span <= m <= hi + span:
            return math.inf
        res = _inner(k, w, m, sigma)
        return math.inf if res is None else res[1]

    best: tuple[float, float] | None = None
    best_val = math.inf
    for m_grid in np.linspace(lo, hi, 21):
        for s_grid in np.geomspace(0.005, 1.0, 15):
            point = (float(m_grid), float(s_grid))
            v = objective(point)
            if v < best_val:
                best, best_val = point, v
    if best is None:
        return None
    m_opt, s_opt = _nelder_mead(objective, best, (span / 20, best[1] / 4))
    res = _inner(k, w, m_opt, s_opt)
    if res is None:
        return None
    svi, sse = res
    return svi, math.sqrt(sse / len(k))


@dataclass(frozen=True)
class Distribution:
    """A discrete risk-neutral distribution of S_T: support points and probabilities."""

    points: FloatArray
    probs: FloatArray

    def mean(self) -> float:
        return float(np.dot(self.points, self.probs))

    def expect(self, values: FloatArray) -> float:
        return float(np.dot(values, self.probs))


class ArbitrageError(ValueError):
    """The fitted smile implies arbitrage (negative density etc.)."""


def terminal_distribution(
    svi: SVI,
    forward: float,
    df: float,
    t: float,
    k_lo: float,
    k_hi: float,
    n: int = 2000,
    tol: float = 1e-7,
) -> Distribution:
    """Discrete Breeden-Litzenberger distribution on strikes [k_lo, k_hi]."""
    if not 0 < k_lo < forward < k_hi:
        raise ArbitrageError("the strike grid must bracket the forward")
    strikes = np.linspace(k_lo, k_hi, n + 1)
    w = svi.total_variance(np.log(strikes / forward))
    if np.any(w <= 0):
        raise ArbitrageError("non-positive total variance on the grid")
    calls = black76_calls(forward, strikes, np.sqrt(w / t), t, df)
    h = strikes[1] - strikes[0]
    digital = -(np.diff(calls)) / (df * h)  # P(S_T > K) at the midpoints
    if np.any(digital < -tol) or np.any(digital > 1 + tol):
        raise ArbitrageError("call prices are not decreasing with slope in [-DF, 0]")
    dens = -np.diff(digital)
    if np.any(dens < -tol):
        raise ArbitrageError("negative density (call prices not convex)")
    digital = np.clip(digital, 0.0, 1.0)
    interior = np.clip(-np.diff(digital), 0.0, None)
    m_lo = 1.0 - float(digital[0])
    m_hi = float(digital[-1])
    put_lo = float(calls[0]) - df * (forward - k_lo)
    x_lo = k_lo - put_lo / (df * m_lo) if m_lo > 1e-15 else k_lo
    x_hi = k_hi + float(calls[-1]) / (df * m_hi) if m_hi > 1e-15 else k_hi
    points = np.concatenate(([x_lo], strikes[1:-1], [x_hi]))
    probs = np.concatenate(([m_lo], interior, [m_hi]))
    total = float(probs.sum())
    if abs(total - 1.0) > 1e-6:
        raise ArbitrageError(f"probabilities sum to {total}")
    dist = Distribution(points=points, probs=probs / total)
    if abs(dist.mean() / forward - 1.0) > 1e-3:
        raise ArbitrageError(f"distribution mean {dist.mean():.4f} != forward {forward:.4f}")
    return dist
