"""30-day ATM implied volatility from one session's end-of-day chain (DR-0045).

The same building blocks as MKT-1 (DR-0030, DR-0037): put-call parity forward with a
rate-implied discount factor, Black-76 implied vols from out-of-the-money mids, time to
expiry measured from the 16:00 ET close. Per expiration, total variance w = vol^2 * T is
interpolated linearly in log-moneyness to the forward (k = 0); across expirations, w is
interpolated linearly in T to 30 days. No extrapolation: without valid expirations on both
sides of 30 days, the nearest valid expiration 21-45 days out is used alone and flagged
(DR-0045 as amended 2026-11-23); with none, the value is missing, with its reason.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time

import numpy as np

from remy_finance.baselines.mkt1 import MKT_1, YEAR_SECONDS
from remy_finance.baselines.pricing import implied_vol
from remy_finance.data.thetadata import is_standard_root
from remy_finance.data.timeutil import NEW_YORK

TARGET_DAYS = 30
MIN_DTE = 7
MAX_DTE = 120
# DR-0045 as amended (2026-11-23): with valid expirations on one side of 30 days only, the
# nearest one 21-45 days out is used alone and flagged ("nearest-expiry"). Bracketed
# values are unchanged. ``NO_BRACKET_30D`` is the reason the method recorded before this
# fallback existed, so those sessions are derived again; a failure now has its own reason.
NEAREST_MIN_DTE, NEAREST_MAX_DTE = 21, 45
LEGACY_RETRY_REASONS = frozenset({"NO_BRACKET_30D"})
NO_BRACKET_OR_NEAREST = "NO_BRACKET_OR_NEAREST_30D"


@dataclass(frozen=True)
class ChainRow:
    expiration: date
    strike: float
    call: bool
    bid: float
    ask: float

    @property
    def two_sided(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)


@dataclass(frozen=True)
class ExpiryIV:
    expiration: date
    dte: int
    years: float
    forward: float
    atm_iv: float

    @property
    def total_variance(self) -> float:
        return self.atm_iv * self.atm_iv * self.years


@dataclass
class IVResult:
    iv30: float | None
    reason: str = ""
    used: list[ExpiryIV] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)  # expiration -> reason
    nearest: bool = False  # the nearest-expiry fallback, not a bracket

    def details(self) -> tuple[tuple[str, str], ...]:
        out = [(f"exp:{e.expiration}", f"dte={e.dte};F={e.forward:.6g};iv={e.atm_iv:.6g}")
               for e in self.used]  # fmt: skip
        if self.nearest:
            out.append(("method", "nearest-expiry"))
        return tuple(sorted(out))


def rows_from_vendor(raw: Sequence[Mapping[str, object]], underlying: str) -> list[ChainRow]:
    """ThetaData ``option/history/eod`` rows (flattened) to ``ChainRow``. Only
    standard-deliverable roots are kept, as MKT-1 does (DR-0014, DR-0021): an adjusted root
    (``XYZ1``) shares strikes and expirations with ``XYZ`` but not its deliverable.
    Malformed rows are skipped."""
    out: list[ChainRow] = []
    for r in raw:
        if not is_standard_root(underlying, str(r.get("symbol") or underlying)):
            continue
        try:
            right = str(r.get("right", "")).upper()
            out.append(
                ChainRow(
                    expiration=date.fromisoformat(str(r["expiration"])[:10]),
                    strike=float(r["strike"]),  # type: ignore[arg-type]
                    call=right in ("C", "CALL"),
                    bid=float(r.get("bid") or 0),  # type: ignore[arg-type]
                    ask=float(r.get("ask") or 0),  # type: ignore[arg-type]
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def discount_factor(rates: Mapping[float, float], years: float) -> float | None:
    """exp(-r T), with r interpolated linearly in maturity over the given (years -> rate)
    points, flat beyond the ends (as MKT-1's ``discount_factor_from_rates``)."""
    if not rates:
        return None
    ts = sorted(rates)
    rate = float(np.interp(years, ts, [rates[t] for t in ts]))
    return math.exp(-rate * years)


def _expiry_iv(
    rows: list[ChainRow], spot: float, session: date, expiration: date, rates: Mapping[float, float]
) -> tuple[ExpiryIV | None, str]:
    cfg = MKT_1
    close = datetime.combine(session, time(16, 0), tzinfo=NEW_YORK)
    expiry_close = datetime.combine(expiration, time(16, 0), tzinfo=NEW_YORK)
    years = (expiry_close - close).total_seconds() / YEAR_SECONDS
    if years <= 0:
        return None, "EXPIRED"
    calls = {r.strike: r for r in rows if r.call and r.two_sided}
    puts = {r.strike: r for r in rows if not r.call and r.two_sided}
    pairs = sorted(set(calls) & set(puts), key=lambda k: (abs(k - spot), k))[: cfg.parity_pairs]
    if len(pairs) < cfg.min_parity_pairs:
        return None, "TOO_FEW_PARITY_PAIRS"
    df = discount_factor(rates, years)
    if df is None:
        return None, "NO_RATE"
    forward = float(np.median([k + (calls[k].mid - puts[k].mid) / df for k in pairs]))
    if abs(forward / spot - 1) > float(cfg.max_forward_gap):
        return None, "FORWARD_FAR_FROM_SPOT"
    below = sorted(k for k in puts if k <= forward and puts[k].mid >= float(cfg.min_iv_mid))
    above = sorted(k for k in calls if k > forward and calls[k].mid >= float(cfg.min_iv_mid))
    if not below or not above:
        return None, "NO_OTM_BRACKET"
    k_lo, k_hi = below[-1], above[0]
    iv_lo = implied_vol(puts[k_lo].mid, forward, k_lo, years, df, call=False)
    iv_hi = implied_vol(calls[k_hi].mid, forward, k_hi, years, df, call=True)
    if iv_lo is None or iv_hi is None:
        return None, "IV_NOT_SOLVABLE"
    x_lo, x_hi = math.log(k_lo / forward), math.log(k_hi / forward)
    w_lo, w_hi = iv_lo * iv_lo * years, iv_hi * iv_hi * years
    w0 = w_lo if x_hi == x_lo else w_lo + (w_hi - w_lo) * (0 - x_lo) / (x_hi - x_lo)
    if w0 <= 0:
        return None, "NONPOSITIVE_VARIANCE"
    return ExpiryIV(
        expiration, (expiration - session).days, years, forward, math.sqrt(w0 / years)
    ), ""


def atm_iv30(
    rows: Sequence[ChainRow], spot: float, session: date, rates: Mapping[float, float]
) -> IVResult:
    """The 30-day ATM implied vol for ``session`` from its EOD ``rows``. ``spot`` is the
    session's official close; ``rates`` maps maturity in years to a continuously
    compounded rate (the latest public yields)."""
    by_exp: dict[date, list[ChainRow]] = {}
    for r in rows:
        dte = (r.expiration - session).days
        if MIN_DTE <= dte <= MAX_DTE:
            by_exp.setdefault(r.expiration, []).append(r)
    res = IVResult(None)
    valid: list[ExpiryIV] = []
    for exp in sorted(by_exp):
        e, why = _expiry_iv(by_exp[exp], spot, session, exp, rates)
        if e is None:
            res.rejected[exp.isoformat()] = why
        else:
            valid.append(e)
    exact = [e for e in valid if e.dte == TARGET_DAYS]
    lower = [e for e in valid if e.dte < TARGET_DAYS]
    upper = [e for e in valid if e.dte > TARGET_DAYS]
    target = TARGET_DAYS / 365.0
    if exact:
        e = exact[0]
        res.used = [e]
        res.iv30 = math.sqrt(e.total_variance / e.years)
        return res
    if not lower or not upper:
        if not valid:
            res.reason = "NO_VALID_EXPIRATION"
            return res
        near = [e for e in valid if NEAREST_MIN_DTE <= e.dte <= NEAREST_MAX_DTE]
        if not near:
            res.reason = NO_BRACKET_OR_NEAREST
            return res
        e = min(near, key=lambda x: (abs(x.dte - TARGET_DAYS), x.dte))
        res.used, res.nearest, res.iv30 = [e], True, e.atm_iv
        return res
    e1, e2 = lower[-1], upper[0]
    w = e1.total_variance + (e2.total_variance - e1.total_variance) * (
        (target - e1.years) / (e2.years - e1.years)
    )
    if w <= 0:
        res.reason = "NONPOSITIVE_VARIANCE"
        return res
    res.used = [e1, e2]
    res.iv30 = math.sqrt(w / target)
    return res
