"""Market-implied baseline MKT-1 (handoff R1.4, DR-0030).

For each underlying and expiration on the decision board:

1. Forward F and discount factor DF from put-call parity on near-the-money pairs:
   ``C_mid - P_mid = DF (F - K)``, a straight-line fit over up to 8 strikes nearest spot.
2. Implied vols from out-of-the-money mids (puts below F, calls at or above), Black-76 on
   the forward. The American early-exercise premium is ignored (flagged on every run).
3. A raw-SVI smile in log-moneyness, fitted to total variance.
4. A discrete Breeden-Litzenberger terminal distribution; expirations whose fit fails the
   no-arbitrage checks are rejected and flagged.
5. Each candidate's expected net return on capital at risk:
   ``(E[payoff] - executable entry premium - fees) / capital at risk``, undiscounted, at
   expiration (HOLD-1), using the spread-crossing prices movegen priced it at.
6. Rank by that mean (ADJ-0: no adjustment) and select under SELECT-1 (K = 5, one per
   underlying per day).

Sanity property (the baseline's purpose): predicted returns cluster near
``-(half-spreads + fees) / capital at risk``. A predicted mean above
``bug_signal_return`` is treated as a bug signal: logged in the run record and not
selected.

The volatility-risk-premium adjustment is OFF in R1 (``vrp_adjustment``): a hook for R2.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

import numpy as np
from pydantic import Field

from remy_finance.baselines.common import (
    SELECT_1,
    BaselineRun,
    ExpirationFit,
    SelectionRule,
    apply_caps,
    build_recommendation,
    recommendable,
    to_decimal_places,
    underlying_of,
)
from remy_finance.baselines.pricing import FloatArray, implied_vol
from remy_finance.baselines.smile import (
    SVI,
    ArbitrageError,
    Distribution,
    fit_svi,
    terminal_distribution,
)
from remy_finance.core import Candidate, MarketState, OptionQuote, Record, Right
from remy_finance.core.exact import exact_decimal
from remy_finance.core.types import PosDec
from remy_finance.data.timeutil import NEW_YORK
from remy_finance.movegen.generator import list_hash
from remy_finance.registry import ADJ_0, EVAL_1_BINS, Recommendation, ReturnBins

YEAR_SECONDS = 365.0 * 86400.0
# EVAL-1 §6 / DR-0032: 15 levels for strategy returns; 17 for S_T / F.
QUANTILE_PROBS = tuple(
    Decimal(x)
    for x in (
        "0.01", "0.025", "0.05", "0.1", "0.2", "0.3", "0.4", "0.5",
        "0.6", "0.7", "0.8", "0.9", "0.95", "0.975", "0.99",
    )
)  # fmt: skip
RN_QUANTILE_PROBS = (Decimal("0.001"), *QUANTILE_PROBS, Decimal("0.999"))


class Mkt1Config(Record):
    version: str = "MKT-1"
    engine_version: str = "1"
    parity_pairs: int = Field(default=8, ge=3)
    min_parity_pairs: int = Field(default=3, ge=2)
    df_min: PosDec = Decimal("0.9")
    df_max: PosDec = Decimal("1.0001")
    max_forward_gap: PosDec = Decimal("0.1")  # |F / spot - 1|
    min_iv_points: int = Field(default=5, ge=5)
    min_iv_mid: PosDec = Decimal("0.05")  # mids below this are tick noise: not fitted
    max_rmse_vol: PosDec = Decimal("0.05")  # SVI fit RMSE in implied vol
    grid_points: int = Field(default=2000, ge=200)
    grid_sd: PosDec = Decimal(6)
    bug_signal_return: Decimal = Decimal("0.05")
    return_places: int = Field(default=8, ge=4)
    vrp_adjustment: str = "OFF"  # R2 hook; must stay OFF in R1
    selection: SelectionRule = SELECT_1


MKT_1 = Mkt1Config()


@dataclass(frozen=True)
class ExpiryModel:
    """A fitted, arbitrage-checked expiration: everything needed to score candidates."""

    symbol: str
    expiration: date
    forward: float
    df: float
    years: float
    svi: SVI
    dist: Distribution

    def vanilla(self, right: Right, strike: float) -> float:
        """Undiscounted risk-neutral expected payoff of one option (per share)."""
        x = self.dist.points - strike
        if right is Right.CALL:
            return self.dist.expect(np.maximum(x, 0.0))
        return self.dist.expect(np.maximum(-x, 0.0))

    def payoff_on_support(self, c: Candidate) -> FloatArray:
        out = np.zeros_like(self.dist.points)
        for leg in c.legs:
            k = float(leg.contract.strike)
            x = self.dist.points - k
            v = np.maximum(x, 0.0) if leg.contract.right is Right.CALL else np.maximum(-x, 0.0)
            out += int(leg.side) * leg.ratio * leg.contract.multiplier * v
        return out


def _mid(q: OptionQuote) -> float:
    return float((q.bid + q.ask) / 2)


def _years(as_of: datetime, expiration: date) -> float:
    close = datetime.combine(expiration, time(16, 0), tzinfo=NEW_YORK).astimezone(UTC)
    return (close - as_of).total_seconds() / YEAR_SECONDS


def fit_expiration(
    board: MarketState, symbol: str, expiration: date, cfg: Mkt1Config = MKT_1
) -> tuple[ExpiryModel | None, ExpirationFit]:
    def reject(reason: str, **kw: object) -> tuple[None, ExpirationFit]:
        fit = ExpirationFit.model_validate(
            {"symbol": symbol, "expiration": expiration, "ok": False, "reason": reason, **kw}
        )
        return None, fit

    uq = next((u for u in board.underlying_quotes if u.symbol == symbol), None)
    if uq is None or not uq.is_two_sided:
        return reject("NO_UNDERLYING_QUOTE")
    spot = float((uq.bid + uq.ask) / 2)
    years = _years(board.as_of, expiration)
    if years <= 0:
        return reject("EXPIRED")

    calls: dict[float, OptionQuote] = {}
    puts: dict[float, OptionQuote] = {}
    for q in board.option_quotes:
        c = q.contract
        if (
            c.underlying == symbol
            and c.expiration == expiration
            and c.standard_deliverable
            and q.is_two_sided
        ):
            (calls if c.right is Right.CALL else puts)[float(c.strike)] = q

    both = sorted(set(calls) & set(puts), key=lambda k: (abs(k - spot), k))[: cfg.parity_pairs]
    if len(both) < cfg.min_parity_pairs:
        return reject("TOO_FEW_PARITY_PAIRS", parity_pairs=len(both))
    ks = np.array(both)
    diffs = np.array([_mid(calls[k]) - _mid(puts[k]) for k in both])
    slope, intercept = np.polyfit(ks, diffs, 1)
    df = -float(slope)
    if not float(cfg.df_min) <= df <= float(cfg.df_max):
        return reject(f"DISCOUNT_FACTOR_OUT_OF_RANGE:{df:.6f}", parity_pairs=len(both))
    forward = float(intercept) / df
    if abs(forward / spot - 1) > float(cfg.max_forward_gap):
        return reject(f"FORWARD_FAR_FROM_SPOT:{forward:.4f}", parity_pairs=len(both))

    k_pts: list[float] = []
    w_pts: list[float] = []
    ivs: list[float] = []
    for strike in sorted(set(calls) | set(puts)):
        otm = puts.get(strike) if strike < forward else calls.get(strike)
        if otm is None or _mid(otm) < float(cfg.min_iv_mid):
            continue
        iv = implied_vol(_mid(otm), forward, strike, years, df, call=strike >= forward)
        if iv is None:
            continue
        k_pts.append(math.log(strike / forward))
        w_pts.append(iv * iv * years)
        ivs.append(iv)
    base = {
        "forward": to_decimal_places(forward),
        "discount_factor": to_decimal_places(df),
        "years": to_decimal_places(years),
        "parity_pairs": len(both),
        "iv_points": len(k_pts),
    }
    if len(k_pts) < cfg.min_iv_points:
        return reject("TOO_FEW_IV_POINTS", **base)
    k_arr, w_arr = np.array(k_pts), np.array(w_pts)
    fitted = fit_svi(k_arr, w_arr)
    if fitted is None:
        return reject("SVI_FIT_FAILED", **base)
    svi, _ = fitted
    fit_vols = np.sqrt(np.maximum(svi.total_variance(k_arr), 0.0) / years)
    rmse_vol = float(np.sqrt(np.mean((fit_vols - np.array(ivs)) ** 2)))
    params = tuple(
        (name, to_decimal_places(getattr(svi, name), 10))
        for name in ("a", "b", "rho", "m", "sigma")
    )
    base.update(rmse_vol=to_decimal_places(rmse_vol), svi=params)
    if rmse_vol > float(cfg.max_rmse_vol):
        return reject("SVI_FIT_POOR", **base)

    atm_sd = math.sqrt(max(float(svi.total_variance(np.array([0.0]))[0]), 1e-12))
    sd = float(cfg.grid_sd)
    lo = min(forward * math.exp(-sd * atm_sd), min(calls | puts) * 0.9)
    hi = max(forward * math.exp(sd * atm_sd), max(calls | puts) * 1.1)
    try:
        dist = terminal_distribution(svi, forward, df, years, max(lo, 1e-6), hi, cfg.grid_points)
    except ArbitrageError as e:
        return reject(f"ARBITRAGE:{e}", **base)
    model = ExpiryModel(symbol, expiration, forward, df, years, svi, dist)
    return model, ExpirationFit.model_validate(
        {
            "symbol": symbol,
            "expiration": expiration,
            "ok": True,
            "rn_quantiles": rn_quantiles(model),
            **base,
        }
    )


VanillaCache = dict[tuple[str, date, Right, Decimal], float]


def expected_return(model: ExpiryModel, c: Candidate, cache: VanillaCache | None = None) -> float:
    """MKT-1 predicted mean net return on capital at risk (per unit; quantity cancels)."""
    s = c.structure
    assert s is not None
    cache = {} if cache is None else cache
    payoff = 0.0
    for leg in c.legs:
        key = (model.symbol, model.expiration, leg.contract.right, leg.contract.strike)
        v = cache.get(key)
        if v is None:
            v = cache[key] = model.vanilla(leg.contract.right, float(leg.contract.strike))
        payoff += int(leg.side) * leg.ratio * leg.contract.multiplier * v
    cost = float(s.net_premium + s.entry_fees + s.exit_fees)
    return (payoff - cost) / float(s.capital_at_risk)


@dataclass(frozen=True)
class Forecast:
    """A candidate's predicted net-return distribution, as recorded (Decimal)."""

    mean: Decimal
    quantiles: list[tuple[Decimal, Decimal]]
    tails: list[tuple[str, Decimal]]
    bins_version: str
    bin_probabilities: list[Decimal]


