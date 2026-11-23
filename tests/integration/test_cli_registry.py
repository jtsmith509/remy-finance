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


def _legacy_with_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
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
    return path, export


def test_migrate_refuses_a_registry_that_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTS-04: a changed exported line: no backup, no migration, exit 1."""
    path, export = _legacy_with_export(tmp_path, monkeypatch)
    f = next(export.rglob("*.jsonl"))
    f.write_text(f.read_text().replace('"seq":1', '"seq":1 ', 1))
    r = runner.invoke(cli.app, ["registry", "migrate", "--mode", "prospective"])
    assert r.exit_code == 1 and "verification failed, not migrating" in r.output, r.output
    assert not list((tmp_path / "backup").glob("*"))
    with Registry.open(path) as reg:
        assert reg.schema == "REGISTRY-1"


def test_migrate_fails_if_a_chain_head_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTS-04: the heads-unchanged check."""
    _legacy_with_export(tmp_path, monkeypatch)
    real = Registry.migrate

    def migrate_and_append(p: Path, now: object = None) -> bool:
        out = real(p)
        with Registry.open(p) as reg:
            reg.add_recommendation(recommendation(quantity=3))
        return out

    monkeypatch.setattr(Registry, "migrate", staticmethod(migrate_and_append))
    r = runner.invoke(cli.app, ["registry", "migrate", "--mode", "prospective"])
    assert r.exit_code == 1 and "chain heads CHANGED" in r.output, r.output


def test_migrate_needs_the_export_and_a_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPS-1 and OPS-4: no silent skip of the export check, no silent success."""
    _, export = _legacy_with_export(tmp_path, monkeypatch)
    monkeypatch.delenv("REMY_EXPORT_REPO")
    r = runner.invoke(cli.app, ["registry", "migrate"])
    assert r.exit_code == 1 and "--export-dir" in r.output
    r = runner.invoke(cli.app, ["registry", "migrate", "--export-dir", str(tmp_path / "typo")])
    assert r.exit_code == 1
    r = runner.invoke(cli.app, ["registry", "migrate", "--export-dir", str(export)])
    assert r.exit_code == 0 and "exported rows checked 1" in r.output, r.output
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path / "elsewhere"))
    r = runner.invoke(cli.app, ["registry", "migrate", "--export-dir", str(export)])
    assert r.exit_code == 1 and "no prospective registry" in r.output
