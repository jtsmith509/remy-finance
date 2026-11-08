"""R0.2 tests: round-trip serialization, hash stability, immutability, Decimal-only fields."""

from __future__ import annotations

import inspect
import typing
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

import remy_finance.core as core
from remy_finance.core import (
    Candidate,
    CorporateAction,
    CorporateActionType,
    Leg,
    LookaheadError,
    OptionContract,
    OptionQuote,
    Priceability,
    Provenance,
    QualityFlag,
    QualityNote,
    Record,
    Right,
    Side,
    Structure,
    StructureType,
)
from tests import factories as f

ET = timezone(timedelta(hours=-5))


def all_records() -> list[Record]:
    ca = CorporateAction(
        symbol="XYZ",
        action_type=CorporateActionType.SPLIT,
        effective_date=date(2026, 12, 1),
        details=(("ratio_to", "2"), ("ratio_from", "1")),
        event_time=f.AS_OF - timedelta(days=30),
        public_time=f.AS_OF - timedelta(days=30),
        source="fixture",
    )
    structure = f.bull_call_spread()
    return [
        f.contract(),
        f.option_quote(),
        f.underlying_quote(),
        f.dividend(),
        ca,
        f.rate(),
        Provenance(source="thetadata", details=(("terminal", "v3"),)),
        QualityNote(scope="XYZ", code="GAP", detail="missing chain"),
        Leg(contract=f.contract(), side=Side.SHORT, ratio=2),
        structure,
        Candidate.priced(structure, quantity=3, entry_rule="FILL-1", exit_policy_id="HOLD-1"),
        Candidate(
            legs=structure.legs,
            structure_type=structure.structure_type,
            quantity=0,
            entry_rule="FILL-1",
            exit_policy_id="HOLD-1",
            priceability=Priceability.UNPRICEABLE,
            unpriceable_reason="ONE_SIDED_LEG",
        ),
        core.FEES_1,
        Candidate.null_move(),
        f.market_state(corporate_actions=(ca,)),
    ]


# --------------------------------------------------------------------- round trip


@pytest.mark.parametrize("record", all_records(), ids=lambda r: type(r).__name__)
def test_round_trip_is_identity(record: Record) -> None:
    text = record.canonical_json()
    back = type(record).from_canonical_json(text)
    assert back == record
    assert back.canonical_json() == text
    assert back.content_hash() == record.content_hash()


@pytest.mark.parametrize("record", all_records(), ids=lambda r: type(r).__name__)
def test_type_tag_is_enforced(record: Record) -> None:
    text = record.canonical_json()
    other = OptionContract if not isinstance(record, OptionContract) else Leg
    with pytest.raises(ValidationError):
        other.from_canonical_json(text)


# ----------------------------------------------------------------- hash stability

GOLDEN_QUOTE_HASH = "30f01b8880256ef1f58e69fdcea11bd2a30d052dfd99478ccab3fa3dac8730aa"


def golden_quote() -> OptionQuote:
    return OptionQuote(
        contract=OptionContract.build("AAPL", date(2026, 11, 20), "340", Right.CALL),
        bid=Decimal("2.50"),
        ask=Decimal("2.55"),
        bid_size=10,
        ask_size=12,
        quote_time=datetime(2026, 11, 6, 14, 59, 55, 898000, tzinfo=UTC),
        source="thetadata",
    )


def test_golden_hash_is_pinned() -> None:
    """Changing CANON-1 or a field changes this hash: that needs a DR and a version bump."""
    assert golden_quote().content_hash() == GOLDEN_QUOTE_HASH


def test_equal_values_hash_equal() -> None:
    a = golden_quote()
    b = OptionQuote(
        contract=OptionContract.build("AAPL", date(2026, 11, 20), Decimal("340.000"), Right.CALL),
        bid=Decimal("2.5"),
        ask="2.550",
        bid_size=10,
        ask_size=12,
        quote_time=datetime(2026, 11, 6, 9, 59, 55, 898000, tzinfo=ET),  # same instant
        source="thetadata",
    )
    assert a.content_hash() == b.content_hash()


def test_any_field_change_changes_hash() -> None:
    a = golden_quote()
    assert a.model_copy(update={"bid_size": 11}).content_hash() != a.content_hash()
    assert a.model_copy(update={"ask": Decimal("2.56")}).content_hash() != a.content_hash()


def test_market_state_id_independent_of_input_order() -> None:
    quotes = f.market_state().option_quotes
    s1 = f.market_state(option_quotes=quotes)
    s2 = f.market_state(option_quotes=tuple(reversed(quotes)))
    assert s1.snapshot_id == s2.snapshot_id
    assert s1.snapshot_id == s1.content_hash()