def _quantiles(
    values: FloatArray, probs: FloatArray, levels: Sequence[Decimal], places: int
) -> list[tuple[Decimal, Decimal]]:
    """Quantiles of a discrete distribution: the smallest value with CDF >= level."""
    order = np.argsort(values, kind="stable")
    v, cum = values[order], np.cumsum(probs[order])
    out = []
    for p in levels:
        idx = min(int(np.searchsorted(cum, float(p), side="left")), len(v) - 1)
        out.append((p, to_decimal_places(float(v[idx]), places)))
    return out


def bin_probabilities(
    values: FloatArray, probs: FloatArray, bins: ReturnBins, places: int
) -> list[Decimal]:
    """Bin masses as Decimals that sum to exactly 1: each is rounded to ``places`` and the
    rounding residual goes to the largest bin (DR-0032)."""
    edges = np.array([float(e) for e in bins.edges])
    idx = np.searchsorted(edges, values, side="left")  # right-closed bins
    raw = np.bincount(idx, weights=probs, minlength=bins.n_bins)
    raw = raw / raw.sum()
    dec = [to_decimal_places(float(x), places) for x in raw]
    with exact_decimal(*dec):
        residual = Decimal(1) - sum(dec, Decimal(0))
    biggest = max(range(len(dec)), key=lambda i: (dec[i], -i))
    dec[biggest] += residual
    if dec[biggest] < 0:  # pragma: no cover - residual is at most a few units of 1e-places
        raise ValueError("bin rounding residual exceeds the largest bin")
    return dec


