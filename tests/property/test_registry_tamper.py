"""Property: any one-character edit to any stored row is detected by `verify`, whether or
not the forger also recomputes that row's record hash (R1.1 tamper evidence)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from remy_finance.core import Mode
from remy_finance.core.canonical import sha256_hex
from remy_finance.registry import EventKind, Registry, registry_path
from tests.unit.registry.helpers import event, recommendation

E = EventKind


def build(root: Path) -> Registry:
    reg = Registry.create(registry_path(root, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    for engine in ("MKT-1", "RAND-1"):
        rec = recommendation(engine=engine)
        reg.add_recommendation(rec)
        for i, k in enumerate((E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXPIRATION_SETTLED)):
            reg.add_event(event(rec, k, minutes=5 + i))
    return reg


@settings(max_examples=60, deadline=None)
@given(
    table=st.sampled_from(["recommendations", "journal"]),
    row_pick=st.integers(min_value=0, max_value=10_000),
    pos_pick=st.integers(min_value=0, max_value=1_000_000),
    replacement=st.characters(codec="ascii", exclude_characters="'"),
    rehash=st.booleans(),
)
def test_any_single_character_edit_is_detected(
    table: str, row_pick: int, pos_pick: int, replacement: str, rehash: bool
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = build(Path(tmp))
        rows = reg.rows(table)  # type: ignore[arg-type]
        row = rows[row_pick % len(rows)]
        pos = pos_pick % len(row.record_json)
        if row.record_json[pos] == replacement:
            return
        edited = row.record_json[:pos] + replacement + row.record_json[pos + 1 :]
        new_hash = sha256_hex(edited) if rehash else row.record_hash
        with sqlite3.connect(reg.path, isolation_level=None) as conn:
            conn.execute(f"DROP TRIGGER {table}_no_update")
            conn.execute(
                f"UPDATE {table} SET record_json = ?, record_hash = ? WHERE seq = ?",
                (edited, new_hash, row.seq),
            )
        assert not reg.verify().ok
        reg.close()