def test_quality_flags_normalized() -> None:
    q1 = golden_quote().model_copy(
        update={"quality_flags": (QualityFlag.STALE_QUOTE, QualityFlag.CROSSED)}
    )
    q2 = golden_quote().model_copy(
        update={
            "quality_flags": (QualityFlag.CROSSED, QualityFlag.STALE_QUOTE, QualityFlag.CROSSED)
        }
    )
    assert q1.content_hash() == q2.content_hash()


# ------------------------------------------------------------------- immutability


@pytest.mark.parametrize("record", all_records(), ids=lambda r: type(r).__name__)
def test_records_are_frozen(record: Record) -> None:
    name = next(iter(type(record).model_fields))
    with pytest.raises(ValidationError):
        setattr(record, name, getattr(record, name))


def test_collections_are_tuples() -> None:
    state = f.market_state()
    for name in type(state).model_fields:
        value = getattr(state, name)
        assert not isinstance(value, list | dict | set), name


def test_model_copy_revalidates() -> None:
    with pytest.raises(ValidationError):
        golden_quote().model_copy(update={"bid": Decimal("-1")})


def test_extra_fields_forbidden() -> None:
    data: dict[str, Any] = {
        name: getattr(f.market_state(), name) for name in core.MarketState.model_fields
    }
    data["score"] = Decimal("1")  # the board must never carry model outputs
    with pytest.raises(ValidationError):
        core.MarketState.model_validate(data)


# ----------------------------------------------------------- Decimal-only numerics


@pytest.mark.parametrize("bad", [2.5, True])
@pytest.mark.parametrize("field", ["bid", "ask"])
def test_cash_fields_reject_float_and_bool(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        golden_quote().model_copy(update={field: bad})


def test_decimal_fields_accept_int_and_str() -> None:
    q = golden_quote().model_copy(update={"bid": 2, "ask": "2.55"})
    assert q.bid == Decimal(2) and isinstance(q.bid, Decimal)


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_non_finite_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        golden_quote().model_copy(update={"ask": Decimal(bad)})


def _mentions_float(annotation: Any) -> bool:
    if annotation is float:
        return True
    return any(_mentions_float(a) for a in typing.get_args(annotation))


def test_no_record_declares_a_float_field() -> None:
    record_types = [
        obj
        for module in (core.instruments, core.market, core.record, core.structure, core.fees)
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Record)
    ]
    assert len(record_types) >= 10
    for cls in record_types:
        for name, field in cls.model_fields.items():
            assert not _mentions_float(field.annotation), f"{cls.__name__}.{name}"


# ------------------------------------------------------------------- timestamps


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        golden_quote().model_copy(update={"quote_time": datetime(2026, 11, 6, 10, 0)})  # noqa: DTZ001


def test_numeric_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        golden_quote().model_copy(update={"quote_time": 1_700_000_000})


def test_datetimes_normalized_to_utc() -> None:
    q = golden_quote().model_copy(update={"quote_time": datetime(2026, 11, 6, 10, 0, tzinfo=ET)})
    assert q.quote_time.tzinfo is UTC and q.quote_time.hour == 15


# --------------------------------------------------------------- OptionContract


