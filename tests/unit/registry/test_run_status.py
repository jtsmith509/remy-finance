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


# ------------------------------------------------- review regressions (run-status review)


def _raw(reg: Registry, key: str, text: str) -> None:
    with reg._write() as conn:  # a well-chained row that bypasses add_run_status
        S._insert(conn, "run_status", key, text)


def test_verify_checks_run_status_records(tmp_path: Path) -> None:
    """TESTS-03: the status key column, the mode, canonical JSON and validity."""
    cases = {
        "key": ("2026-11-24|OPEN|RAND-1", status().canonical_json()),
        "mode": (status().status_key, status(mode=Mode.REPLAY).canonical_json()),
        "canon": (status().status_key, status().canonical_json().replace(",", ", ", 1)),
        "invalid": (status().status_key, '{"x":1}'),
    }
    for name, (key, text) in cases.items():
        with Registry.create(tmp_path / f"{name}.sqlite", Mode.PROSPECTIVE) as reg:
            _raw(reg, key, text)
            assert not reg.verify().ok, name


def test_verify_reports_a_duplicate_status_key(tmp_path: Path) -> None:
    """INTEGRITY-2: a rebuilt table without UNIQUE and a second status for one slot."""
    path = tmp_path / "r.sqlite"
    with Registry.create(path, Mode.PROSPECTIVE) as reg:
        reg.add_run_status(status())
    c = sqlite3.connect(path, isolation_level=None)
    guards = "\n".join(
        p + " END;"
        for p in S._trigger_sql(("run_status",)).split("END;")
        if p.strip() and " ON run_status " in p
    )
    rows = c.execute("SELECT * FROM run_status").fetchall()
    c.execute("DROP TABLE run_status")
    c.execute(S._RUN_STATUS_SCHEMA.replace("TEXT NOT NULL UNIQUE", "TEXT NOT NULL"))
    c.executemany("INSERT INTO run_status VALUES (?,?,?,?,?,?)", rows)
    dup = status(RunStatusKind.FAILED).canonical_json()
    prev = rows[-1][5]
    c.execute(
        "INSERT INTO run_status VALUES (2,?,?,?,?,?)",
        (rows[0][1], dup, S.sha256_hex(dup), prev, S.chain_hash(prev, dup)),
    )
    c.executescript("BEGIN;" + guards + "COMMIT;")
    c.close()
    with Registry.open(path) as reg:
        assert any("duplicate status" in p for p in reg.verify().problems)


def test_insert_or_replace_on_an_existing_status_key_is_refused(tmp_path: Path) -> None:
    """TESTS-05: the key clause of the insert trigger, at the right next seq."""
    path = tmp_path / "r.sqlite"
    with Registry.create(path, Mode.PROSPECTIVE) as reg:
        row = reg.add_run_status(status())
    assert row is not None
    forged = status(RunStatusKind.FAILED).canonical_json()
    with sqlite3.connect(path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute(
            "INSERT OR REPLACE INTO run_status VALUES (2, ?, ?, ?, ?, ?)",
            (row.trade_id, forged, "x" * 64, row.chain_hash, S.chain_hash(row.chain_hash, forged)),
        )


def test_a_handle_opened_before_migration_follows_it(tmp_path: Path) -> None:
    """OPS-2: a long-lived handle (a replay) sees the upgrade instead of 'meta altered'."""
    path = tmp_path / "r.sqlite"
    legacy_registry(path)
    with Registry.open(path) as reg:
        assert not reg.has_run_status
        Registry.migrate(path)
        assert reg.verify().ok
        assert reg.has_run_status and reg.add_run_status(status()) is not None
        assert reg.verify().ok


def test_a_downgrade_to_registry_1_is_reported(tmp_path: Path) -> None:
    """A migrated database set back to REGISTRY-1 to escape the run_status checks."""
    path = tmp_path / "r.sqlite"
    legacy_registry(path)
    Registry.migrate(path)
    with sqlite3.connect(path, isolation_level=None) as c:
        c.executescript(
            "BEGIN; DROP TRIGGER meta_no_update;"
            "UPDATE meta SET value = 'REGISTRY-1' WHERE key = 'schema';"
            + "\n".join(
                p + " END;" for p in S._trigger_sql(()).split("END;") if "meta_no_update" in p
            )
            + "COMMIT;"
        )
    with Registry.open(path) as reg:
        assert reg.schema == "REGISTRY-1"
        assert any("downgraded" in p for p in reg.verify().problems)
