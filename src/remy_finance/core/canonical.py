"""Canonical JSON serialization and SHA-256 content hashing (CANON-1, DR-0016).

Rules:
- Objects: keys sorted; no insignificant whitespace; UTF-8.
- Decimal: string, normalized (no exponent, no trailing zeros, no negative zero),
  so equal values always serialize identically ("2.50" -> "2.5", "1E+2" -> "100").
- datetime: must be timezone-aware; UTC, ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.
- date: ``YYYY-MM-DD``.
- Enum: its value. bool, int, str, None: as JSON.
- tuple/list: array in order. set/frozenset: array sorted by canonical text.
- pydantic models: object of their fields plus a ``"$type"`` tag ``Name@version``.
- float, NaN and infinity are rejected: nothing that feeds a hash may be a float.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

TYPE_TAG = "$type"


def canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError(f"non-finite Decimal cannot be serialized: {value!r}")
    if value == 0:
        return "0"
    # Strip trailing zeros exactly. Decimal.normalize() is NOT used: it rounds to the
    # context precision (28 digits) and would silently change long values.
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)  # finite, checked above
    trimmed = list(digits)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
        exponent += 1
    return format(Decimal((sign, tuple(trimmed), exponent)), "f")


def canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"naive datetime cannot be serialized: {value!r}")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def type_tag(model: type[BaseModel]) -> str:
    version = getattr(model, "SCHEMA_VERSION", 1)
    return f"{model.__name__}@{version}"


Memo = dict[int, tuple[Any, Any]]
"""id(model) -> (model, canonical data). Keeping the model alive pins its id for the call."""


def to_canonical(value: Any, memo: Memo | None = None) -> Any:
    """Convert a value into plain JSON-compatible data following CANON-1.

    ``memo`` (optional) caches the canonical form of each pydantic model *instance* for the
    duration of one caller's work, so models shared across many records (contracts, legs)
    are converted once. Records are immutable, so a cached form never goes stale.
    """
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, Enum):
        return to_canonical(value.value, memo)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise TypeError(f"float is not allowed in canonical data: {value!r}")
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return canonical_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        if memo is not None and id(value) in memo:
            return memo[id(value)][1]
        out = {name: to_canonical(getattr(value, name), memo) for name in type(value).model_fields}
        out[TYPE_TAG] = type_tag(type(value))
        if memo is not None:
            memo[id(value)] = (value, out)
        return out
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical object keys must be str, got {k!r}")
            result[k] = to_canonical(v, memo)
        return result
    if isinstance(value, tuple | list):
        return [to_canonical(v, memo) for v in value]
    if isinstance(value, set | frozenset):
        items = [to_canonical(v, memo) for v in value]
        return sorted(items, key=dumps)
    raise TypeError(f"type {type(value).__name__} is not canonically serializable")


def dumps(data: Any) -> str:
    """Serialize already-canonical data to the canonical JSON text."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_json(value: Any, memo: Memo | None = None) -> str:
    return dumps(to_canonical(value, memo))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(value: Any, memo: Memo | None = None) -> str:
    return sha256_hex(canonical_json(value, memo))