def test_osi_built_and_normalized() -> None:
    c = OptionContract.build("AAPL", date(2026, 11, 20), "340", Right.CALL)
    assert c.osi_symbol == "AAPL  261120C00340000"
    compact = OptionContract.model_validate({**c.model_dump(), "osi_symbol": "AAPL261120C00340000"})
    assert compact == c
    assert OptionContract.build("F", date(2026, 11, 20), "12.5", Right.PUT).osi_symbol == (
        "F     261120P00012500"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"strike": Decimal("345")},
        {"right": Right.PUT},
        {"expiration": date(2026, 11, 27)},
        {"underlying": "MSFT"},
        {"strike": Decimal("0")},
        {"strike": Decimal("340.0005")},
        {"multiplier": 10},
    ],
)
def test_contract_inconsistencies_rejected(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OptionContract.build("AAPL", date(2026, 11, 20), "340", Right.CALL).model_copy(
            update=update
        )


def test_adjusted_root_cannot_be_standard() -> None:
    with pytest.raises(ValidationError):
        OptionContract.build("AAPL", date(2026, 11, 20), "340", Right.CALL, root="AAPL1")
    adj = OptionContract.build(
        "AAPL", date(2026, 11, 20), "340", Right.CALL, root="AAPL1", standard_deliverable=False
    )
    assert adj.root == "AAPL1"


# -------------------------------------------------------- Structure / Candidate


def test_structure_rules() -> None:
    long_c = f.leg("100", Right.CALL, Side.LONG)
    with pytest.raises(ValidationError, match="one underlying"):
        Structure.price(
            [long_c, f.leg("105", Right.CALL, Side.SHORT, sym="ABC")],
            ["1", "1"],
            StructureType.VERTICAL_DEBIT,
            core.FEES_1,
        )
    with pytest.raises(ValidationError, match="only one leg"):
        Structure.price([long_c, long_c], ["1", "1"], StructureType.LONG_CALL, core.FEES_1)
    other_exp = Leg(
        contract=OptionContract.build("XYZ", date(2026, 12, 18), "105", Right.CALL),
        side=Side.SHORT,
    )
    with pytest.raises(ValidationError, match="one expiration"):
        Structure.price([long_c, other_exp], ["2", "1"], StructureType.VERTICAL_DEBIT, core.FEES_1)
    with pytest.raises(ValidationError, match="one price per leg"):
        f.bull_call_spread().model_copy(update={"entry_prices": (Decimal("2.5"),)})


@pytest.mark.parametrize(
    "update",
    [
        {"capital_at_risk": Decimal("151.29")},
        {"max_loss": Decimal("149")},
        {"max_gain": Decimal("351")},
        {"max_gain": None},
        {"entry_prices": (Decimal("2.40"), Decimal("1.00"))},
    ],
)
def test_structure_numbers_must_match_legs(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="imply"):
        f.bull_call_spread().model_copy(update=update)


def test_candidate_rules() -> None:
    null = Candidate.null_move()
    assert null.is_null_move and null.quantity == 0
    with pytest.raises(ValidationError):
        null.model_copy(update={"quantity": 1})
    s = f.bull_call_spread()
    with pytest.raises(ValidationError, match="reason"):
        Candidate(
            legs=s.legs,
            structure_type=s.structure_type,
            quantity=1,
            entry_rule="FILL-1",
            exit_policy_id="HOLD-1",
            priceability=Priceability.UNPRICEABLE,
        )
    with pytest.raises(ValidationError, match="priced structure"):
        Candidate(
            legs=s.legs,
            structure_type=s.structure_type,
            quantity=1,
            entry_rule="FILL-1",
            exit_policy_id="HOLD-1",
            priceability=Priceability.PRICED,
        )
    with pytest.raises(ValidationError, match="unbounded"):
        Candidate(
            legs=(f.leg("100", Right.CALL, Side.SHORT),),
            structure_type=StructureType.OTHER_DEFINED_RISK,
            quantity=1,
            entry_rule="FILL-1",
            exit_policy_id="HOLD-1",
            priceability=Priceability.UNPRICEABLE,
            unpriceable_reason="ONE_SIDED_LEG",
        )
    assert not Candidate.priced(s, 1, "FILL-1", "HOLD-1").is_null_move


# ------------------------------------------------------------------- MarketState


def test_board_rejects_quote_after_as_of() -> None:
    late = f.option_quote("110", at=f.AS_OF + timedelta(microseconds=1))
    with pytest.raises(LookaheadError, match="after as_of"):
        f.market_state(option_quotes=(late,))


def test_board_rejects_underlying_quote_after_as_of() -> None:
    with pytest.raises(LookaheadError):
        f.market_state(underlying_quotes=(f.underlying_quote(at=f.AS_OF + timedelta(seconds=1)),))


def test_board_rejects_dividend_not_yet_public() -> None:
    with pytest.raises(LookaheadError, match="dividend"):
        f.market_state(dividends=(f.dividend(public=f.AS_OF + timedelta(hours=1)),))


def test_board_accepts_observation_exactly_at_as_of() -> None:
    state = f.market_state(underlying_quotes=(f.underlying_quote(at=f.AS_OF),))
    assert state.underlying_quotes[0].quote_time == state.as_of


def test_board_rejects_duplicates_and_outsiders() -> None:
    q = f.option_quote("100")
    with pytest.raises(ValidationError, match="more than one quote"):
        f.market_state(option_quotes=(q, q))
    with pytest.raises(ValidationError, match="outside the universe"):
        f.market_state(option_quotes=(f.option_quote("100", sym="ABC"),))
    with pytest.raises(ValidationError, match="duplicate underlyings"):
        f.market_state(underlyings=("XYZ", "XYZ"))
