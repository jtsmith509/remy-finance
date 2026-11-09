"""Registry records: frozen recommendations and journal events (handoff R1.1, spec §11).

A ``Recommendation`` is the frozen, falsifiable prediction an engine (or baseline) made at a
decision snapshot. A ``JournalEvent`` records what then happened to it, append-only:
orders, fills, exits, assumed assignments, settlements, closes, annotations, corrections.
Nothing is ever edited; a correction is a new event that links to the row it corrects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from remy_finance.core import Candidate, Mode, Priceability, Record
from remy_finance.core.exact import exact_decimal
from remy_finance.core.types import Dec, NonNegDec, UtcDatetime

ADJ_0 = "ADJ-0"  # no ranking adjustment in R1 (spec §9; uncertainty-adjusted ranking is later)


class Quantile(Record):
    probability: Dec = Field()
    value: Dec

    @field_validator("probability")
    @classmethod
    def _in_unit_interval(cls, value: Decimal) -> Decimal:
        if not 0 < value < 1:
            raise ValueError("quantile probability must be in (0, 1)")
        return value


class ReturnDistribution(Record):
    """Predicted net return on capital at risk (fractions: -1 = lose all capital at risk).

    Baselines record summary quantiles and the mean (handoff R1.1); richer engines may add
    more quantiles under the same type.
    """

    method: str = Field(min_length=1)
    mean: Dec
    quantiles: tuple[Quantile, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        probs = [q.probability for q in self.quantiles]
        if probs != sorted(probs) or len(set(probs)) != len(probs):
            raise ValueError("quantiles must have strictly increasing probabilities")
        values = [q.value for q in self.quantiles]
        if values != sorted(values):
            raise ValueError("quantile values must be non-decreasing")
        return self


def derive_trade_id(engine: str, engine_version: str, snapshot_id: str, candidate_hash: str) -> str:
    """Deterministic: the same engine, version, board and candidate give the same id
    (replay reruns reproduce ids); different inputs give different ids."""
    text = "\n".join((engine, engine_version, snapshot_id, candidate_hash))
    return "T-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class Recommendation(Record):
    """A frozen, falsifiable paper-trade recommendation (spec §11 required fields)."""

    trade_id: str
    mode: Mode
    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    code_commit: str = Field(min_length=7)
    artifact_hash: str = Field(min_length=1)  # hash of the engine's configuration/model
    decision_time: UtcDatetime  # the decision snapshot's as_of
    receipt_time: UtcDatetime  # when the recommendation was recorded
    snapshot_id: str = Field(min_length=64, max_length=64)
    quote_provenance: tuple[tuple[str, str], ...] = ()
    rank: int = Field(ge=1)
    candidate: Candidate  # legs, priced structure, quantity, entry rule, exit policy
    entry_conditions: str = Field(min_length=1)
    cancellation_conditions: str = Field(min_length=1)
    expiration: date
    horizon: str = Field(min_length=1)
    fill_rule_version: str = Field(min_length=1)
    sizing_version: str = Field(min_length=1)
    capital_at_risk: NonNegDec  # total = per-unit capital at risk x quantity
    predicted_return: ReturnDistribution
    expected_return_pct: Dec  # equals predicted_return.mean
    expected_return_dollars: Dec  # = expected_return_pct x capital_at_risk
    tail_measures: tuple[tuple[str, Dec], ...] = ()
    probability_of_fill: Dec | None = None
    probability_of_fill_reason: str | None = None
    exit_policy_id: str = Field(min_length=1)
    ranking_adjustment: str = ADJ_0
    random_seed: str | None = None
    search_config: tuple[tuple[str, str], ...] = ()  # causal depth etc.; empty in R1
    opening_book_id: str | None = None  # R3+; None in R1

    @model_validator(mode="after")
    def _check(self) -> Self:
        c = self.candidate
        s = c.structure
        if c.is_null_move or s is None or c.priceability is not Priceability.PRICED:
            raise ValueError("only priced, non-null candidates can be recommended")
        if c.quantity < 1:
            raise ValueError("a recommendation needs quantity >= 1 (SIZE-1 gave 0)")
        if s.expiration != self.expiration:
            raise ValueError("expiration must match the structure's legs")
        if c.exit_policy_id != self.exit_policy_id or c.entry_rule != self.fill_rule_version:
            raise ValueError("exit policy and fill rule must match the candidate")
        with exact_decimal(s.capital_at_risk, self.expected_return_pct):
            if self.capital_at_risk != s.capital_at_risk * c.quantity:
                raise ValueError("capital_at_risk must equal per-unit CaR x quantity")
            if self.expected_return_dollars != self.expected_return_pct * self.capital_at_risk:
                raise ValueError("expected_return_dollars must equal pct x capital_at_risk")
        if self.expected_return_pct != self.predicted_return.mean:
            raise ValueError("expected_return_pct must equal the predicted distribution's mean")
        if self.probability_of_fill is None and not self.probability_of_fill_reason:
            raise ValueError("a missing probability_of_fill needs a reason")
        if self.probability_of_fill is not None and not 0 <= self.probability_of_fill <= 1:
            raise ValueError("probability_of_fill must be in [0, 1]")
        if self.decision_time > self.receipt_time:
            raise ValueError("a recommendation cannot be recorded before its decision time")
        expected = derive_trade_id(
            self.engine, self.engine_version, self.snapshot_id, c.content_hash()
        )
        if self.trade_id != expected:
            raise ValueError(f"trade_id must be {expected} (derived from engine and candidate)")
        return self


class EventKind(StrEnum):
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    LEG_FILLED = "LEG_FILLED"
    ORDER_UNFILLED = "ORDER_UNFILLED"
    EXIT_SUBMITTED = "EXIT_SUBMITTED"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_UNFILLED = "EXIT_UNFILLED"
    ASSIGNMENT_ASSUMED = "ASSIGNMENT_ASSUMED"
    EXPIRATION_SETTLED = "EXPIRATION_SETTLED"
    TRADE_CLOSED = "TRADE_CLOSED"
    ANNOTATION = "ANNOTATION"
    CORRECTION = "CORRECTION"


class JournalEvent(Record):
    """One append-only fact about a recommendation.

    ``evidence_type``/``evidence`` carry the canonical JSON of the record that justifies
    the event (an ``OrderFill``, ``AssignmentAssessment``, ``ExpirationSettlement``,
    ``StockFill``...). ``cash`` is the event's signed cash effect in dollars (0 if none).
    A ``CORRECTION`` names the journal sequence number it corrects in ``corrects_seq``.
    """

    trade_id: str = Field(min_length=1)
    mode: Mode
    kind: EventKind
    event_time: UtcDatetime  # when it happened (market time for fills/settlements)
    public_time: UtcDatetime | None = None  # when the triggering fact became public, if different
    recorded_at: UtcDatetime
    snapshot_id: str | None = None  # the board the event was evaluated on, if any
    cash: Dec = Decimal(0)
    evidence_type: str | None = None
    evidence: str | None = None
    note: str = ""
    corrects_seq: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.evidence_type is None) != (self.evidence is None):
            raise ValueError("evidence_type and evidence go together")
        if self.evidence is not None:
            data = json.loads(self.evidence)
            if not isinstance(data, dict) or not str(data.get("$type", "")).startswith(
                f"{self.evidence_type}@"
            ):
                raise ValueError("evidence must be canonical JSON of the named record type")
        if (self.kind is EventKind.CORRECTION) != (self.corrects_seq is not None):
            raise ValueError("CORRECTION events, and only they, link the row they correct")
        if self.kind in (EventKind.ANNOTATION, EventKind.CORRECTION) and not self.note:
            raise ValueError(f"{self.kind.value} needs a note")
        if self.event_time > self.recorded_at:
            raise ValueError("an event cannot be recorded before it happens")
        if self.public_time is not None and self.public_time > self.recorded_at:
            raise ValueError("an event cannot be recorded before its trigger is public")
        return self

    @classmethod
    def with_evidence(cls, evidence: Record, **fields: object) -> JournalEvent:
        data = {**fields, "evidence_type": type(evidence).__name__}
        data["evidence"] = evidence.canonical_json()
        return cls.model_validate(data)
