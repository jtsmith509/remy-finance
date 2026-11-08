from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

import pytest

from remy_finance.core import canonical


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.50", "2.5"),
        ("2.5", "2.5"),
        ("100", "100"),
        ("1E+2", "100"),
        ("100.000", "100"),
        ("0", "0"),
        ("-0", "0"),
        ("0.000", "0"),
        ("-1.230", "-1.23"),
        ("0.0001", "0.0001"),
        ("1E-7", "0.0000001"),
    ],
)
def test_decimal_normal_form(raw: str, expected: str) -> None:
    assert canonical.canonical_decimal(Decimal(raw)) == expected


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_decimal_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        canonical.canonical_decimal(Decimal(raw))


def test_datetime_is_utc_with_microseconds() -> None:
    et = timezone(timedelta(hours=-5))
    dt = datetime(2026, 11, 6, 10, 0, tzinfo=et)
    assert canonical.canonical_datetime(dt) == "2026-11-06T15:00:00.000000Z"


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        canonical.to_canonical(datetime(2026, 11, 6, 10, 0))  # noqa: DTZ001


def test_float_rejected_anywhere() -> None:
    with pytest.raises(TypeError):
        canonical.to_canonical({"a": [1, 2.5]})


def test_dumps_is_compact_and_sorted() -> None:
    text = canonical.canonical_json({"b": 1, "a": {"d": True, "c": None}})
    assert text == '{"a":{"c":null,"d":true},"b":1}'


def test_sets_sorted_and_dates_iso() -> None:
    class Color(StrEnum):
        RED = "red"
        BLUE = "blue"

    data = {"s": frozenset({"z", "a"}), "d": date(2026, 1, 2), "e": Color.RED}
    assert canonical.canonical_json(data) == '{"d":"2026-01-02","e":"red","s":["a","z"]}'


def test_non_str_keys_rejected() -> None:
    with pytest.raises(TypeError):
        canonical.to_canonical({1: "x"})


def test_sha256_known_value() -> None:
    assert canonical.sha256_hex("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_utc_constant_used() -> None:
    assert canonical.canonical_datetime(datetime(2026, 1, 1, tzinfo=UTC)).endswith("Z")


def test_long_decimals_are_not_rounded() -> None:
    # Regression: Decimal.normalize() rounds to 28 significant digits.
    d = Decimal("8.3951311347944737426358025309E-28")
    assert Decimal(canonical.canonical_decimal(d)) == d
    big = Decimal("12345678901234567890123456789012345.67")
    assert canonical.canonical_decimal(big) == "12345678901234567890123456789012345.67"
