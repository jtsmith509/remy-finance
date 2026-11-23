"""The R2 cut-over (DR-0048): which MKT-1/RAND-1 version runs on a decision slot.

``CUTOVER`` is the first R2 decision slot, (ET session date, session). It stays ``None``
until the cut-over DR is approved; that DR's own commit sets it, so the switch is in git
before it takes effect (EVAL-2 §4.1). Slots before it run v3 exactly as in R1; slots from
it run v4 (DR-0041 eligibility, DR-0042 ADJ-1 ranking).

Replays don't follow the date: ``remy replay run --baselines v3|v4`` names the version
(default v3), so re-running an old replay month keeps its meaning.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from remy_finance.baselines.mkt1 import MKT_1, MKT_1_V4
from remy_finance.baselines.rand1 import RAND_1, RAND_1_V4
from remy_finance.core import Session

CUTOVER: tuple[date, Session] | None = None

BASELINE_CONFIGS: dict[str, dict[str, Any]] = {
    "3": {"MKT-1": MKT_1, "RAND-1": RAND_1},
    "4": {"MKT-1": MKT_1_V4, "RAND-1": RAND_1_V4},
}
_ORDER = {Session.OPEN: 0, Session.CLOSE: 1}


def baseline_version(
    day: date,
    session: Session,
    override: str | None = None,
    cutover: tuple[date, Session] | None = None,
) -> str:
    """ "3" or "4" for a decision slot: ``override`` (replay) if given, else by the
    cut-over slot (``cutover`` defaults to ``CUTOVER``)."""
    if override is not None:
        if override not in BASELINE_CONFIGS:
            raise ValueError(f"unknown baseline version {override!r}")
        return override
    at = CUTOVER if cutover is None else cutover
    if at is None:
        return "3"
    return "4" if (day, _ORDER[session]) >= (at[0], _ORDER[at[1]]) else "3"
