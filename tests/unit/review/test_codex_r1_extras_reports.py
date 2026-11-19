"""Regression tests from Codex's review of the R1 extras (docs/review/R1-extras-findings.md).

Ported from its offline probes at fc8b424 (reports). The failing probes are the regression
tests for X01-X06; the passing ones guard behaviour Codex verified.
"""

import math
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from remy_finance.baselines.common import BaselineRun, ExpirationFit
from remy_finance.core import Mode, OfficialClose
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.registry import Registry, registry_path
from remy_finance.reports.outcomes import outcomes
from remy_finance.reports.render import _sensitivity, cumulative_report, group_stats
from remy_finance.reports.underlying import DENSITY_FLOOR, density_at, score_fit, underlying_scores
from remy_finance.scheduler.runlog import RunLogStore
from tests.integration.test_reports import built
from tests.unit.baselines import synthetic as S


@pytest.fixture(scope="module")
def report_data(tmp_path_factory):
    return built.__wrapped__(tmp_path_factory.mktemp("reports-extras"))


def test_v05_age_eligible_trade_closed_after_cutoff_is_excluded(report_data):
    now = et_at(S.EXPIRY.replace(year=2027), 19, 0)
    with Registry.open(registry_path(report_data, Mode.REPLAY)) as reg:
        trade = next(o for o in outcomes(reg) if o.closed_filled)
    future_close = replace(trade, closed_at=now + timedelta(days=1))
    assert future_close.eval1_eligible(now.date())
    stats = group_stats(trade.engine_group, [future_close], now, now.date())
    assert stats.closed == [] and stats.scores == []


def test_underlying_null_decision_engine_is_in_report_provenance(tmp_path):
    # A fitted board need not recommend any trade. DR-0040 still scores its fit.
    run = BaselineRun(
        engine="MKT-1",
        engine_version="3",
        artifact_hash="b" * 64,
        mode=Mode.REPLAY,
        snapshot_id="a" * 64,
        decision_time=S.AS_OF,
        recorded_at=S.AS_OF + timedelta(seconds=30),
        movegen_list_hash="c" * 64,
        n_candidates=1,
        n_recommendable=0,
        n_scored=0,
        selected=(),
        shortfall="null decision",
        fits=(
            ExpirationFit(
                symbol="AAA",
                expiration=S.EXPIRY,
                ok=True,
                forward=Decimal("100"),
                rn_quantiles=(
                    (Decimal("0.1"), Decimal("0.9")),
                    (Decimal("0.5"), Decimal("1")),
                    (Decimal("0.9"), Decimal("1.1")),
                ),
            ),
        ),
    )
    closes = FixtureSource()
    closes.add(
        OfficialClose(
            symbol="AAA",
            session_date=S.EXPIRY,
            price=Decimal("100"),
            event_time=session_close(S.EXPIRY),
            public_time=et_at(S.EXPIRY, 17, 15),
            source="fixture",
        )
    )
    with Registry.create(tmp_path / "null.sqlite", Mode.REPLAY) as reg:
        text = cumulative_report(
            reg, et_at(S.EXPIRY.replace(year=2027), 19, 0), "e" * 40, (run,), closes
        )
    assert "| MKT-1 v3 | EVAL-1 cohort | 1 |" in text
    provenance = next(line for line in text.splitlines() if "**Engines (code commits):**" in line)
    assert "MKT-1 v3" in provenance, provenance


def test_known_defect_sensitivity_repeats_pnl_results(report_data):
    now = et_at(S.EXPIRY.replace(year=2027), 19, 0)
    with Registry.open(registry_path(report_data, Mode.REPLAY)) as reg:
        closed = [o for o in outcomes(reg) if o.closed_filled and o.rec.engine == "MKT-1"]
    assert len(closed) > 1
    annotated = [replace(closed[0], defects=("V04",)), *closed[1:]]
    # The main P&L/drawdown table is operational, so require its matching all-closed
    # sensitivity rather than demanding an otherwise absent cohort P&L table.
    text = "\n".join(
        _sensitivity({closed[0].engine_group: annotated}, now, None, "all closed trades")
    )
    remaining_pnl = sum((o.realized_pnl for o in annotated if not o.defects), Decimal(0))
    assert "Sensitivity (EVAL-1 §1.7)" in text
    assert f"${remaining_pnl:,.2f}" in text, "No P&L sensitivity is emitted for the retained trades"


