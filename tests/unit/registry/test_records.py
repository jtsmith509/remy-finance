"""Recommendation and JournalEvent validation (R1.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from remy_finance.core import Candidate, Mode
from remy_finance.execution.fill import fill_order
from remy_finance.registry import EventKind, JournalEvent, Recommendation
from tests.factories import AS_OF
from tests.unit.registry.helpers import D, event, recommendation


def test_valid_recommendation_round_trips() -> None:
    rec = recommendation()
    assert Recommendation.from_canonical_json(rec.canonical_json()) == rec
    assert rec.trade_id.startswith("T-")
    assert rec.ranking_adjustment == "ADJ-0"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"capital_at_risk": D("1")}, "capital_at_risk"),
        ({"expected_return_dollars": D("1")}, "expected_return_dollars"),
        ({"expected_return_pct": D("0.1")}, "expected_return_dollars|mean"),
        ({"probability_of_fill_reason": None}, "needs a reason"),
        ({"probability_of_fill": D("1.5")}, r"\[0, 1\]"),
        ({"exit_policy_id": "LSM-1"}, "exit policy"),
        ({"fill_rule_version": "FILL-2"}, "fill rule"),
        ({"trade_id": "T-made-up"}, "trade_id"),
        ({"receipt_time": AS_OF.replace(hour=14)}, "before its decision"),
        ({"snapshot_id": "short"}, "snapshot_id"),
    ],
)
def test_inconsistent_recommendation_rejected(override: dict[str, object], message: str) -> None:
    good = recommendation()
    with pytest.raises(ValidationError, match=message):
        Recommendation.model_validate({**dict(good), **override})


def test_null_move_and_quantity_zero_cannot_be_recommended() -> None:
    good = recommendation()
    null = Candidate.null_move("FILL-1", "HOLD-1")
    with pytest.raises(ValidationError, match="priced, non-null"):
        Recommendation.model_validate({**dict(good), "candidate": null})
    with pytest.raises(ValidationError, match="quantity >= 1"):
        recommendation(quantity=0)


def test_trade_id_is_deterministic_and_input_sensitive() -> None:
    assert recommendation().trade_id == recommendation().trade_id
    assert recommendation(engine="RAND-1").trade_id != recommendation().trade_id
    assert recommendation(snapshot_id="c" * 64).trade_id != recommendation().trade_id


def test_records_are_frozen() -> None:
    rec = recommendation()
    with pytest.raises(ValidationError):
        rec.rank = 2  # type: ignore[misc]


def test_event_rules() -> None:
    rec = recommendation()
    with pytest.raises(ValidationError, match="only they"):
        event(rec, EventKind.ANNOTATION, note="x", corrects_seq=1)
    with pytest.raises(ValidationError, match="only they"):
        event(rec, EventKind.CORRECTION, note="x")
    with pytest.raises(ValidationError, match="needs a note"):
        event(rec, EventKind.ANNOTATION)
    with pytest.raises(ValidationError, match="before it happens"):
        event(rec, EventKind.ORDER_SUBMITTED, minutes=600, recorded_at=AS_OF)
    with pytest.raises(ValidationError, match="go together"):
        event(rec, EventKind.LEG_FILLED, evidence_type="OrderFill")


def test_event_evidence_carries_the_typed_record() -> None:
    rec = recommendation()
    fill = fill_order(rec.candidate.legs, 2, {}, opening=True)  # no quotes: unfilled
    ev = JournalEvent.with_evidence(
        fill,
        trade_id=rec.trade_id,
        mode=Mode.PROSPECTIVE,
        kind=EventKind.ORDER_UNFILLED,
        event_time=AS_OF,
        recorded_at=AS_OF,
    )
    assert ev.evidence_type == "OrderFill"
    assert type(fill).from_canonical_json(ev.evidence or "") == fill
    with pytest.raises(ValidationError, match="named record type"):
        JournalEvent.model_validate({**dict(ev), "evidence_type": "StockFill"})


def test_cash_is_decimal_only() -> None:
    rec = recommendation()
    with pytest.raises(ValidationError):
        event(rec, EventKind.LEG_FILLED, cash=1.5)
    assert event(rec, EventKind.LEG_FILLED, cash=Decimal("-130.65")).cash == D("-130.65")


# ------------------------------------------------------------------ return bins (DR-0032)


def test_return_bins_are_right_closed() -> None:
    from remy_finance.registry import EVAL_1_BINS

    assert EVAL_1_BINS.n_bins == 9
    assert EVAL_1_BINS.index(D("-1")) == 0
    assert EVAL_1_BINS.index(D("-0.99")) == 0
    assert EVAL_1_BINS.index(D("-0.98")) == 1
    assert EVAL_1_BINS.index(D("0")) == 3
    assert EVAL_1_BINS.index(D("0.0001")) == 4
    assert EVAL_1_BINS.index(D("5")) == 8


@pytest.mark.parametrize(
    ("bins_version", "probs", "message"),
    [
        ("EVAL-1-BINS", ["0.5", "0.5"], "9 bins"),
        ("EVAL-1-BINS", ["0.1"] * 9, "not exactly 1"),
        ("EVAL-1-BINS", ["1.1", "-0.1"] + ["0"] * 7, r"\[0, 1\]"),
        ("NOPE", ["1"], "unknown bins_version"),
        (None, ["1"], "need a bins_version"),
    ],
)
def test_bin_probability_validation(
    bins_version: str | None, probs: list[str], message: str
) -> None:
    from remy_finance.registry import ReturnDistribution

    with pytest.raises(ValidationError, match=message):
        ReturnDistribution(
            method="m",
            mean=D("0"),
            bins_version=bins_version,
            bin_probabilities=tuple(D(p) for p in probs),
        )
    ok = ReturnDistribution(
        method="m",
        mean=D("0"),
        bins_version="EVAL-1-BINS",
        bin_probabilities=(D("0.2"), D("0.8")) + (D(0),) * 7,
    )
    assert ok.canonical_json().count("ReturnDistribution@2") == 1
