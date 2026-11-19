# DR-0043: Run logs record their code commit (BaselineRun schema 2)

- **Status:** proposed (2026-11-19; awaiting Josh's confirmation)
- **Date:** 2026-11-19
- **Source:** Codex X02 (`docs/review/R1-extras-findings.md`). Extends [DR-0028](DR-0028-registry-details.md) §11 (run logs) and supports [DR-0040](DR-0040-underlying-forecast-scores.md).
- **Stage:** R1 maintenance

## Context
- **What changed:** DR-0040 scores MKT-1's S_T/F forecasts from run logs, including null decisions that recommend nothing.
- **The problem:** EVAL-1 §7 requires the code commit of every scored engine. Report headers listed commits only from recommendations, and `BaselineRun` had no commit field, so a scored forecast from a null run had no provenance at all.

## Options considered
1. **Add `code_commit` to `BaselineRun` as schema 2, and keep schema-1 logs readable** (chosen).
2. Infer the commit from recommendations made at the same snapshot. That guesses, and it fails for null decisions.
3. Write a sidecar file next to each run log. That's a second artefact to keep consistent.

## Decision
- **Schema 2:** `BaselineRun` is at schema 2, with `code_commit` (the commit the pipeline ran). MKT-1 and RAND-1 fill it in.
- **Legacy logs:** `Record` gains `LEGACY_SCHEMA_VERSIONS`. `BaselineRun` lists `(1,)`, so schema-1 logs, including the live run's on the Mac, load with `code_commit` None. Their files are never rewritten.
- **Report headers:** they list every engine/version/code-commit combination from the recommendations and, when run logs are scored, from those logs too. A log without a commit shows "code commit not recorded"; nothing is guessed.

## Consequences
- **Run logs from the Mac's next pull onward** carry their commit. The schema-1 logs from 11–19 November show "not recorded". The recommendations from those sessions carry the commit `aa53762` or `fc8b424`, so the provenance is still recoverable for audit.
- **The legacy mechanism is general,** but each use needs its own DR: a legacy version may differ only by fields added later with defaults.
