"""Regression tests from Codex's review of the R1 extras (docs/review/R1-extras-findings.md).

Ported from its offline probes at fc8b424 (registry). The failing probes are the regression
tests for X01-X06; the passing ones guard behaviour Codex verified.
"""

import threading
from datetime import date

from remy_finance.core import Mode
from remy_finance.registry import Registry, store
from tests.unit.registry.helpers import recommendation


def test_x_verify_locks_before_snapshot_even_before_first_export(tmp_path, monkeypatch):
    path = tmp_path / "registry.sqlite"
    out = tmp_path / "not-exported-yet"
    with Registry.create(path, Mode.PROSPECTIVE) as reg:
        reg.add_recommendation(recommendation())
        assert not out.exists()
        original_rows = reg.rows
        started, done = threading.Event(), threading.Event()
        errors = []
        interleaved = False

        def first_export():
            try:
                with Registry.open(path) as other:
                    other.add_recommendation(recommendation(engine="RAND-1"))
                    started.set()
                    other.export(out, date(2026, 11, 18))
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=first_export, daemon=True)

        def rows_with_first_export(table, after_seq=0):
            nonlocal interleaved
            rows = original_rows(table, after_seq)
            if table == "recommendations" and not interleaved:
                interleaved = True
                worker.start()
                assert started.wait(5)
                # Once fixed, the exporter can wait behind verification's lock.
                # Currently it completes because nonexistent OUT skips the lock.
                done.wait(0.2)
            return rows

        monkeypatch.setattr(reg, "rows", rows_with_first_export)
        report = reg.verify(export_dir=out)
        assert done.wait(5)
        worker.join(1)
        assert not errors
        assert report.ok, report.problems


def test_x_repointed_export_alias_keeps_io_bound_to_locked_destination(tmp_path, monkeypatch):
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    first_target.mkdir()
    second_target.mkdir()
    alias = tmp_path / "current-export"
    alias.symlink_to(first_target, target_is_directory=True)
    day = date(2026, 11, 18)
    real_replace = store._replace_atomically

    with Registry.create(tmp_path / "registry.sqlite", Mode.PROSPECTIVE) as reg:
        reg.add_recommendation(recommendation())

        def repoint_before_publish(path, text):
            monkeypatch.setattr(store, "_replace_atomically", real_replace)
            # The first exporter holds the lock for FIRST, but its actual file
            # operations still use the unresolved alias. Repointing that alias
            # makes it replace SECOND while holding the wrong destination lock.
            alias.unlink()
            alias.symlink_to(second_target, target_is_directory=True)
            with Registry.open(reg.path) as other:
                other.add_recommendation(recommendation(engine="RAND-1"))
                other.export(second_target, day)
            real_replace(path, text)

        monkeypatch.setattr(store, "_replace_atomically", repoint_before_publish)
        reg.export(alias, day)
        second_file = second_target / "prospective/2026/11/18.jsonl"
        assert len(second_file.read_text().splitlines()) == 2, (
            "the first exporter overwrote SECOND with one stale row while holding FIRST's lock"
        )
