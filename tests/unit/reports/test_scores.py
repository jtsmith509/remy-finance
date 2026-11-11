"""EVAL-1 §2.1 scores."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from remy_finance.registry import EVAL_1_BINS, Quantile, ReturnDistribution
from remy_finance.reports.scores import crps, log_score, pit, score

D = Decimal
LEVELS = [0.1, 0.5, 0.9]
VALUES = [-0.5, 0.0, 0.5]


def dist(**kw: object) -> ReturnDistribution:
    data: dict[str, object] = {
        "method": "t",
        "mean": D("0"),
        "quantiles": tuple(
            Quantile(probability=D(str(t)), value=D(str(v)))
            for t, v in zip(LEVELS, VALUES, strict=True)
        ),
        "bins_version": EVAL_1_BINS.version,
        "bin_probabilities": (
            D("0.1"),
            D("0.1"),
            D("0.1"),
            D("0.2"),
            D("0.2"),
            D("0.1"),
            D("0.1"),
            D("0.1"),
            D("0"),
        ),
    }
    data.update(kw)
    return ReturnDistribution.model_validate(data)


def test_crps_quantile_form() -> None:
    assert crps(LEVELS, VALUES, 0.0) == pytest.approx(2 * (0.1 * 0.5 + 0 + 0.1 * 0.5) / 3)
    assert crps([0.5], [1.0], 1.0) == 0.0  # a point forecast that is exactly right
    assert crps(LEVELS, VALUES, 3.0) > crps(LEVELS, VALUES, 0.6) > 0  # worse when further off


def test_log_score_uses_the_realized_bin_with_a_floor() -> None:
    d = dist()
    assert log_score(d, D("0.1")) == pytest.approx(-math.log(0.2))
    assert log_score(d, D("5")) == pytest.approx(-math.log(1e-4))  # p = 0: floored


def test_pit_interpolates_and_randomizes_jumps_deterministically() -> None:
    # A single quantile equal to r is an interpolation knot, not a jump (Codex R1.6 F06).
    assert pit(LEVELS, VALUES, 0.0, "t") == pytest.approx(0.5)
    assert {pit(LEVELS, VALUES, 0.0, s) for s in "abcdef"} == {0.5}
    # At the lowest quantile the unrecorded tail below it is a possible jump: [0, 0.1].
    assert 0.0 <= pit(LEVELS, VALUES, -0.5, "t") <= 0.1
    assert pit(LEVELS, VALUES, 0.25, "t") == pytest.approx(0.7)
    low = pit(LEVELS, VALUES, -9.0, "seed-a")
    assert 0 <= low <= 0.1 and low == pit(LEVELS, VALUES, -9.0, "seed-a")
    assert 0.9 <= pit(LEVELS, VALUES, 9.0, "seed-b") <= 1
    atom = pit([0.1, 0.5, 0.9], [-1.0, -1.0, 0.5], -1.0, "x")  # point mass at max loss
    assert 0.0 <= atom <= 0.5  # the CDF jumps to 0.5 at -1, from the unrecorded tail
    assert {pit([0.1, 0.5, 0.9], [-1.0, -1.0, 0.5], -1.0, s) for s in "abcdef"} != {atom}


def test_score_bundle_and_coverage() -> None:
    q = [D("0.01"), D("0.05"), D("0.5"), D("0.95"), D("0.99")]
    v = [D("-1"), D("-0.8"), D("0"), D("0.8"), D("1.5")]
    d = dist(quantiles=tuple(Quantile(probability=a, value=b) for a, b in zip(q, v, strict=True)))
    s = score(d, D("-0.9"), "tid")
    assert s.exceed == {D("0.01"): False, D("0.05"): True, D("0.95"): False, D("0.99"): False}
    assert s.bias == pytest.approx(-0.9)
