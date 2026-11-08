# DR-0001: Tooling and core libraries

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D1
- **Stage:** R0

## Context
Python is settled (handoff §1). We still need a toolchain that makes correctness cheap to check: type checking where errors would be costly, property tests for payoff maths, a columnar format for snapshots, and an exchange calendar that knows US holidays and half-days.

## Options considered
1. **Proposed default:** Python 3.12, `uv`, `pytest` + `hypothesis`, `ruff` (lint + format), `mypy` (strict on `core/` and `registry/`), `pydantic` v2 frozen models, `polars` + `pyarrow` (Parquet), `exchange_calendars`, `typer` CLI.
2. Poetry or pip-tools instead of uv. They work, but they're slower and offer no advantage here.
3. pandas instead of polars. It has a larger ecosystem, but it's weaker on strict dtypes and immutability, and it's slower on chain-sized frames.
4. dataclasses or attrs instead of pydantic. They're lighter, but we'd have to hand-write validation and canonical serialization.

## Decision
Adopt option 1. Pin Python `>=3.12,<3.13`. Add `httpx` as the HTTP client for the Theta Terminal REST API; it's a small addition beyond D1, and the script and adapter need it. Configuration lives in `pyproject.toml`.

## Consequences
- Strict mypy on `core` and `registry` catches Decimal/float mixing and `Optional` mistakes where they matter most.
- Frozen pydantic models give immutability and validation, but canonical JSON (sorted keys, fixed Decimal formatting) still needs a dedicated serializer in R0.2. Don't rely on `model_dump_json` defaults for hashing.
- `exchange_calendars` releases must be kept current, because holiday tables change. Pin the version in `uv.lock`.
