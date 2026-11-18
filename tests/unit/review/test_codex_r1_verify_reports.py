"""Regression tests from Codex's verification of the R1 fixes (docs/review/R1-fixes-findings.md).

Ported from its offline probes at aa53762 (reports). Each failing probe is the regression
test for its V-finding; the passing probes guard the fixes they verified.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from remy_finance.core import Mode
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, derive_trade_id
from remy_finance.reports.bootstrap import RESAMPLES, SEED, cluster_bootstrap
from remy_finance.reports.outcomes import Status, TradeOutcome
from remy_finance.reports.render import group_stats, header
from remy_finance.reports.scores import pit
from tests.unit.registry.helpers import recommendation


def test_f04_cohort_excludes_a_trade_closed_after_analysis_cutoff():
    rec = recommendation()
    cutoff = rec.decision_time.date() + timedelta(days=65)
    now = et_at(cutoff, 19, 0)
    # The stored trade has closed by the time an historical report is requested,
    # but was still open at the requested analysis cutoff. All data are valid frozen
    # records; the age rule alone cannot decide its outcome availability.
    closed = TradeOutcome(
        rec=rec,
        status=Status.CLOSED,
        unfilled_reason=None,
        filled_at=rec.decision_time + timedelta(minutes=5),
        closed_at=now + timedelta(days=1),
        realized_pnl=Decimal("10"),
        realized_pnl_original=Decimal("10"),
        corrections=0,
        assignments=0,
        forced_exits=0,
        exit_retries=0,
        pin_flags=0,
        annotations=0,
    )
    assert closed.eval1_eligible(cutoff)
    stats = group_stats(closed.engine_group, [closed], now, cutoff)
    assert stats.closed == [], "EVAL-1 section 1.4 requires closure before the cutoff too"
    assert stats.scores == []


def test_f06_interior_point_mass_uses_only_supported_equal_level_edges():
    levels = [0.05, 0.2, 0.4, 0.6, 0.95]
    values = [-2.0, -1.0, 0.0, 0.0, 1.0]
    points = [pit(levels, values, 0.0, str(i)) for i in range(100)]
    assert min(points) >= 0.4 and max(points) <= 0.6
    assert len(set(points)) > 90
    assert pit(levels, values, -1.0, "a") == 0.2
    assert pit(levels, values, -1.0, "b") == 0.2


def test_f08_bootstrap_matches_independent_scalar_pooled_cluster_reference():
    # Deliberately unequal cluster sizes: averaging cluster means would be wrong.
    groups = [[-2.0], [0.0, 1.0], [3.0] * 7, [-0.5] * 3]
    dates = [date(2026, 12, 25), date(2026, 12, 31), date(2027, 1, 8), date(2027, 1, 15)]
    values = [value for group in groups for value in group]
    expirations = [d for group, d in zip(groups, dates, strict=True) for _ in group]
    result = cluster_bootstrap(values, expirations)
    rng = np.random.Generator(np.random.PCG64(SEED))
    stats = []
    for _ in range(10_000):
        sample = [value for i in rng.integers(0, 4, size=4) for value in groups[i]]
        stats.append(sum(sample) / len(sample))
    expected = np.percentile(stats, [2.5, 97.5])
    assert RESAMPLES == 10_000
    assert result.clusters == 4
    assert result.mean == pytest.approx(sum(values) / len(values))
    assert (result.lo, result.hi) == pytest.approx(expected)
    assert result == cluster_bootstrap(values, expirations)


def test_f08_iso_year_boundary_is_one_cluster_and_interval_is_unavailable():
    # Thursday Dec 31 and Friday Jan 1 belong to the same ISO expiration week.
    result = cluster_bootstrap([0.1, 0.2], [date(2026, 12, 31), date(2027, 1, 1)])
    assert result.clusters == 1
    assert result.lo is None and result.hi is None
    assert "insufficient clusters" in result.text()


def test_f07_header_retains_every_engine_version_commit_and_count(tmp_path):
    records = [
        recommendation(snapshot_id=str(i) * 64, code_commit=commit)
        for i, commit in [(1, "a" * 40), (2, "a" * 40), (3, "b" * 40), (4, "c" * 40)]
    ]
    last = records[-1]
    records[-1] = last.model_copy(
        update={
            "engine_version": "2",
            "trade_id": derive_trade_id(
                last.engine, "2", last.snapshot_id, last.candidate.content_hash()
            ),
        }
    )
    with Registry.create(tmp_path / "report.sqlite", Mode.PROSPECTIVE) as reg:
        for rec in records:
            reg.add_recommendation(rec)
        rendered = "\n".join(header(reg, records[-1].receipt_time, "e" * 40).lines("test"))
    assert f"MKT-1 v1 at `{'a' * 40}` (2 records)" in rendered
    assert f"MKT-1 v1 at `{'b' * 40}` (1 records)" in rendered
    assert f"MKT-1 v2 at `{'c' * 40}` (1 records)" in rendered
