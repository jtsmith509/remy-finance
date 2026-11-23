# EVAL-2 §5 validation evidence (frozen with EVAL-2)

This folder is the evidence behind EVAL-2 §5's choice of inference method for P3 and P4 (Codex E01). The files are archived verbatim. They are excluded from the project's lint, because they are kept exactly as they were run.

| File | What it is | How it was run |
|---|---|---|
| `naive_bootstrap_stress.py` / `.json` | Audit stress study: a naive week bootstrap and two block bootstraps under a common loading μ and AR(1) week effects | `python naive_bootstrap_stress.py`; seeds in the script |
| `blind_reproduction_a.py` / `.json` | Blind implementation A (loop-based): EWC with K = ⌊W/10⌋, ⌊W/8⌋, ⌊W/12⌋, plus the naive bootstrap; R = 4,000 per null cell | `uv run python blind_reproduction_a.py`; seeds in the script |
| `blind_reproduction_b.py` / `.json` | Blind implementation B (vectorized); same grid | as above |
| `blind_reproduction_c.py` / `.json` | Blind implementation C (explicit cosine matrix, plus a pure iid check) | as above |
| `high_r_size_study.py` / `.json` | Reference study at **R = 100,000** replications per cell; μ ∈ {0, 0.5, 1, 2}, holds of up to 4, 8 or 12 weeks, plus AR(1), t(3) and 35%-empty-week variants and power cells | `python high_r_size_study.py 100000 high_r_size_study.json`. Seeds `default_rng([555, cell_index])`. It used scipy's t quantiles, from the reconciler's environment; scipy is not a project dependency. |

**Shared synthetic design.** Weekly boards ~ Poisson(10), with weeks empty with probability q. Holds ~ U{1..Hmax} weeks. Board differences are d = (μ + 0.7·c_t + 0.3·z_b)·(sum of hold-period weekly shocks)/√H + 0.5·e_b, so the null is exact, and weeks are dependent through μ and overlapping holds. The design models a single board-weighted mean with cross-week dependence, not the two-arm fill selection (EVAL-2 §3).

**History.**
1. A first study, under a design without cross-week covariance, is superseded and not archived. An audit found the flaw.
2. Three blind reproductions agreed within Monte Carlo error.
3. A high-R reference settled the remaining spread between them.

See `docs/review/EVAL-2-findings.md`.
