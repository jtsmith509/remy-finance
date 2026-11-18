"""Annotations for known defects (EVAL-1 §1.7).

A trade affected by a known defect stays in every analysis. It gets an ``ANNOTATION``
journal event whose note starts ``DEFECT <id>:``, and reports show results with and
without such trades as a labelled sensitivity analysis. The record itself is never edited:
the annotation is append-only, hash-chained and exported like every other event.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from remy_finance.registry.records import EventKind, JournalEvent
from remy_finance.registry.store import Registry, RegistryError

DEFECT_PREFIX = "DEFECT "
_DEFECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def defect_note(defect_id: str, text: str) -> str:
    if not _DEFECT_ID.match(defect_id):
        raise ValueError(f"defect id {defect_id!r}: letters, digits, '.', '_' or '-' only")
    text = text.strip()
    if not text:
        raise ValueError("an annotation needs a description")
    return f"{DEFECT_PREFIX}{defect_id}: {text}"


def defect_ids(events: Iterable[JournalEvent]) -> tuple[str, ...]:
    """The defects a trade is annotated with, in first-annotation order."""
    out: list[str] = []
    for ev in events:
        if ev.kind is EventKind.ANNOTATION and ev.note.startswith(DEFECT_PREFIX):
            ident = ev.note[len(DEFECT_PREFIX) :].split(":", 1)[0]
            if ident not in out:
                out.append(ident)
    return tuple(out)


def annotate(registry: Registry, trade_id: str, note: str, now: datetime) -> JournalEvent | None:
    """Append an ``ANNOTATION`` to ``trade_id``. Idempotent: if the trade already carries
    an annotation with exactly this note, nothing is written and None is returned."""
    rec = registry.recommendation(trade_id)
    if rec is None:
        raise RegistryError(f"no recommendation {trade_id} in the {registry.mode.value} registry")
    for _, ev in registry.events(trade_id):
        if ev.kind is EventKind.ANNOTATION and ev.note == note:
            return None
    event = JournalEvent(
        trade_id=trade_id,
        mode=registry.mode,
        kind=EventKind.ANNOTATION,
        event_time=now,
        recorded_at=now,
        note=note,
    )
    registry.add_event(event)
    return event
