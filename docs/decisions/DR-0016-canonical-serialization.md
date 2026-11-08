# DR-0016: Canonical serialization and content hashing (`CANON-1`)

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §4 ("canonical JSON (sorted keys, fixed decimal handling) … SHA-256 content hash"), task R0.2
- **Stage:** R0

## Context
Snapshot IDs, registry rows and the R1.1 hash chain all depend on hashing records. A hash is only useful if equal content always produces identical bytes. The handoff fixes the outline (sorted keys, fixed decimal handling, SHA-256) but not the details.

## Options considered
1. **Custom canonical JSON** (below), implemented in `remy_finance.core.canonical`.
2. RFC 8785 (JSON Canonicalization Scheme). It's a standard, but it serializes numbers as IEEE doubles, which is exactly what Decimal-only cash fields must avoid.
3. pydantic's `model_dump_json`. Its key order, Decimal formatting and datetime formatting are not guaranteed stable across pydantic versions.

## Decision
Option 1:
- **Objects:** keys sorted, separators `,` and `:`, UTF-8 output.
- **Decimal:** written as a string in exact normal form: no exponent, no trailing zeros, and no negative zero. `2.50` → `"2.5"`, `1E+2` → `"100"`. NaN and infinity are rejected. `Decimal.normalize()` is deliberately not used, because it rounds to 28 digits; a hypothesis test found this.
- **datetime:** must be timezone-aware; written in UTC as `YYYY-MM-DDTHH:MM:SS.ffffffZ`. A **date** is `YYYY-MM-DD`.
- **Enum:** written as its value.
- **Collections:** a tuple or list keeps its order. A set is sorted by canonical text.
- **Floats** are rejected anywhere in canonical data.
- **Type tag:** every record carries `"$type": "<ClassName>@<SCHEMA_VERSION>"`. A record can't be deserialized as the wrong type, and a schema change is visible in the hash.
- **Hash:** `content_hash = SHA-256(canonical text)`, as lowercase hex.
- **`MarketState.snapshot_id`** is its content hash. It is computed, not stored, so it can't refer to itself.
- **Collection order on the board:** `MarketState` stores its collections in canonical order (quotes by OSI symbol or underlying symbol, everything else by canonical text). The same board therefore has the same ID regardless of input order.

## Consequences
- A golden-hash test pins the format. Any change to these rules or to a record's fields changes hashes, and needs a new DR plus a `SCHEMA_VERSION` bump, never an in-place edit.
- Equal values hash equally: `2.5` and `2.50`, or the same instant in different timezones.
- Hashing a full 50-name board with about 300k quotes through canonical JSON may be slow. R0.4 measures it; if needed, the snapshot store can hash a canonical Parquet encoding instead, under a new DR.