def return_distribution(
    model: ExpiryModel, c: Candidate, places: int, bins: ReturnBins = EVAL_1_BINS
) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[str, Decimal]], list[Decimal]]:
    """Quantiles, tail measures and bin probabilities of the predicted net return."""
    s = c.structure
    assert s is not None
    cost = float(s.net_premium + s.entry_fees + s.exit_fees)
    r = (model.payoff_on_support(c) - cost) / float(s.capital_at_risk)
    probs = model.dist.probs
    quantiles = _quantiles(r, probs, QUANTILE_PROBS, places)
    order = np.argsort(r, kind="stable")
    r_sorted, p_sorted = r[order], probs[order]
    cum = np.cumsum(p_sorted)
    tail = 0.05
    take = np.minimum(p_sorted, np.maximum(tail - (cum - p_sorted), 0.0))
    cvar = float(np.dot(take, r_sorted) / tail)
    p_loss = float(p_sorted[r_sorted < 0].sum())
    tails = [
        ("CVaR_5pct", to_decimal_places(cvar, places)),
        ("P_loss", to_decimal_places(p_loss, places)),
        ("P_max_loss", to_decimal_places(float(p_sorted[r_sorted <= -0.999999].sum()), places)),
    ]
    return quantiles, tails, bin_probabilities(r, probs, bins, places)


