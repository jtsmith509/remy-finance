"""Registry store: append-only triggers, hash chain, modes, links, export, backup (R1.1)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from remy_finance.core import Mode
from remy_finance.registry import (
    GENESIS,
    EventKind,
    Recommendation,
    Registry,
    RegistryError,
    chain_hash,
    registry_path,
)
from tests.unit.registry.helpers import event, recommendation

E = EventKind
DAY = date(2026, 11, 9)


@pytest.fixture
def reg(tmp_path: Path) -> Registry:
    return Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)


def filled_trade(reg: Registry, engine: str = "MKT-1") -> Recommendation:
    rec = recommendation(engine=engine)
    reg.add_recommendation(rec)
    for i, k in enumerate((E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXPIRATION_SETTLED, E.TRADE_CLOSED)):
        reg.add_event(event(rec, k, minutes=5 + i))
    return rec


def raw(reg: Registry) -> sqlite3.Connection:
    return sqlite3.connect(reg.path, isolation_level=None)


# ------------------------------------------------------------------ immutability


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE recommendations SET record_json = '{}'",
        "DELETE FROM recommendations",
        "UPDATE journal SET kind = 'ANNOTATION'",
        "DELETE FROM journal WHERE seq = 4",
        "UPDATE meta SET value = 'REPLAY' WHERE key = 'mode'",
        "DELETE FROM meta",
    ],
)
def test_update_and_delete_abort(reg: Registry, sql: str) -> None:
    filled_trade(reg)
    with raw(reg) as conn, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(sql)
    assert reg.verify().ok


def test_rows_chain_from_genesis(reg: Registry) -> None:
    filled_trade(reg)
    rows = reg.rows("journal")
    assert rows[0].prev_chain == GENESIS
    for a, b in pairwise(rows):
        assert b.prev_chain == a.chain_hash
        assert b.chain_hash == chain_hash(a.chain_hash, b.record_json)
    report = reg.verify()
    assert report.ok, report.problems
    assert report.rows == {"recommendations": 1, "journal": 4}
    assert report.heads["journal"] == rows[-1].chain_hash


# ------------------------------------------------------------------ tamper detection


RESTORE = [
    f"CREATE TRIGGER journal_no_{op.lower()} BEFORE {op} ON journal "
    f"BEGIN SELECT RAISE(ABORT, 'registry is append-only: {op} on journal refused'); END"
    for op in ("UPDATE", "DELETE")
]


def tamper(reg: Registry, sql: str, restore: bool = True) -> list[str]:
    """Drop the triggers, run ``sql``, and (like a careful forger) put the triggers back."""
    with raw(reg) as conn:
        conn.execute("DROP TRIGGER journal_no_update")
        conn.execute("DROP TRIGGER journal_no_delete")
        conn.execute(sql)
        if restore:
            for stmt in RESTORE:
                conn.execute(stmt)
    return reg.verify().problems


def test_dropped_triggers_are_reported(reg: Registry) -> None:
    filled_trade(reg)
    problems = tamper(reg, "SELECT 1", restore=False)
    assert any("journal_no_update is missing" in p for p in problems)


def test_edited_record_detected(reg: Registry) -> None:
    filled_trade(reg)
    problems = tamper(
        reg, "UPDATE journal SET record_json = replace(record_json, 'LEG_FILLED', 'ANNOTATION')"
    )
    assert any("record_hash does not match" in p for p in problems)


def test_rehashed_edit_still_breaks_the_chain(reg: Registry) -> None:
    filled_trade(reg)
    row = reg.rows("journal")[1]
    edited = row.record_json.replace('"cash":"0"', '"cash":"1000"')
    assert edited != row.record_json
    from remy_finance.core.canonical import sha256_hex

    problems = tamper(
        reg,
        f"UPDATE journal SET record_json = '{edited}', record_hash = '{sha256_hex(edited)}' "
        f"WHERE seq = {row.seq}",
    )
    assert any("chain_hash is wrong" in p for p in problems)


def test_deleted_middle_row_detected(reg: Registry) -> None:
    filled_trade(reg)
    problems = tamper(reg, "DELETE FROM journal WHERE seq = 2")
    assert any("sequence gap" in p for p in problems)
    assert any("prev_chain" in p for p in problems)


def test_truncation_is_caught_only_by_the_export(reg: Registry, tmp_path: Path) -> None:
    filled_trade(reg)
    export_dir = tmp_path / "export"
    reg.export(export_dir, DAY)
    assert tamper(reg, "DELETE FROM journal WHERE seq = 4") == []  # a shorter valid chain
    problems = reg.verify(export_dir=export_dir).problems
    assert any("missing or changed" in p for p in problems)


# ------------------------------------------------------------------ modes and links


def test_modes_are_kept_apart(tmp_path: Path) -> None:
    pros = registry_path(tmp_path, Mode.PROSPECTIVE)
    rep = registry_path(tmp_path, Mode.REPLAY)
    assert pros != rep
    with Registry.create(pros, Mode.PROSPECTIVE) as reg:
        with pytest.raises(RegistryError, match="REPLAY record refused"):
            reg.add_recommendation(recommendation(Mode.REPLAY))
        rec = recommendation()
        reg.add_recommendation(rec)
        replay_event = event(rec, E.ORDER_SUBMITTED).model_copy(update={"mode": Mode.REPLAY})
        with pytest.raises(RegistryError, match="REPLAY event refused"):
            reg.add_event(replay_event)
    with pytest.raises(RegistryError, match="is a PROSPECTIVE registry"):
        Registry.open(pros, Mode.REPLAY)
    with pytest.raises(FileExistsError):
        Registry.create(pros, Mode.PROSPECTIVE)


def test_duplicate_and_unknown_trades_refused(reg: Registry) -> None:
    rec = recommendation()
    reg.add_recommendation(rec)
    with pytest.raises(RegistryError, match="already recorded"):
        reg.add_recommendation(rec)
    other = recommendation(engine="RAND-1")
    with pytest.raises(RegistryError, match="unknown trade"):
        reg.add_event(event(other, E.ORDER_SUBMITTED))


def test_lifecycle_enforced_on_append_and_failed_append_leaves_no_row(reg: Registry) -> None:
    rec = recommendation()
    reg.add_recommendation(rec)
    with pytest.raises(RegistryError, match="LEG_FILLED not allowed"):
        reg.add_event(event(rec, E.LEG_FILLED))
    assert reg.rows("journal") == []
    reg.add_event(event(rec, E.ORDER_SUBMITTED))
    assert reg.head("journal")[0] == 1


def test_correction_links_an_event_of_the_same_trade(reg: Registry) -> None:
    a = filled_trade(reg)
    b = filled_trade(reg, engine="RAND-1")
    fix = event(a, E.CORRECTION, minutes=60, note="fill price was misread", corrects_seq=2)
    row = reg.add_event(fix)
    assert reg.events(a.trade_id)[-1] == (row.seq, fix)
    wrong = event(b, E.CORRECTION, minutes=60, note="x", corrects_seq=2)  # seq 2 is trade a's
    with pytest.raises(RegistryError, match="not an event of trade"):
        reg.add_event(wrong)
    missing = event(a, E.CORRECTION, minutes=60, note="x", corrects_seq=999)
    with pytest.raises(RegistryError, match="not an event of trade"):
        reg.add_event(missing)
    assert reg.verify().ok


def test_reopen_reads_back_identical_records(reg: Registry) -> None:
    rec = filled_trade(reg)
    reg.close()
    with Registry.open(reg.path, Mode.PROSPECTIVE) as again:
        assert again.recommendation(rec.trade_id) == rec
        assert [e.kind for _, e in again.events(rec.trade_id)][-1] is E.TRADE_CLOSED
        assert again.verify().ok


# ------------------------------------------------------------------ export and backup


def test_export_is_incremental_and_append_only(reg: Registry, tmp_path: Path) -> None:
    out = tmp_path / "registry_export"
    filled_trade(reg)
    first = reg.export(out, DAY)
    assert len(first) == 5
    path = out / "2026" / "11" / "09.jsonl"
    before = path.read_text()
    assert reg.export(out, DAY) == []
    filled_trade(reg, engine="RAND-1")
    second = reg.export(out, DAY)
    assert len(second) == 5
    after = path.read_text()
    assert after.startswith(before)
    third = reg.export(out, date(2026, 11, 10))
    assert third == []
    report = reg.verify(export_dir=out)
    assert report.ok and report.exported_rows_checked == 10


def test_backup_is_a_verifiable_copy(reg: Registry, tmp_path: Path) -> None:
    filled_trade(reg)
    dest = reg.backup(tmp_path / "backup", now=datetime(2026, 11, 9, 23, 0, tzinfo=UTC))
    assert dest.name == "prospective-20261109T230000Z.sqlite"
    with Registry.open(dest, Mode.PROSPECTIVE) as copy:
        assert copy.verify().ok
        assert copy.head("journal") == reg.head("journal")
    with pytest.raises(FileExistsError):
        reg.backup(tmp_path / "backup", now=datetime(2026, 11, 9, 23, 0, tzinfo=UTC))
