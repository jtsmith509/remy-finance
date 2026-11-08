"""Base class for every immutable, hashable REMY record."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator

from remy_finance.core import canonical


class Record(BaseModel):
    """Frozen pydantic model with canonical JSON and a SHA-256 content hash.

    - ``extra="forbid"``: no undeclared fields (the board can't smuggle in scores).
    - Collections are declared as tuples so instances are deeply immutable.
    - ``model_copy`` revalidates, so copies can't bypass validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    SCHEMA_VERSION: ClassVar[int] = 1

    @model_validator(mode="before")
    @classmethod
    def _check_type_tag(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and canonical.TYPE_TAG in data:
            expected = canonical.type_tag(cls)
            tag = data[canonical.TYPE_TAG]
            if tag != expected:
                raise ValueError(f"type tag {tag!r} does not match {expected!r}")
            return {k: v for k, v in data.items() if k != canonical.TYPE_TAG}
        return data

    def canonical_json(self) -> str:
        return canonical.canonical_json(self)

    def content_hash(self) -> str:
        return canonical.sha256_hex(self.canonical_json())

    @classmethod
    def from_canonical_json(cls, text: str | bytes) -> Self:
        return cls.model_validate(json.loads(text))

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = {name: getattr(self, name) for name in type(self).model_fields}
        data.update(update or {})
        return type(self).model_validate(data)
