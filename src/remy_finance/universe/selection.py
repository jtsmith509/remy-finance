"""Point-in-time universe selection UNIV-1 (handoff R0.5, DR-0004, DR-0023).

On the first XNYS session of each month, before the open snapshot, rank the candidate list
by average daily option volume over the 20 sessions ending the prior session, read only
through ``PointInTimeView``; take the top 50. Membership decisions are write-once records
with their inputs' hashes, and names that later delist stay in the historical record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import Field

from remy_finance.core import LookaheadError, OptionVolume, Record
from remy_finance.core.types import NonNegDec, UtcDatetime
from remy_finance.data.protocol import MarketDataSource
from remy_finance.data.timeutil import et_at, xnys
from remy_finance.data.view import PointInTimeView
from remy_finance.universe.candidates import UNIV_CANDIDATES_1, UNIV_CANDIDATES_1_VERSION


class UniverseRule(Record):
    version: str = Field(min_length=1)
    size: int = Field(ge=1)
    window_sessions: int = Field(ge=1)
    min_sessions_with_data: int = Field(ge=1)
    as_of_hhmm: tuple[int, int]  # ET wall clock on the selection session, before the open snapshot


UNIV_1 = UniverseRule(
    version="UNIV-1", size=50, window_sessions=20, min_sessions_with_data=15, as_of_hhmm=(9, 0)
)


class CandidateScore(Record):
    symbol: str
    sessions_with_data: int = Field(ge=0)
    average_contracts: NonNegDec | None  # None when ineligible for lack of data
    eligible: bool
    rank: int | None = None
    reason: str = ""


class UniverseDecision(Record):
    rule_version: str
    candidate_list_version: str
    selection_session: date
    as_of: UtcDatetime
    window_start: date
    window_end: date
    scores: tuple[CandidateScore, ...]
    members: tuple[str, ...]
    inputs_hash: str  # SHA-256 over the content hashes of every OptionVolume used

    @property
    def version_label(self) -> str:
        return f"{self.rule_version}:{self.selection_session.isoformat()}"


def first_session_of_month(year: int, month: int) -> date:
    cal = xnys()
    s = cal.date_to_session(date(year, month, 1).isoformat(), direction="next")
    return date(s.year, s.month, s.day)


def window_sessions(selection_session: date, n: int) -> list[date]:
    cal = xnys()
    prev = cal.previous_session(selection_session.isoformat())
    sessions = cal.sessions_window(prev, -n)
    return [date(s.year, s.month, s.day) for s in sessions]


def select_universe(
    source: MarketDataSource,
    selection_session: date,
    *,
    candidates: Sequence[str] = UNIV_CANDIDATES_1,
    candidate_list_version: str = UNIV_CANDIDATES_1_VERSION,
    rule: UniverseRule = UNIV_1,
) -> UniverseDecision:
    as_of = et_at(selection_session, *rule.as_of_hhmm)
    view = PointInTimeView(source, as_of)
    window = window_sessions(selection_session, rule.window_sessions)
    used: list[OptionVolume] = []
    scores: list[CandidateScore] = []
    for sym in sorted(set(candidates)):
        vols: list[OptionVolume] = []
        for d in window:
            try:
                v = view.option_volume(sym, d)
            except LookaheadError:
                raise
            except Exception:
                v = None  # vendor error for one day counts as missing data
            if v is not None:
                vols.append(v)
        used.extend(vols)
        if len(vols) < rule.min_sessions_with_data:
            scores.append(
                CandidateScore(
                    symbol=sym,
                    sessions_with_data=len(vols),
                    average_contracts=None,
                    eligible=False,
                    reason=f"only {len(vols)} of {len(window)} sessions with volume data",
                )
            )
            continue
        avg = Decimal(sum(v.contracts for v in vols)) / len(vols)
        scores.append(
            CandidateScore(
                symbol=sym, sessions_with_data=len(vols), average_contracts=avg, eligible=True
            )
        )
    eligible = sorted(
        (s for s in scores if s.eligible),
        key=lambda s: (-(s.average_contracts or Decimal(0)), s.symbol),
    )
    ranked = {s.symbol: i + 1 for i, s in enumerate(eligible)}
    members = tuple(sorted(s.symbol for s in eligible[: rule.size]))
    final_scores = tuple(
        s.model_copy(update={"rank": ranked.get(s.symbol)}) if s.eligible else s for s in scores
    )
    h = hashlib.sha256()
    for v in sorted(used, key=lambda v: v.content_hash()):
        h.update(v.content_hash().encode("ascii"))
    return UniverseDecision(
        rule_version=rule.version,
        candidate_list_version=candidate_list_version,
        selection_session=selection_session,
        as_of=as_of,
        window_start=window[0],
        window_end=window[-1],
        scores=final_scores,
        members=members,
        inputs_hash=h.hexdigest(),
    )


class UniverseStore:
    """Write-once storage of membership decisions: ``<root>/<rule>/<selection_session>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, decision: UniverseDecision) -> Path:
        path = self.root / decision.rule_version / f"{decision.selection_session.isoformat()}.json"
        if path.exists():
            raise FileExistsError(f"universe decision already stored: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as fh:
            fh.write(decision.canonical_json())
        path.chmod(0o444)
        return path

    def decisions(self, rule_version: str = UNIV_1.version) -> list[UniverseDecision]:
        folder = self.root / rule_version
        if not folder.exists():
            return []
        return [
            UniverseDecision.model_validate(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(folder.glob("*.json"))
        ]

    def membership_for(
        self, session: date, rule_version: str = UNIV_1.version
    ) -> UniverseDecision | None:
        """The decision in force on ``session``: the latest with selection_session <= session."""
        prior = [d for d in self.decisions(rule_version) if d.selection_session <= session]
        return prior[-1] if prior else None

    def membership_at(
        self, ts: datetime, rule_version: str = UNIV_1.version
    ) -> UniverseDecision | None:
        prior = [d for d in self.decisions(rule_version) if d.as_of <= ts]
        return prior[-1] if prior else None
