# Decision records

| DR | Title | Default | Status |
|---|---|---|---|
| [DR-0001](DR-0001-tooling.md) | Tooling and core libraries | D1 | accepted |
| [DR-0002](DR-0002-snapshot-times.md) | Snapshot times | D2 | accepted |
| [DR-0003](DR-0003-execution-latency.md) | Execution latency and execution quote | D3 | accepted |
| [DR-0004](DR-0004-universe-rule.md) | Point-in-time universe rule | D4 | accepted |
| [DR-0005](DR-0005-fees.md) | Fee schedule FEES-1 | D5 | accepted |
| [DR-0006](DR-0006-sizing.md) | Paper sizing rule | D6 | accepted |
| [DR-0007](DR-0007-selections-per-snapshot.md) | Selections per snapshot | D7 | accepted |
| [DR-0008](DR-0008-candidate-bounds.md) | Candidate bounds MOVEGEN-1 | D8 | accepted |
| [DR-0009](DR-0009-assignment-threshold.md) | Early-assignment threshold ASSIGN-1 | D9 | accepted |
| [DR-0010](DR-0010-expiration-handling.md) | HOLD-1 and EXP-1 | D10 | accepted |
| [DR-0011](DR-0011-risk-free-rate.md) | Risk-free rate source | D11 | accepted |
| [DR-0012](DR-0012-registry-storage.md) | Registry and snapshot storage | D12 | accepted |
| [DR-0013](DR-0013-underlying-quote-source.md) | Underlying quote source (Stock Standard) | R0.1 | accepted |
| [DR-0014](DR-0014-dividends-corporate-actions.md) | Dividend and corporate-action source (Polygon) | R0.1 | accepted |
| [DR-0015](DR-0015-quote-age.md) | Quote age handling QAGE-1 | R0.1 | accepted |
| [DR-0016](DR-0016-canonical-serialization.md) | Canonical serialization and hashing CANON-1 | R0.2 | accepted |
| [DR-0017](DR-0017-time-model.md) | Time model and the board's look-ahead invariant | R0.2 | accepted |
| [DR-0018](DR-0018-payoff-conventions.md) | Payoff and capital-at-risk conventions; priced structures | R0.6 | accepted |
| [DR-0019](DR-0019-assign-1-details.md) | ASSIGN-1 implementation details | R0.9 | accepted |
| [DR-0020](DR-0020-movegen-lattice.md) | Move-generator strike lattice and enumeration details | R0.7 | accepted |
| [DR-0021](DR-0021-data-layer.md) | Data-layer semantics (PointInTimeView, adapters) | R0.3 | accepted |
| [DR-0022](DR-0022-snapshot-store.md) | Snapshot store rules SNAPSTORE-1 | R0.4 | accepted |
| [DR-0023](DR-0023-univ-1-details.md) | UNIV-1 implementation details; option-volume read path | R0.5 | accepted |
| [DR-0024](DR-0024-slot-finality.md) | Atomic slot finality and per-slot gap files (amends DR-0022) | R0 review | accepted |
| [DR-0025](DR-0025-exact-decimal-arithmetic.md) | Exact Decimal arithmetic and strict numeric inputs | R0 review | accepted |
| [DR-0026](DR-0026-data-source-matrix.md) | Which source supplies each data type (ThetaData, Massive, calendar) | R0.1 ext. | accepted |
| [DR-0027](DR-0027-schedule-declared-dividends.md) | Schedule-declared dividends public on the ex-date (amends DR-0014) | R0.1 ext. | accepted |
| [DR-0028](DR-0028-registry-details.md) | Registry implementation details REGISTRY-1 | R1.1 | proposed |

Each record has these sections: context, options considered, decision, consequences. Status stays *proposed* until Josh confirms it. A confirmed DR is superseded only by a new, linked DR; it is never edited in place.
