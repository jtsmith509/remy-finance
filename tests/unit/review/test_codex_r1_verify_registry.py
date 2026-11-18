"""Regression tests from Codex's verification of the R1 fixes (docs/review/R1-fixes-findings.md).

Ported from its offline probes at aa53762 (registry). Each failing probe is the regression
test for its V-finding; the passing probes guard the fixes they verified.
"""

import json
import sqlite3
import threading
from datetime import date

import pytest

from remy_finance.core import Mode
from remy_finance.registry import EventKind, Registry, RegistryError, store
from tests.unit.registry.helpers import event, recommendation

DAY = date(2026, 11, 9)


@pytest.fixture
def reg(tmp_path):
    with Registry.create(tmp_path / "registry.sqlite", Mode.PROSPECTIVE) as registry:
        yield registry


def test_f01_same_path_export_waits_then_preserves_all_rows(reg, tmp_path, monkeypatch):
    reg.add_recommendation(recommendation())
    out = tmp_path / "export"
    real_replace = store._replace_atomically
    started, done = threading.Event(), threading.Event()
    errors = []

    def other_exporter():
        try:
            with Registry.open(reg.path) as second:
                second.add_recommendation(recommendation(engine="RAND-1"))
                started.set()
                second.export(out, DAY)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=other_exporter, daemon=True)

    def paused_replace(path, text):
        monkeypatch.setattr(store, "_replace_atomically", real_replace)
        worker.start()
        assert started.wait(5)
        assert not done.wait(0.1), "same-path exporter bypassed the lock"
        real_replace(path, text)

    monkeypatch.setattr(store, "_replace_atomically", paused_replace)
    reg.export(out, DAY)
    assert done.wait(5)
    worker.join(1)
    assert not errors
    assert len((out / "prospective/2026/11/09.jsonl").read_text().splitlines()) == 2
    assert reg.verify(export_dir=out).ok


def test_v_registry_export_alias_cannot_bypass_export_lock(reg, tmp_path, monkeypatch):
    reg.add_recommendation(recommendation())
    out = tmp_path / "export"
    out.mkdir()
    alias = tmp_path / "export-link"
    alias.symlink_to(out, target_is_directory=True)
    real_replace = store._replace_atomically
    finished = threading.Event()
    started = threading.Event()
    errors = []

    def second_exporter():
        try:
            with Registry.open(reg.path) as second:
                second.add_recommendation(recommendation(engine="RAND-1"))
                started.set()
                second.export(alias, DAY)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=second_exporter, daemon=True)
    bypassed = False

    def paused_replace(path, text):
        nonlocal bypassed
        monkeypatch.setattr(store, "_replace_atomically", real_replace)
        worker.start()
        assert started.wait(5)
        # If locks identify the same directory, B waits. If they do not, B
        # publishes two lines before A publishes its obsolete one-line snapshot.
        bypassed = finished.wait(1)
        real_replace(path, text)

    monkeypatch.setattr(store, "_replace_atomically", paused_replace)
    reg.export(out, DAY)
    assert finished.wait(5)
    worker.join(1)
    assert not errors
    lines = (out / "prospective/2026/11/09.jsonl").read_text().splitlines()
    assert not bypassed and len(lines) == 2, (
        f"symlink alias bypassed={bypassed}; final export retains only {len(lines)} rows"
    )


def test_v_verify_export_is_consistent_with_concurrent_export(reg, tmp_path, monkeypatch):
    reg.add_recommendation(recommendation())
    out = tmp_path / "export"
    reg.export(out, DAY)
    original_rows = reg.rows
    interleaved = False
    started, done = threading.Event(), threading.Event()
    errors = []

    def append_and_export():
        try:
            with Registry.open(reg.path) as other:
                other.add_recommendation(recommendation(engine="RAND-1"))
                started.set()
                other.export(out, DAY)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=append_and_export, daemon=True)

    def rows_with_new_export(table, after_seq=0):
        nonlocal interleaved
        rows = original_rows(table, after_seq)
        if table == "recommendations" and not interleaved:
            interleaved = True
            worker.start()
            assert started.wait(5)
            # A correct verifier can hold the export lock. Let it finish reading its
            # consistent old files instead of synchronously deadlocking on export.
            done.wait(0.2)
        return rows

    monkeypatch.setattr(reg, "rows", rows_with_new_export)
    report = reg.verify(export_dir=out)
    assert done.wait(5)
    worker.join(1)
    assert not errors
    assert report.ok, report.problems


def test_f02_retargeted_trigger_is_detected(reg):
    with sqlite3.connect(reg.path) as conn:
        original = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='recommendations_no_update'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER recommendations_no_update")
        conn.execute(original.replace("ON recommendations", "ON journal"))
    report = reg.verify()
    assert not report.ok
    assert any("recommendations_no_update has been altered" in p for p in report.problems)


def test_f03_returned_event_row_matches_stored_export_row(reg):
    rec = recommendation()
    reg.add_recommendation(rec)
    returned = reg.add_event(event(rec, EventKind.ORDER_SUBMITTED))
    stored = reg.rows("journal")[0]
    assert returned == stored
    assert returned.export_line() == stored.export_line()


def test_v_existing_unchanged_registry1_export_remains_verifiable(reg, tmp_path):
    rec = recommendation()
    reg.add_recommendation(rec)
    reg.add_event(event(rec, EventKind.ORDER_SUBMITTED))
    out = tmp_path / "export"
    reg.export(out, DAY)
    path = out / "prospective/2026/11/09.jsonl"
    # Exact old REGISTRY-1 export format, as previously committed and timestamped.
    old_lines = []
    for line in path.read_text().splitlines():
        data = json.loads(line)
        data.pop("kind", None)
        old_lines.append(
            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    path.write_text("\n".join(old_lines) + "\n")
    before = path.read_bytes()
    report = reg.verify(export_dir=out)
    assert report.ok, report.problems
    assert path.read_bytes() == before, "historical committed lines must not be rewritten"


def test_batch_rollback_does_not_poison_any_trade_cache(reg):
    a, b = recommendation(), recommendation(engine="RAND-1")
    reg.add_recommendation(a)
    reg.add_recommendation(b)
    with pytest.raises(RegistryError):
        reg.add_events(
            [
                event(a, EventKind.ORDER_SUBMITTED),
                event(a, EventKind.LEG_FILLED, minutes=6),
                event(b, EventKind.ORDER_SUBMITTED),
                event(b, EventKind.LEG_FILLED, minutes=6),
                event(b, EventKind.ORDER_UNFILLED, minutes=7),
            ]
        )
    assert reg.events(a.trade_id) == reg.events(b.trade_id) == []
    assert reg.rows("journal") == []
    reg.add_events(
        [
            event(a, EventKind.ORDER_SUBMITTED),
            event(b, EventKind.ORDER_SUBMITTED),
        ]
    )
    assert len(reg.events(a.trade_id)) == len(reg.events(b.trade_id)) == 1
    assert reg.verify().ok
