"""Shared baseline plumbing: selection caps (SELECT-1, DR-0007), Decimal conversion of model
outputs, the per-run decision log record, and Recommendation building (DR-0030)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from remy_finance.core import Candidate, MarketState, Mode, Priceability, Record
from remy_finance.core.exact import exact_decimal
from remy_finance.core.types import Dec, UtcDatetime
from remy_finance.data.timeutil import et_date
from remy_finance.registry import (
    Quantile,
    Recommendation,
    Registry,
    ReturnDistribution,
    derive_trade_id,
)

if TYPE_CHECKING:
    from remy_finance.baselines.mkt1 import Forecast


class SelectionRule(Record):
    version: str = Field(min_length=1)
    k: int = Field(ge=1)
    one_per_underlying_per_day: bool


SELECT_1 = SelectionRule(version="SELECT-1", k=5, one_per_underlying_per_day=True)


def to_decimal_places(x: float, places: int = 8) -> Decimal:
    """A model float as a Decimal with ``places`` decimals (half-even). Non-finite raises."""
    if not math.isfinite(x):
        raise ValueError(f"non-finite model output {x!r}")
    return Decimal(repr(x)).quantize(Decimal(1).scaleb(-places))


def recommendable(c: Candidate) -> bool:
    return (
        not c.is_null_move
        and c.priceability is Priceability.PRICED
        and c.structure is not None
        and c.quantity >= 1
    )


def underlying_of(c: Candidate) -> str:
    return c.legs[0].contract.underlying


def underlyings_picked_today(
    registry: Registry, engine: str, day: date, before: datetime | None = None
) -> frozenset[str]:
    """Underlyings this baseline already recommended on ``day`` (ET), for the per-day cap.

    With ``before``, only recommendations decided strictly earlier count. A re-run of a
    snapshot's baseline then sees the same exclusions and reproduces the same picks."""
    return frozenset(
        underlying_of(r.candidate)
        for r in registry.recommendations()
        if r.engine == engine
        and et_date(r.decision_time) == day
        and (before is None or r.decision_time < before)
    )


def apply_caps(
    ranked: Iterable[Candidate], rule: SelectionRule, excluded_underlyings: frozenset[str]
) -> list[Candidate]:
    """Take candidates in order, at most ``k``, one per underlying (incl. earlier today)."""
    taken: list[Candidate] = []
    seen = set(excluded_underlyings)
    for c in ranked:
        sym = underlying_of(c)
        if rule.one_per_underlying_per_day and sym in seen:
            continue
        taken.append(c)
        seen.add(sym)
        if len(taken) == rule.k:
            break
    return taken


class ExpirationFit(Record):
    """MKT-1 diagnostics for one underlying and expiration (also logged when rejected)."""

    symbol: str
    expiration: date
    ok: bool
    reason: str = ""
    forward: Dec | None = None
    discount_factor: Dec | None = None
    years: Dec | None = None
    parity_pairs: int = 0
    iv_points: int = 0
    rmse_vol: Dec | None = None
    svi: tuple[tuple[str, Dec], ...] = ()
    rn_quantiles: tuple[tuple[Dec, Dec], ...] = ()  # (level, S_T / F); EVAL-1 §2.2

    SCHEMA_VERSION: ClassVar[int] = 2  # DR-0032 added rn_quantiles


class BaselineRun(Record):
    """The per-run decision log (DR-0028 §11): one per baseline and decision snapshot.

    An empty ``selected`` is a null decision; ``shortfall`` says why fewer than K."""

    engine: str
    engine_version: str
    artifact_hash: str
    mode: Mode
    snapshot_id: str
    decision_time: UtcDatetime
    recorded_at: UtcDatetime
    movegen_list_hash: str
    n_candidates: int = Field(ge=0)
    n_recommendable: int = Field(ge=0)
    n_scored: int = Field(ge=0)
    excluded_underlyings: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()  # trade ids, in rank order
    shortfall: str = ""
    fits: tuple[ExpirationFit, ...] = ()
    bug_signals: tuple[tuple[str, Dec], ...] = ()  # (candidate hash, predicted mean)
    random_seed: str | None = None
    flags: tuple[str, ...] = ()


def provenance_pairs(board: MarketState) -> tuple[tuple[str, str], ...]:
    return tuple((p.source, ";".join(f"{k}={v}" for k, v in p.details)) for p in board.provenance)


def build_recommendation(
    *,
    candidate: Candidate,
    board: MarketState,
    engine: str,
    engine_version: str,
    code_commit: str,
    artifact_hash: str,
    receipt_time: datetime,
    rank: int,
    method: str,
    forecast: Forecast,
    search_config: Sequence[tuple[str, str]] = (),
    random_seed: str | None = None,
    fill_reason: str = "baseline: probability of fill not modelled in R1",
) -> Recommendation:
    s = candidate.structure
    assert s is not None
    mean = forecast.mean
    with exact_decimal(s.capital_at_risk, mean):
        car = s.capital_at_risk * candidate.quantity
        dollars = mean * car
    return Recommendation(
        trade_id=derive_trade_id(
            engine, engine_version, board.snapshot_id, candidate.content_hash()
        ),
        mode=board.mode,
        engine=engine,
        engine_version=engine_version,
        code_commit=code_commit,
        artifact_hash=artifact_hash,
        decision_time=board.as_of,
        receipt_time=receipt_time,
        snapshot_id=board.snapshot_id,
        quote_provenance=provenance_pairs(board),
        rank=rank,
        candidate=candidate,
        entry_conditions=(
            f"{candidate.entry_rule}: all-or-none at the execution quote "
            f"(decision + 5 min); full displayed size"
        ),
        cancellation_conditions="unfilled at the execution quote: cancelled (single attempt)",
        expiration=s.expiration,
        horizon=f"{candidate.exit_policy_id}: to expiration {s.expiration.isoformat()}",
        fill_rule_version=candidate.entry_rule,
        sizing_version="SIZE-1",
        capital_at_risk=car,
        predicted_return=ReturnDistribution(
            method=method,
            mean=mean,
            quantiles=tuple(Quantile(probability=p, value=v) for p, v in forecast.quantiles),
            bins_version=forecast.bins_version,
            bin_probabilities=tuple(forecast.bin_probabilities),
        ),
        expected_return_pct=mean,
        expected_return_dollars=dollars,
        tail_measures=tuple(forecast.tails),
        probability_of_fill=None,
        probability_of_fill_reason=fill_reason,
        exit_policy_id=candidate.exit_policy_id,
        random_seed=random_seed,
        search_config=tuple(search_config),
    )
