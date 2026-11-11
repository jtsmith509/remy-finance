"""Codex R1 review, R1.1 findings F01-F04 (regression tests; adapted from Codex's
reproductions at b38b9b1). See docs/review/R1.1-findings.md."""

from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path

import pytest

from remy_finance.core import Mode
from remy_finance.registry import EventKind, Registry, store
from tests.unit.registry.helpers import event, recommendation


@pytest.fixture
def reg(tmp_path: Path):  # type: ignore[no-untyped-def]
    with Registry.create(tmp_path / "registry.sqlite", Mode.PROSPECTIVE) as registry:
        yield registry


def test_f01_concurrent_export_cannot_erase_already_exported_rows(
    reg: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second exporter starts while the first is between reading and publishing. With
    the export lock it waits; afterwards the file holds both exports, none erased."""
    reg.add_recommendation(recommendation())
    out = tmp_path / "export"
    day = date(2026, 11, 9)
    real_replace = store._replace_atomically
    errors: list[BaseException] = []

    def second_exporter() -> None:
        try:
            with Registry.open(reg.path) as second:
                second.add_recommendation(recommendation(engine="RAND-1"))
                second.export(out, day)
        except BaseException as e:  # surfaced below
            errors.append(e)

    worker = threading.Thread(target=second_exporter)

    def delayed_replace(path: Path, text: str) -> None:
        monkeypatch.setattr(store, "_replace_atomically", real_replace)
        worker.start()  # the second exporter blocks on the export lock
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second exporter did not wait for the export lock"
        real_replace(path, text)

    monkeypatch.setattr(store, "_replace_atomically", delayed_replace)
    first = reg.export(out, day)
    worker.join(timeout=10)
    assert not errors and not worker.is_alive()
    lines = (out / "prospective" / "2026" / "11" / "09.jsonl").read_text().splitlines()
    assert len(first) == 1 and len(lines) == 2  # both recommendations, neither erased
    assert reg.verify(export_dir=out).ok


def test_f02_verify_detects_replaced_noop_trigger(reg: Registry) -> None:
    reg.add_recommendation(recommendation())
    with sqlite3.connect(reg.path) as conn:
        conn.execute("DROP TRIGGER recommendations_no_update")
        conn.execute(
            "CREATE TRIGGER recommendations_no_update BEFORE UPDATE ON recommendations "
            "BEGIN SELECT 1; END"
        )
        conn.execute("UPDATE recommendations SET record_hash = record_hash")
    report = reg.verify()
    assert not report.ok
    assert any("recommendations_no_update has been altered" in p for p in report.problems)


def test_f03_verify_and_export_detect_changed_journal_kind(reg: Registry, tmp_path: Path) -> None:
    rec = recommendation()
    reg.add_recommendation(rec)
    reg.add_event(event(rec, EventKind.ORDER_SUBMITTED))
    export_dir = tmp_path / "export"
    reg.export(export_dir, date(2026, 11, 9))
    line = (export_dir / "prospective" / "2026" / "11" / "09.jsonl").read_text().splitlines()[1]
    assert '"kind":"ORDER_SUBMITTED"' in line  # the export anchors the column too
    with sqlite3.connect(reg.path) as conn:
        original = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'journal_no_update'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER journal_no_update")
        conn.execute("UPDATE journal SET kind = 'TRADE_CLOSED' WHERE seq = 1")
        conn.execute(original)
    report = reg.verify(export_dir=export_dir)
    assert not report.ok
    assert any("kind column" in p for p in report.problems)
    assert any("missing or changed" in p for p in report.problems)


def test_f04_verify_uses_one_snapshot_while_another_writer_appends(
    reg: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg.add_recommendation(recommendation())
    original_rows = reg.rows
    interleaved = False

    def rows_with_concurrent_commit(table: store.Table, after_seq: int = 0) -> list[store.Row]:
        nonlocal interleaved
        rows = original_rows(table, after_seq)
        if table == "recommendations" and not interleaved:
            interleaved = True
            with Registry.open(reg.path) as second:
                other = recommendation(engine="RAND-1")
                second.add_recommendation(other)
                second.add_event(event(other, EventKind.ORDER_SUBMITTED))
        return rows

    monkeypatch.setattr(reg, "rows", rows_with_concurrent_commit)
    report = reg.verify()
    assert report.ok, report.problems
    assert report.rows == {"recommendations": 1, "journal": 0}  # one consistent snapshot
