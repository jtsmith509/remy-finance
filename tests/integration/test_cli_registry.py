"""`remy registry init|verify|export|backup` end to end on a temp data dir."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

import remy_finance.cli as cli
from remy_finance.core import Mode
from remy_finance.registry import EventKind, Registry, registry_path
from tests.unit.registry.helpers import event, recommendation

runner = CliRunner()


def test_registry_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    assert runner.invoke(cli.app, ["registry", "verify"]).exit_code == 1  # none yet
    r = runner.invoke(cli.app, ["registry", "init"])
    assert r.exit_code == 0, r.output
    assert runner.invoke(cli.app, ["registry", "init"]).exit_code != 0  # never re-created

    with Registry.open(registry_path(tmp_path, Mode.PROSPECTIVE)) as reg:
        rec = recommendation()
        reg.add_recommendation(rec)
        reg.add_event(event(rec, EventKind.ORDER_SUBMITTED))

    r = runner.invoke(cli.app, ["registry", "verify"])
    assert r.exit_code == 0 and "registry OK" in r.output, r.output

    out = tmp_path / "registry_export"
    r = runner.invoke(cli.app, ["registry", "export", "--out", str(out), "--day", "2026-11-09"])
    assert r.exit_code == 0 and "exported 2 new rows" in r.output, r.output
    assert (out / "prospective" / "2026" / "11" / "09.jsonl").exists()

    r = runner.invoke(cli.app, ["registry", "backup"])
    assert r.exit_code == 0 and "backup written" in r.output
    assert len(list((tmp_path / "backup").glob("prospective-*.sqlite"))) == 1

    # Truncate behind the triggers' back: only the export notices.
    with sqlite3.connect(registry_path(tmp_path, Mode.PROSPECTIVE)) as conn:
        conn.execute("DROP TRIGGER journal_no_delete")
        conn.execute("DELETE FROM journal")
    r = runner.invoke(cli.app, ["registry", "verify", "--export-dir", str(out)])
    assert r.exit_code == 1 and "missing or changed" in r.output
    r = runner.invoke(cli.app, ["registry", "export", "--out", str(out)])
    assert r.exit_code == 1 and "not exporting" in r.output

    assert runner.invoke(cli.app, ["registry", "verify", "--mode", "replay"]).exit_code == 1
    assert runner.invoke(cli.app, ["registry", "verify", "--mode", "bogus"]).exit_code != 0


def test_registry_migrate_backs_up_migrates_and_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remy_finance.core import Mode
    from remy_finance.registry import Registry, registry_path
    from tests.unit.registry.helpers import recommendation
    from tests.unit.registry.test_run_status import legacy_registry

    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    export = tmp_path / "export"
    monkeypatch.setenv("REMY_EXPORT_REPO", str(export))
    path = registry_path(tmp_path, Mode.PROSPECTIVE)
    path.parent.mkdir(parents=True)
    legacy_registry(path)
    with Registry.open(path) as reg:
        reg.add_recommendation(recommendation())
        reg.export(export, date(2026, 11, 24))
    result = runner.invoke(cli.app, ["registry", "migrate"])
    assert result.exit_code == 0, result.output
    assert "prospective: now REGISTRY-2; verify OK; chain heads unchanged" in result.output
    assert "exported rows checked 1" in result.output
    assert "replay: no registry" in result.output
    assert list((tmp_path / "backup").glob("prospective-*.sqlite"))
    again = runner.invoke(cli.app, ["registry", "migrate"])
    assert again.exit_code == 0 and "already REGISTRY-2" in again.output
    statuses = runner.invoke(cli.app, ["registry", "statuses"])
    assert statuses.exit_code == 0
