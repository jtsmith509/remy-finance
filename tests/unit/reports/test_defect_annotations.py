"""Known-defect annotations (EVAL-1 §1.7): append-only notes, report flags, and results
with and without the annotated trades."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import remy_finance.cli as cli
from remy_finance.core import Mode
from remy_finance.data.timeutil import et_at
from remy_finance.registry import EventKind, Registry, RegistryError, registry_path
from remy_finance.registry.notes import annotate, defect_ids, defect_note
from remy_finance.reports.outcomes import outcomes
from remy_finance.reports.render import cumulative_report, trades_report
from tests.integration.test_reports import built
from tests.unit.baselines import synthetic as S


@pytest.fixture
def data(tmp_path: Path) -> Path:
    path: Path = built.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    return path


def section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_defect_note_format_and_parsing() -> None:
    assert (
        defect_note("V04", " upper quantiles too high ") == "DEFECT V04: upper quantiles too high"
    )
    with pytest.raises(ValueError):
        defect_note("V 04", "x")
    with pytest.raises(ValueError):
        defect_note("V04", "  ")


def test_annotate_is_append_only_idempotent_and_verified(data: Path) -> None:
    now = et_at(S.EXPIRY, 19, 0)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        tid = next(o for o in outcomes(reg) if o.closed_filled).trade_id
        before = len(reg.events(tid))
        note = defect_note("V04", "forecast upper quantiles exceed the attainable maximum")
        assert annotate(reg, tid, note, now) is not None
        assert annotate(reg, tid, note, now) is None  # re-running writes nothing
        events = [e for _, e in reg.events(tid)]
        assert len(events) == before + 1 and events[-1].kind is EventKind.ANNOTATION
        assert defect_ids(events) == ("V04",)
        assert reg.verify().ok
        with pytest.raises(RegistryError):
            annotate(reg, "T-" + "0" * 24, note, now)


def test_reports_flag_defects_and_add_a_sensitivity_view(data: Path) -> None:
    now = et_at(S.EXPIRY.replace(year=2027), 19, 0)  # every trade is in the EVAL-1 cohort
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        text = cumulative_report(reg, now, "e" * 40)
        assert "no trade is annotated with one" in text
        closed = [o for o in outcomes(reg) if o.closed_filled and o.rec.engine == "MKT-1"]
        tid = closed[0].trade_id
        annotate(reg, tid, defect_note("V04", "upper tail too high"), now)
        text = cumulative_report(reg, now, "e" * 40)
        trades = trades_report(reg, now, "e" * 40)
    assert "known defect V04" in next(ln for ln in trades.splitlines() if tid in ln)
    cohort = section(text, "## Forecast scores")
    assert "Sensitivity (EVAL-1 §1.7), EVAL-1 cohort" in cohort and "V04 (1)" in cohort
    with_rows = [ln for ln in cohort.split("**Sensitivity")[0].splitlines() if "| MKT-1 v" in ln]
    without = cohort.split("**Sensitivity")[1]
    without_rows = [ln for ln in without.splitlines() if "| MKT-1 v" in ln]
    n_with = int(with_rows[0].split("|")[2])
    assert int(without_rows[0].split("|")[2]) == len(closed) - 1  # realized table, without
    assert n_with == len(closed)
    assert "Sensitivity (EVAL-1 §1.7), all closed trades" in section(text, "## Operational view")


def test_cli_annotate(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(data))
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        tids = [o.trade_id for o in outcomes(reg)][:2]
    args = ["registry", "annotate", "--mode", "replay", "--defect", "V04", "--note", "x"]
    for t in tids:
        args += ["--trade", t]
    first = CliRunner().invoke(cli.app, args)
    assert first.exit_code == 0, first.output
    assert first.output.count(": annotated") == 2
    again = CliRunner().invoke(cli.app, args)
    assert again.exit_code == 0 and again.output.count("already annotated") == 2
    bad = CliRunner().invoke(cli.app, [*args[:8], "--trade", "T-" + "0" * 24])
    assert bad.exit_code == 1 and "no such trade" in bad.output
