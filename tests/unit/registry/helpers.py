"""Builders for registry tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from remy_finance.core import Candidate, Mode, Structure
from remy_finance.registry import (
    EventKind,
    JournalEvent,
    Quantile,
    Recommendation,
    ReturnDistribution,
    derive_trade_id,
)
from tests.factories import AS_OF, EXP, bull_call_spread

D = Decimal
SNAP = "a" * 64


def recommendation(
    mode: Mode = Mode.PROSPECTIVE,
    *,
    engine: str = "MKT-1",
    quantity: int = 2,
    structure: Structure | None = None,
    **kw: Any,
) -> Recommendation:
    cand = Candidate.priced(
        structure or bull_call_spread(),
        quantity=quantity,
        entry_rule="FILL-1",
        exit_policy_id="HOLD-1",
    )
    car = cand.structure.capital_at_risk * quantity  # type: ignore[union-attr]
    mean = D("-0.04")
    snapshot_id = kw.pop("snapshot_id", SNAP)
    data: dict[str, Any] = {
        "trade_id": derive_trade_id(engine, "1", snapshot_id, cand.content_hash()),
        "mode": mode,
        "engine": engine,
        "engine_version": "1",
        "code_commit": "0123456789abcdef",
        "artifact_hash": "b" * 64,
        "decision_time": AS_OF,
        "receipt_time": AS_OF + timedelta(seconds=30),
        "snapshot_id": snapshot_id,
        "quote_provenance": (("options", "thetadata"),),
        "rank": 1,
        "candidate": cand,
        "entry_conditions": "FILL-1 at the +5 min execution quote; all-or-none",
        "cancellation_conditions": "unfilled at the execution quote: cancelled (single attempt)",
        "expiration": EXP,
        "horizon": "HOLD-1: to expiration",
        "fill_rule_version": "FILL-1",
        "sizing_version": "SIZE-1",
        "capital_at_risk": car,
        "predicted_return": ReturnDistribution(
            method="MKT-1 risk-neutral",
            mean=mean,
            quantiles=(
                Quantile(probability=D("0.05"), value=D("-1")),
                Quantile(probability=D("0.5"), value=D("-0.2")),
                Quantile(probability=D("0.95"), value=D("1.1")),
            ),
        ),
        "expected_return_pct": mean,
        "expected_return_dollars": mean * car,
        "tail_measures": (("CVaR5", D("-1")),),
        "probability_of_fill": None,
        "probability_of_fill_reason": "baseline: not modelled in R1",
        "exit_policy_id": "HOLD-1",
    }
    data.update(kw)
    return Recommendation.model_validate(data)


def event(rec: Recommendation, kind: EventKind, minutes: int = 5, **kw: Any) -> JournalEvent:
    t = rec.decision_time + timedelta(minutes=minutes)
    data: dict[str, Any] = {
        "trade_id": rec.trade_id,
        "mode": rec.mode,
        "kind": kind,
        "event_time": t,
        "recorded_at": max(t, datetime(2026, 11, 6, 15, 30, tzinfo=UTC)),
    }
    data.update(kw)
    return JournalEvent.model_validate(data)
