"""Regression tests for Codex's R1.7 report findings (docs/review/R1.6-R1.8-findings.md).

Ported from Codex's offline reproductions at b38b9b1. F09 (EVAL-1 §2.2 scoring of the
underlying S_T/F forecasts) is deferred until after the 5-day live run.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from remy_finance.core import Mode
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, registry_path
from remy_finance.reports.bootstrap import SEED, cluster_bootstrap
from remy_finance.reports.outcomes import outcomes
from remy_finance.reports.render import cumulative_report
from remy_finance.reports.scores import pit
from tests.integration.test_reports import built
from tests.unit.baselines import synthetic as S


@pytest.fixture(scope="module")
def report_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = built.__wrapped__(tmp_path_factory.mktemp("reports-review"))  # type: ignore[attr-defined]
    return path


def section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_f06_unique_quantile_is_interpolated_without_an_invented_mass() -> None:
    for seed in ("trade-a", "trade-b", "trade-c"):
        assert pit([0.1, 0.5, 0.9], [-1.0, 0.0, 1.0], 0.0, seed) == pytest.approx(0.5)


def test_f04_cumulative_forecast_scores_apply_61_day_censoring(report_data: Path) -> None:
    now = et_at(S.EXPIRY, 19, 0)
    with Registry.open(registry_path(report_data, Mode.REPLAY)) as reg:
        closed = [o for o in outcomes(reg) if o.closed_filled]
        assert closed and all(not o.eval1_eligible(now.date()) for o in closed)
        text = cumulative_report(reg, now, "e" * 40)
    for heading in ("## Forecast scores", "## Realized versus predicted"):
        rows = [ln for ln in section(text, heading).splitlines() if ln.startswith("| MKT-1 v")]
        assert rows
        assert all(ln.split("|")[2].strip() == "0" for ln in rows), heading
    # The all-record results remain, labelled as operational, not EVAL-1.
    ops = section(text, "## Operational view")
    assert "not EVAL-1" in text.split("## Operational view", 1)[1].splitlines()[0]
    assert any(ln.startswith("| MKT-1 v") and ln.split("|")[2].strip() != "0"
               for ln in ops.splitlines())  # fmt: skip


def test_f07_cumulative_report_identifies_scored_engine_commits(report_data: Path) -> None:
    with Registry.open(registry_path(report_data, Mode.REPLAY)) as reg:
        commits = {o.rec.code_commit for o in outcomes(reg)}
        text = cumulative_report(reg, et_at(S.EXPIRY, 19, 0), "e" * 40)
    assert commits
    for commit in commits:
        assert commit in text


def test_f08_cumulative_crps_includes_required_cluster_bootstrap_interval(
    report_data: Path,
) -> None:
    with Registry.open(registry_path(report_data, Mode.REPLAY)) as reg:
        text = cumulative_report(reg, et_at(S.EXPIRY.replace(year=2027), 19, 0), "e" * 40)
    scores = section(text, "## Forecast scores")
    assert re.search(r"interval|bootstrap|\bCI\b", scores, re.IGNORECASE), scores
    # One expiration week in the fixture: the interval is explicitly not estimable.
    assert "insufficient clusters" in scores


def test_f08_bootstrap_is_deterministic_and_clustered_by_iso_week() -> None:
    import hashlib

    assert hashlib.sha256(b"EVAL-1").hexdigest()[:16] == f"{SEED:016x}"
    vals = [0.1, 0.2, 0.3, 0.9, 1.0, 1.1, 0.4]
    exps = [date(2026, 11, 13)] * 3 + [date(2026, 11, 20)] * 3 + [date(2026, 11, 27)]
    a = cluster_bootstrap(vals, exps)
    assert a == cluster_bootstrap(vals, exps)  # fixed seed
    assert a.clusters == 3 and a.lo is not None and a.hi is not None
    assert a.lo <= a.mean <= a.hi
    # Monday and Friday of one ISO week are one cluster.
    one = cluster_bootstrap([1.0, 2.0], [date(2026, 11, 9), date(2026, 11, 13)])
    assert one.clusters == 1 and one.lo is None and "insufficient clusters" in one.text()
