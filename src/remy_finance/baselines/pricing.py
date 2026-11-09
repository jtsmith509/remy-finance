"""Black-76 on the forward and implied volatility (MKT-1 numerical kernel, DR-0030).

Model internals are floats; nothing here touches cash. Results reach records only through
``to_decimal_places`` in ``mkt1``. European exercise is assumed: the American early-exercise
premium is ignored (handoff R1.4 says to flag this, and every MKT-1 run does).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
SQRT2 = math.sqrt(2.0)
_erf = np.frompyfunc(math.erf, 1, 1)


def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def ncdf_array(x: FloatArray) -> FloatArray:
    return 0.5 * (1.0 + np.asarray(_erf(x / SQRT2), dtype=np.float64))


def black76(forward: float, strike: float, vol: float, t: float, df: float, call: bool) -> float:
    """Discounted Black-76 price of a European option on the forward."""
    if vol <= 0 or t <= 0:
        intrinsic = max(forward - strike, 0.0) if call else max(strike - forward, 0.0)
        return df * intrinsic
    sd = vol * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if call:
        return df * (forward * ncdf(d1) - strike * ncdf(d2))
    return df * (strike * ncdf(-d2) - forward * ncdf(-d1))


def black76_calls(
    forward: float, strikes: FloatArray, vols: FloatArray, t: float, df: float
) -> FloatArray:
    """Vectorised discounted call prices for per-strike vols."""
    sd = vols * math.sqrt(t)
    d1 = (np.log(forward / strikes) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return df * (forward * ncdf_array(d1) - strikes * ncdf_array(d2))


def implied_vol(
    price: float,
    forward: float,
    strike: float,
    t: float,
    df: float,
    call: bool,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-10,
) -> float | None:
    """Bisection (monotone in vol, so robust and deterministic). None if the price is
    outside the no-arbitrage range the model can produce."""
    if t <= 0 or price <= 0:
        return None
    p_lo = black76(forward, strike, lo, t, df, call)
    p_hi = black76(forward, strike, hi, t, df, call)
    if not p_lo < price < p_hi:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if black76(forward, strike, mid, t, df, call) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)