def test_actual_baseline_grids_have_finite_scores_at_knots_and_repeated_values(report_data):
    runs = RunLogStore(report_data / "runs").all(Mode.REPLAY)
    fits = [fit for run in runs if run.engine == "MKT-1" for fit in run.fits if fit.ok]
    assert fits
    for fit in fits:
        levels = [float(t) for t, _ in fit.rn_quantiles]
        values = [float(v) for _, v in fit.rn_quantiles]
        assert values == sorted(values)
        # Test all stored knots, including the exact outer endpoints, and both tails.
        for value in [values[0] - 0.1, *values, values[-1] + 0.1]:
            c, ls = score_fit(levels, values, value)
            assert math.isfinite(c) and math.isfinite(ls)
            assert c >= 0 and density_at(levels, values, value) >= DENSITY_FLOOR
    # Ties are possible in the recorded grid.
    # Zero-width segments must not divide by zero.
    tied = density_at([0.01, 0.05, 0.5, 0.95, 0.99], [0.9, 0.9, 1.0, 1.1, 1.1], 0.9)
    assert tied == pytest.approx((0.5 - 0.05) / (1.0 - 0.9))


def test_actual_fits_score_once_per_board_and_fetch_each_close_once(report_data):
    runs = RunLogStore(report_data / "runs").all(Mode.REPLAY)
    original = next(run for run in runs if run.engine == "MKT-1")
    second = original.model_copy(
        update={
            "snapshot_id": "d" * 64,
            "decision_time": original.decision_time + timedelta(days=1),
            "recorded_at": original.recorded_at + timedelta(days=1),
        }
    )
    src = FixtureSource()
    fits = [fit for fit in original.fits if fit.ok and fit.rn_quantiles]
    for fit in fits:
        src.add(
            OfficialClose(
                symbol=fit.symbol,
                session_date=fit.expiration,
                price=fit.forward,
                event_time=session_close(fit.expiration),
                public_time=et_at(fit.expiration, 17, 15),
                source="fixture",
            )
        )

    class CountingSource:
        def __init__(self):
            self.calls = []

        def official_close(self, symbol, session_date):
            self.calls.append((symbol, session_date))
            return src.official_close(symbol, session_date)

    source = CountingSource()
    # RAND's copies are explicitly not observations under accepted DR-0040.
    rand_copy = original.model_copy(update={"engine": "RAND-1"})
    result = underlying_scores((original, second, rand_copy), source, et_at(S.EXPIRY, 19, 0))
    assert len(result.scores) == 2 * len(fits)
    assert len(source.calls) == len({(fit.symbol, fit.expiration) for fit in fits})
    assert all(item.realized == 1 for item in result.scores)


def test_schema1_run_logs_still_load_with_commit_not_recorded(report_data, tmp_path):
    # X02 fix (DR-0043): BaselineRun schema 2 adds code_commit. The live Mac holds schema-1
    # logs; they must load unchanged, and their stored bytes are never rewritten.
    import json

    from remy_finance.reports.render import header

    run = RunLogStore(report_data / "runs").all(Mode.REPLAY)[0]
    assert run.code_commit == "abcdef0123"  # new logs record the producing commit
    data = json.loads(run.canonical_json())
    assert data["$type"] == "BaselineRun@2"
    data["$type"] = "BaselineRun@1"
    del data["code_commit"]
    old = BaselineRun.model_validate(data)
    assert old.code_commit is None
    with pytest.raises(ValueError):
        BaselineRun.model_validate({**data, "$type": "BaselineRun@9"})
    with Registry.create(tmp_path / "x.sqlite", Mode.REPLAY) as reg:
        text = "\n".join(header(reg, et_at(S.EXPIRY, 19, 0), "e" * 40, (old,)).lines("t"))
    assert "MKT-1 v3 code commit not recorded (1 run logs)" in text or (
        "code commit not recorded (1 run logs)" in text
    )
