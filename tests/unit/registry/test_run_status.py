"""Run-status records in the registry (EVAL-2 §4.3, DR-0047): REGISTRY-2 and migration."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, RegistryError, RunStatus, RunStatusKind
from remy_finance.registry import store as S
from tests.unit.registry.helpers import event, recommendation

D = date(2026, 11, 24)
SNAP, LOG = "a" * 64, "b" * 64
AT = datetime(2026, 11, 24, 15, 1, tzinfo=UTC)


def status(kind: RunStatusKind = RunStatusKind.OK, **kw: Any) -> RunStatus:
    base: dict[str, Any] = dict(
        mode=Mode.PROSPECTIVE, session_date=D, session=Session.OPEN,
        slot_time=et_at(D, 10), engine="MKT-1", engine_version="3", status=kind,
        snapshot_id=SNAP, recorded_at=AT,
    )  # fmt: skip
    if kind is RunStatusKind.OK:
        base.update(run_log_hash=LOG, n_selected=1)
    elif kind is RunStatusKind.NULL:
        base.update(run_log_hash=LOG, reason="no candidate")
    else:
        base.update(reason="why")
    base.update(kw)
    return RunStatus(**base)


def test_status_validation() -> None:
    assert status().status_key == "2026-11-24|OPEN|MKT-1"
    with pytest.raises(ValueError):
        status(RunStatusKind.OK, n_selected=0)
    with pytest.raises(ValueError):
        status(RunStatusKind.NULL, n_selected=1)
    with pytest.raises(ValueError):
        status(RunStatusKind.OK, run_log_hash=None)
    with pytest.raises(ValueError):
        status(RunStatusKind.FAILED, reason="")
    with pytest.raises(ValueError):
        status(RunStatusKind.SKIPPED, run_log_hash=LOG)
    assert status(RunStatusKind.SKIPPED, snapshot_id=None).snapshot_id is None


def test_statuses_are_chained_first_wins_and_verified(tmp_path: Path) -> None:
    with Registry.create(tmp_path / "r.sqlite", Mode.PROSPECTIVE) as reg:
        assert reg.schema == "REGISTRY-2" and reg.has_run_status
        row = reg.add_run_status(status())
        assert row is not None and row.table == "run_status" and row.prev_chain == S.GENESIS
        assert reg.add_run_status(status(RunStatusKind.FAILED)) is None  # first wins
        reg.add_run_status(status(engine="RAND-1"))
        assert [s.engine for s in reg.run_statuses()] == ["MKT-1", "RAND-1"]
        assert reg.run_status("2026-11-24|OPEN|MKT-1") == status()
        with pytest.raises(RegistryError):
            reg.add_run_status(status(mode=Mode.REPLAY))
        report = reg.verify()
        assert report.ok, report.problems
        assert report.rows["run_status"] == 2


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE run_status SET record_json = 'x'",
        "DELETE FROM run_status",
        "INSERT OR REPLACE INTO run_status SELECT * FROM run_status",
    ],
)
def test_run_status_is_append_only(tmp_path: Path, sql: str) -> None:
    with Registry.create(tmp_path / "r.sqlite", Mode.PROSPECTIVE) as reg:
        reg.add_run_status(status())
    with sqlite3.connect(tmp_path / "r.sqlite") as c, pytest.raises(sqlite3.DatabaseError):
        c.execute(sql)


def test_export_carries_statuses_and_verify_catches_an_edit(tmp_path: Path) -> None:
    out = tmp_path / "export"
    with Registry.create(tmp_path / "r.sqlite", Mode.PROSPECTIVE) as reg:
        reg.add_run_status(status())
        rows = reg.export(out, D)
        assert [r.table for r in rows] == ["run_status"]
        path = out / "prospective" / "2026" / "11" / "24.jsonl"
        line = json.loads(path.read_text().splitlines()[0])
        assert line["status_key"] == "2026-11-24|OPEN|MKT-1" and "trade_id" not in line
        assert reg.verify(export_dir=out).ok
        assert reg.export(out, D) == []  # incremental
        line["record_json"] = line["record_json"].replace('"ok"', '"failed"')
        path.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
        assert not reg.verify(export_dir=out).ok


def legacy_registry(path: Path, mode: Mode = Mode.PROSPECTIVE) -> None:
    """A REGISTRY-1 database exactly as R1 created it."""
    conn = sqlite3.connect(path, isolation_level=None)
    meta = (
        f"INSERT INTO meta(key, value) VALUES ('mode', '{mode.value}'), ('schema', 'REGISTRY-1');"
    )
    conn.executescript("BEGIN;" + S._SCHEMA + meta + S._trigger_sql(S.TABLES_V1) + "COMMIT;")
    conn.close()


def test_a_legacy_registry_still_works_until_migrated(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite"
    legacy_registry(path)
    with Registry.open(path) as reg:
        assert reg.schema == "REGISTRY-1" and not reg.has_run_status
        reg.add_recommendation(recommendation())
        assert reg.run_statuses() == [] and reg.run_status("x") is None
        with pytest.raises(RegistryError, match="migrate"):
            reg.add_run_status(status())
        report = reg.verify()
        assert report.ok, report.problems
        assert set(report.rows) == {"recommendations", "journal"}


def test_migration_keeps_every_chain_and_export_and_adds_run_status(tmp_path: Path) -> None:
    path, out = tmp_path / "r.sqlite", tmp_path / "export"
    legacy_registry(path)
    with Registry.open(path) as reg:
        rec = recommendation()
        reg.add_recommendation(rec)
        reg.add_event(event(rec, "ORDER_SUBMITTED"))
        reg.export(out, D)
        before = reg.verify(export_dir=out)
    assert before.ok
    assert Registry.migrate(path, now=AT) is True
    assert Registry.migrate(path) is False  # already done
    with Registry.open(path) as reg:
        assert reg.schema == "REGISTRY-2"
        after = reg.verify(export_dir=out)
        assert after.ok, after.problems
        assert {t: after.heads[t] for t in before.heads} == before.heads
        assert after.exported_rows_checked == before.exported_rows_checked == 2
        reg.add_run_status(status())
        assert reg.verify(export_dir=out).ok
    with sqlite3.connect(path) as c:
        meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
        assert meta["migrated"].startswith("REGISTRY-1 to REGISTRY-2 at 2026-11-24")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE meta SET value = 'x' WHERE key = 'schema'")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("INSERT INTO meta VALUES ('extra', 'x')")


def test_a_failed_migration_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "r.sqlite"
    legacy_registry(path)
    monkeypatch.setattr(S, "_RUN_STATUS_SCHEMA", "CREATE TABLE broken (;")
    with pytest.raises(sqlite3.DatabaseError):
        Registry.migrate(path)
    with Registry.open(path) as reg:
        assert reg.schema == "REGISTRY-1" and reg.verify().ok


def test_verify_reports_a_dropped_run_status_trigger(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite"
    Registry.create(path, Mode.PROSPECTIVE).close()
    with sqlite3.connect(path) as c:
        c.execute("DROP TRIGGER run_status_no_delete")
    with Registry.open(path) as reg:
        problems = reg.verify().problems
    assert any("run_status_no_delete is missing" in p for p in problems)


def test_a_migrated_replay_registry_refuses_prospective_statuses(tmp_path: Path) -> None:
    path = tmp_path / "r.sqlite"
    legacy_registry(path, Mode.REPLAY)
    Registry.migrate(path)
    with Registry.open(path, Mode.REPLAY) as reg:
        with pytest.raises(RegistryError):
            reg.add_run_status(status())
        reg.add_run_status(status(mode=Mode.REPLAY))
        assert reg.verify().ok