def rn_quantiles(model: ExpiryModel, places: int = 8) -> tuple[tuple[Decimal, Decimal], ...]:
    """Risk-neutral quantiles of S_T / F (EVAL-1 §2.2), recorded in the run log."""
    return tuple(
        _quantiles(model.dist.points / model.forward, model.dist.probs, RN_QUANTILE_PROBS, places)
    )


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    mean: float
    index: int


VrpHook = Callable[[ExpiryModel], ExpiryModel]
MKT1_METHOD = "MKT-1: SVI smile, Breeden-Litzenberger, undiscounted at expiration"


@dataclass(frozen=True)
class BoardScore:
    """MKT-1's view of one board: fits, and every scorable candidate's predicted mean."""

    cfg: Mkt1Config
    usable: tuple[Candidate, ...]
    models: dict[tuple[str, date], ExpiryModel | None]
    fits: tuple[ExpirationFit, ...]
    scored: tuple[Scored, ...]  # in candidate order; bug signals excluded
    bug_signals: tuple[tuple[str, Decimal], ...]

    def model_for(self, c: Candidate) -> ExpiryModel:
        model = self.models[(underlying_of(c), c.legs[0].contract.expiration)]
        assert model is not None
        return model

    def forecast(self, s: Scored) -> Forecast:
        quantiles, tails, bins = return_distribution(
            self.model_for(s.candidate), s.candidate, self.cfg.return_places
        )
        return Forecast(
            mean=to_decimal_places(s.mean, self.cfg.return_places),
            quantiles=quantiles,
            tails=tails,
            bins_version=EVAL_1_BINS.version,
            bin_probabilities=bins,
        )


