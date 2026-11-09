"""Power-estimate formulas (EVAL-1)."""

from __future__ import annotations

import math

import pytest

from remy_finance.reports.power import (
    Assumptions,
    design_effect_n,
    detectable_mean,
    months_needed,
    n_eff_for_coverage,
    n_eff_for_difference,
    n_eff_for_mean,
)


def test_design_effect_limits() -> None:
    assert design_effect_n(100, 0.0) == 100
    assert design_effect_n(100, 1.0) == 1
    assert design_effect_n(150, 0.1) == pytest.approx(150 / 15.9)
    with pytest.raises(ValueError):
        design_effect_n(10, 1.5)


def test_textbook_sample_size() -> None:
    a = Assumptions(sigma=1.0, alpha=0.05, power=0.8)
    assert n_eff_for_mean(0.25, a) == pytest.approx((1.6449 + 0.8416) ** 2 * 16, rel=1e-3)
    assert n_eff_for_difference(0.25, a) == pytest.approx(2 * n_eff_for_mean(0.25, a))


def test_months_and_detectable_mean_are_inverse() -> None:
    a = Assumptions()
    m = months_needed(n_eff_for_mean(0.1, a), a)
    assert detectable_mean(m, a) == pytest.approx(0.1)
    assert months_needed(1, Assumptions(fill_rate=0.0)) == math.inf


def test_coverage_sample_size() -> None:
    n = n_eff_for_coverage(0.05, 0.10, Assumptions())
    assert 140 < n < 160
