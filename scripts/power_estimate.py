"""Print the EVAL-1 power tables (docs/evaluation/power-estimate.md). Offline; no data."""

from __future__ import annotations

from dataclasses import replace

from remy_finance.reports.power import (
    Assumptions,
    detectable_mean,
    months_needed,
    n_eff_for_coverage,
    n_eff_for_difference,
    n_eff_for_mean,
)

BASE = Assumptions()


def fmt(x: float) -> str:
    return "never" if x == float("inf") else (f"{x:.0f}" if x >= 10 else f"{x:.1f}")


def main() -> None:
    print("## Effective observations per month (per baseline)\n")
    print("| fill rate | rho = 0.05 | rho = 0.10 | rho = 0.20 | rho = 0.30 |")
    print("|---|---|---|---|---|")
    for f in (0.5, 0.7, 0.9):
        cells = [
            fmt(replace(BASE, fill_rate=f, rho=r).effective_per_month)
            for r in (0.05, 0.1, 0.2, 0.3)
        ]
        a = replace(BASE, fill_rate=f)
        print(f"| {f:.0%} ({a.filled_per_month:.0f} filled) | " + " | ".join(cells) + " |")

    print("\n## Months to detect a mean net return of delta (one sample, sigma = 0.7)\n")
    print("| delta | n_eff needed | rho = 0.05 | rho = 0.10 | rho = 0.20 | rho = 0.30 |")
    print("|---|---|---|---|---|---|")
    for d in (0.02, 0.05, 0.10, 0.20):
        n = n_eff_for_mean(d, BASE)
        cells = [fmt(months_needed(n, replace(BASE, rho=r))) for r in (0.05, 0.1, 0.2, 0.3)]
        print(f"| {d:.0%} | {fmt(n)} | " + " | ".join(cells) + " |")

    print("\n## Sensitivity to sigma (rho = 0.10, delta = 0.10)\n")
    print("| sigma | months (one sample) | months per arm (MKT-1 vs RAND-1) |")
    print("|---|---|---|")
    for s in (0.4, 0.7, 1.0):
        a = replace(BASE, sigma=s)
        one = months_needed(n_eff_for_mean(0.10, a), a)
        two = months_needed(n_eff_for_difference(0.10, a), a)
        print(f"| {s} | {fmt(one)} | {fmt(two)} |")

    print("\n## Smallest detectable mean after 1, 3, 6, 12, 24 months (base case)\n")
    print("| months | 1 | 3 | 6 | 12 | 24 |")
    print("|---|---|---|---|---|---|")
    print(
        "| delta | "
        + " | ".join(f"{detectable_mean(m, BASE):.1%}" for m in (1, 3, 6, 12, 24))
        + " |"
    )

    print("\n## Tail coverage (forecast 5%; months to detect the true rate)\n")
    print("| true rate | n_eff needed | months (rho = 0.10) |")
    print("|---|---|---|")
    for p1 in (0.075, 0.10, 0.15):
        n = n_eff_for_coverage(0.05, p1, BASE)
        print(f"| {p1:.1%} | {fmt(n)} | {fmt(months_needed(n, BASE))} |")


if __name__ == "__main__":
    main()