def score_board(
    board: MarketState,
    candidates: Sequence[Candidate],
    cfg: Mkt1Config = MKT_1,
    vrp_hook: VrpHook | None = None,
) -> BoardScore:
    if cfg.vrp_adjustment != "OFF" or vrp_hook is not None:
        raise NotImplementedError("the volatility-risk-premium adjustment is R2 work")
    usable = [c for c in candidates if recommendable(c)]
    models: dict[tuple[str, date], ExpiryModel | None] = {}
    fits: list[ExpirationFit] = []
    for c in usable:
        key = (underlying_of(c), c.legs[0].contract.expiration)
        if key not in models:
            model, fit = fit_expiration(board, key[0], key[1], cfg)
            models[key] = model
            fits.append(fit)
    cache: VanillaCache = {}
    scored: list[Scored] = []
    bug_signals: list[tuple[str, Decimal]] = []
    for i, c in enumerate(usable):
        model = models[(underlying_of(c), c.legs[0].contract.expiration)]
        if model is None:
            continue
        mean = expected_return(model, c, cache)
        if mean > float(cfg.bug_signal_return):
            bug_signals.append((c.content_hash(), to_decimal_places(mean, cfg.return_places)))
            continue
        scored.append(Scored(c, mean, i))
    return BoardScore(cfg, tuple(usable), models, tuple(fits), tuple(scored), tuple(bug_signals))


def shortfall_note(n: int, k: int, score: BoardScore, excluded: frozenset[str]) -> str:
    if n >= k:
        return ""
    return (
        f"{n} of {k}: {len(score.usable)} recommendable, {len(score.scored)} scored, "
        f"{len(excluded)} underlyings already used today"
    )


def run_mkt1(
    board: MarketState,
    candidates: Sequence[Candidate],
    *,
    code_commit: str,
    receipt_time: datetime,
    excluded_underlyings: frozenset[str] = frozenset(),
    cfg: Mkt1Config = MKT_1,
    vrp_hook: VrpHook | None = None,
    score: BoardScore | None = None,
    candidates_hash: str | None = None,
) -> tuple[list[Recommendation], BaselineRun]:
    """Score every recommendable candidate on ``board`` and pick MKT-1's recommendations.

    ``score`` and ``candidates_hash`` let a caller share one scoring pass and one list
    hash between baselines (they must come from this board and these candidates)."""
    if score is None or score.cfg != cfg:
        score = score_board(board, candidates, cfg, vrp_hook)
    ranked = sorted(score.scored, key=lambda s: (-s.mean, s.index))
    by_id = {id(s.candidate): s for s in ranked}
    picks = apply_caps((s.candidate for s in ranked), cfg.selection, excluded_underlyings)
    recs: list[Recommendation] = []
    artifact = cfg.content_hash()
    for rank, c in enumerate(picks, start=1):
        forecast = score.forecast(by_id[id(c)])
        recs.append(
            build_recommendation(
                candidate=c,
                board=board,
                engine=cfg.version,
                engine_version=cfg.engine_version,
                code_commit=code_commit,
                artifact_hash=artifact,
                receipt_time=receipt_time,
                rank=rank,
                method=MKT1_METHOD,
                forecast=forecast,
                search_config=(
                    ("ranking_adjustment", ADJ_0),
                    ("selection", cfg.selection.version),
                    ("vrp_adjustment", cfg.vrp_adjustment),
                ),
            )
        )
    shortfall = shortfall_note(len(recs), cfg.selection.k, score, excluded_underlyings)
    run = BaselineRun(
        engine=cfg.version,
        engine_version=cfg.engine_version,
        artifact_hash=artifact,
        mode=board.mode,
        snapshot_id=board.snapshot_id,
        decision_time=board.as_of,
        recorded_at=receipt_time,
        movegen_list_hash=candidates_hash or list_hash(tuple(candidates)),
        n_candidates=len(candidates),
        n_recommendable=len(score.usable),
        n_scored=len(score.scored),
        excluded_underlyings=tuple(sorted(excluded_underlyings)),
        selected=tuple(r.trade_id for r in recs),
        shortfall=shortfall,
        fits=score.fits,
        bug_signals=score.bug_signals,
        flags=("AMERICAN_EXERCISE_PREMIUM_IGNORED",),
    )
    return recs, run
