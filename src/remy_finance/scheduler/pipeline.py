"""The R1 pipeline steps, shared by the prospective tick and the replay driver (DR-0033).

Every step is idempotent: re-running it after a crash, or twice, writes nothing new.

- ``recommend``: movegen, then MKT-1 and RAND-1 on a decision board. Recommendations are
  written, then submitted, and the run log is written last. A re-run recomputes the same
  picks, because the per-day cap only counts earlier snapshots and trade IDs are
  deterministic; recorded trades are skipped.
- ``fill_entries``: the paper engine fills this snapshot's new orders at the execution
  board. None (the execution slot is a gap) means unfilled, and so does a recommendation
  recorded after the execution quote was taken: it never fills at a price already past.
- ``manage``: ASSIGN-1, forced closes and exit-policy exits for positions opened at
  earlier snapshots.
- ``settle_expired``: EXP-1 for positions whose expiration has an official close that is
  already public.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from remy_finance.baselines.common import BaselineRun, underlyings_picked_today
from remy_finance.baselines.mkt1 import BoardScore, run_mkt1, score_board
from remy_finance.baselines.rand1 import run_rand1
from remy_finance.core import Candidate, MarketState, Mode
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.data.timeutil import et_date
from remy_finance.data.view import official_close_as_of
from remy_finance.execution.engine import PaperEngine
from remy_finance.movegen.generator import generate
from remy_finance.registry import Recommendation, Registry
from remy_finance.scheduler.runlog import RunLogStore

Baseline = Callable[..., tuple[list[Recommendation], BaselineRun]]
BASELINES: tuple[tuple[str, Baseline], ...] = (("MKT-1", run_mkt1), ("RAND-1", run_rand1))


@dataclass
class Pipeline:
    registry: Registry
    runs: RunLogStore
    code_commit: str
    clock: Callable[[], datetime]
    engine: PaperEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = PaperEngine(self.registry)

    @property
    def mode(self) -> Mode:
        return self.registry.mode

    # ------------------------------------------------------------------ steps

    def recommend(self, board: MarketState) -> list[str]:
        """Run both baselines on ``board``; return the trade IDs selected (new or not)."""
        if board.mode is not self.mode:
            raise ValueError(f"{board.mode.value} board in the {self.mode.value} pipeline")
        day = et_date(board.as_of)
        candidates: tuple[Candidate, ...] | None = None
        score: BoardScore | None = None
        list_digest: str | None = None
        selected: list[str] = []
        for name, runner in BASELINES:
            done = self.runs.find(
                self.mode, board.session, name, board.snapshot_id, day.isoformat()
            )
            if done is not None:
                selected += self.runs.load(done).selected
                continue
            if candidates is None:
                candidates, summary = generate(board)
                score = score_board(board, candidates)  # shared: MKT-1 and RAND-1 (DR-0031)
                list_digest = summary.list_hash
            excluded = underlyings_picked_today(self.registry, name, day, before=board.as_of)
            recs, log = runner(
                board,
                candidates,
                code_commit=self.code_commit,
                receipt_time=self.clock(),
                excluded_underlyings=excluded,
                score=score,
                candidates_hash=list_digest,
            )
            for rec in recs:
                if self.registry.recommendation(rec.trade_id) is None:
                    self.registry.add_recommendation(rec)
                self.engine.submit(rec.trade_id, self.clock())
            self.runs.write(self.mode, board.session, log)
            selected += log.selected
        return selected

    def fill_entries(self, decision: MarketState, execution: MarketState | None) -> int:
        n = 0
        for rec in self.registry.recommendations():
            if rec.snapshot_id != decision.snapshot_id:
                continue
            self.engine.submit(rec.trade_id, self.clock())  # no-op if already submitted
            board, why = execution, "no execution snapshot (gap)"
            if board is not None and rec.receipt_time > board.as_of:
                board, why = None, "recorded after the execution quote was taken"
            n += len(self.engine.fill_entry(rec.trade_id, board, self.clock(), why))
        return n

    def manage(self, decision: MarketState, execution: MarketState | None) -> int:
        n = 0
        today = et_date(decision.as_of)
        for rec in self.registry.recommendations():
            if rec.decision_time >= decision.as_of or rec.expiration < today:
                continue
            pos = self.engine.position(rec.trade_id)
            if not pos.is_open:
                continue
            underlying = rec.candidate.legs[0].contract.underlying
            if underlying not in decision.underlyings:
                continue  # not on this board (universe change): assessed at the next one
            n += len(self.engine.on_snapshot(rec.trade_id, decision, execution, self.clock()))
        return n

    def settle_expired(self, closes: OfficialCloseSource, as_of: datetime) -> tuple[int, int]:
        """Returns (trades settled, trades still waiting for a public official close)."""
        settled = waiting = 0
        today = et_date(as_of)
        for rec in self.registry.recommendations():
            if rec.expiration > today:
                continue
            if not self.engine.position(rec.trade_id).is_open:
                continue
            symbol = rec.candidate.legs[0].contract.underlying
            close = official_close_as_of(closes, symbol, rec.expiration, as_of)
            if close is None:
                waiting += 1
                continue
            self.engine.settle(rec.trade_id, close, self.clock())
            settled += 1
        return settled, waiting
