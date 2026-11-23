"""The R1 pipeline steps, shared by the prospective tick and the replay driver (DR-0033).

Every step is idempotent: re-running it after a crash, or twice, writes nothing new.

- ``recommend``: movegen, then MKT-1 and RAND-1 on a decision board, restricted to its
  universe members: names on the board only because a position is open in them (DR-0039)
  are never recommended. Recommendations are
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

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from remy_finance.baselines.common import BaselineRun, underlying_of, underlyings_picked_today
from remy_finance.baselines.mkt1 import BoardScore, Mkt1Config, run_mkt1, score_board
from remy_finance.baselines.rand1 import run_rand1
from remy_finance.core import Candidate, MarketState, Mode, Session
from remy_finance.core.canonical import sha256_hex
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.data.timeutil import et_date
from remy_finance.data.view import official_close_as_of
from remy_finance.execution.engine import PaperEngine
from remy_finance.movegen.generator import generate, list_hash
from remy_finance.registry import Recommendation, Registry, RunStatus, RunStatusKind, status_key
from remy_finance.scheduler.cutover import BASELINE_CONFIGS, baseline_version
from remy_finance.scheduler.held import held_underlyings
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.scheduler.tracked import not_recommendable
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time

Baseline = Callable[..., tuple[list[Recommendation], BaselineRun]]
BASELINES: tuple[tuple[str, Baseline], ...] = (("MKT-1", run_mkt1), ("RAND-1", run_rand1))


class VersionMismatchError(RuntimeError):
    """A stored run log belongs to another MKT-1/RAND-1 version than the slot's (DR-0048)."""


STATUS_REPAIR_REASON = "no run log and no status recorded (the run was interrupted)"


@dataclass
class _Shared:
    """Work shared by the engines on one board: movegen, scoring, or movegen's error."""

    configs: dict[str, Any]
    candidates: tuple[Candidate, ...] | None = None
    digest: str | None = None
    score: BoardScore | None = None
    error: Exception | None = None


@dataclass
class Pipeline:
    registry: Registry
    runs: RunLogStore
    code_commit: str
    clock: Callable[[], datetime]
    engine: PaperEngine = field(init=False)
    timings: list[str] = field(default_factory=list)  # per decision board, for the logs
    # Engines not scheduled, with the reason (EVAL-2 §4.3 not_scheduled); none in R1.
    disabled: dict[str, str] = field(default_factory=dict)
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    # Replay is resumable and re-runs a failed engine, so it records no FAILED status and
    # stops instead (DR-0047); the prospective scheduler never retries a slot.
    record_failures: bool = True
    # The MKT-1/RAND-1 version ("3" or "4"): None follows the cut-over slot (prospective);
    # a replay names it (DR-0048).
    baselines: str | None = None

    def __post_init__(self) -> None:
        self.engine = PaperEngine(self.registry)

    @property
    def mode(self) -> Mode:
        return self.registry.mode

    # ------------------------------------------------------------------ steps

    def recommend(self, board: MarketState) -> list[str]:
        """Run every baseline on ``board``; return the trade IDs selected (new or not).

        Each engine runs and records its run status independently (EVAL-2 §4.3, DR-0047):
        one engine's failure, or a failure to write its status, never stops another.
        Failures are returned in ``self.failures`` (engine, exception) for the caller to
        record and notify."""
        if board.mode is not self.mode:
            raise ValueError(f"{board.mode.value} board in the {self.mode.value} pipeline")
        day = et_date(board.as_of)
        shared = _Shared(configs=self.configs(day, board.session))
        selected: list[str] = []
        self.failures = []
        for name, runner in BASELINES:
            if name in self.disabled:
                self._guarded(name, lambda n=name: self.record_status(  # type: ignore[misc]
                    day, board.session, n, RunStatusKind.NOT_SCHEDULED, self.disabled[n],
                    board=board))  # fmt: skip
                continue
            try:
                log = self._run_engine(name, runner, board, day, shared)
            except Exception as e:
                self.failures.append((name, e))
                if self.record_failures:
                    self._guarded(name, lambda n=name, e=e: self.record_status(  # type: ignore[misc]
                        day, board.session, n, RunStatusKind.FAILED,
                        f"{type(e).__name__}: {e}", board=board))  # fmt: skip
                continue
            selected += log.selected
            self._guarded(name, lambda log=log: self._record_run(board, log))  # type: ignore[misc]
        return selected

    def _guarded(self, engine: str, write: Callable[[], object]) -> None:
        """A status write that fails is reported as that engine's failure, never raised:
        the other engines still run (Codex-style review EVAL2-01)."""
        try:
            write()
        except Exception as e:
            self.failures.append((engine, e))

    def _run_engine(
        self, name: str, runner: Baseline, board: MarketState, day: date, shared: _Shared
    ) -> BaselineRun:
        cfg = shared.configs[name]
        done = self.runs.find(self.mode, board.session, name, board.snapshot_id, day.isoformat())
        if done is not None:  # already run: its status is recorded from the log
            log = self.runs.load(done)
            if log.engine_version != cfg.engine_version:
                raise VersionMismatchError(
                    f"{name} already ran on this board as v{log.engine_version}; this slot "
                    f"needs v{cfg.engine_version} (DR-0048): archive the replay first"
                )
            return log
        if shared.error is not None:
            raise shared.error
        if shared.candidates is None:
            try:
                shared.candidates, shared.digest, shared.score = self._candidates(
                    board, day, shared.configs["MKT-1"]
                )
            except Exception as e:  # shared: every engine on this board fails with it
                shared.error = e
                raise
        excluded = underlyings_picked_today(
            self.registry, name, day, before=board.as_of, engine_version=cfg.engine_version
        )
        recs, log = runner(
            board,
            shared.candidates,
            cfg=cfg,
            code_commit=self.code_commit,
            receipt_time=self.clock(),
            excluded_underlyings=excluded,
            score=shared.score,
            candidates_hash=shared.digest,
        )
        for rec in recs:
            if self.registry.recommendation(rec.trade_id) is None:
                self.registry.add_recommendation(rec)
            self.engine.submit(rec.trade_id, self.clock())
        self.runs.write(self.mode, board.session, log)
        return log

    def configs(self, day: date, session: Session) -> dict[str, Any]:
        """The baseline configs for a decision slot (DR-0048)."""
        return BASELINE_CONFIGS[baseline_version(day, session, self.baselines)]

    def _candidates(
        self, board: MarketState, day: date, cfg: Mkt1Config
    ) -> tuple[tuple[Candidate, ...], str, BoardScore]:
        t0 = time.perf_counter()
        candidates, summary = generate(board)
        list_digest = summary.list_hash
        # DR-0039 and DR-0046: held-only and tracked-only names are captured, marked and
        # managed, never recommended.
        held = not_recommendable(board)
        if held:
            candidates = tuple(c for c in candidates if not c.legs or underlying_of(c) not in held)
            list_digest = list_hash(candidates)
        t1 = time.perf_counter()
        score = score_board(board, candidates, cfg)  # shared: MKT-1 and RAND-1 (DR-0031)
        self.timings.append(
            f"{board.session.value} {day}: {len(candidates)} candidates, "
            f"movegen {t1 - t0:.0f}s, scoring {time.perf_counter() - t1:.0f}s"
        )
        return candidates, list_digest, score

    # ------------------------------------------------------------------ run status

    def _record_run(self, board: MarketState, log: BaselineRun) -> bool:
        kind = RunStatusKind.OK if log.selected else RunStatusKind.NULL
        return self.record_status(
            et_date(board.as_of),
            board.session,
            log.engine,
            kind,
            "" if log.selected else (log.shortfall or "no candidate passed the engine's rules"),
            board=board,
            log=log,
        )

    def record_status(
        self,
        day: date,
        session: Session,
        engine: str,
        kind: RunStatusKind,
        reason: str,
        *,
        board: MarketState | None = None,
        log: BaselineRun | None = None,
    ) -> bool:
        """Record one engine's status on one slot (EVAL-2 §4.3). The first status for a
        slot and engine wins, so a re-run never changes it. Returns True if recorded now.
        A registry still at REGISTRY-1 records nothing (``remy registry migrate``)."""
        if not self.registry.has_run_status:
            return False
        if self.registry.run_status(status_key(day, session, engine)) is not None:
            return False
        status = RunStatus(
            mode=self.mode,
            session_date=day,
            session=session,
            slot_time=snapshot_time(day, session, SnapshotKind.DECISION),
            engine=engine,
            engine_version=log.engine_version
            if log is not None
            else getattr(self.configs(day, session).get(engine), "engine_version", None),
            status=kind,
            reason=reason[:500],
            snapshot_id=None if board is None else board.snapshot_id,
            run_log_hash=None if log is None else sha256_hex(log.canonical_json()),
            n_selected=0 if log is None else len(log.selected),
            code_commit=(log.code_commit if log is not None else self.code_commit) or None,
            recorded_at=self.clock(),
        )
        return self.registry.add_run_status(status) is not None

    def record_skipped(
        self, day: date, session: Session, reason: str, board: MarketState | None = None
    ) -> int:
        """Every engine skipped on a slot (a decision gap, a dirty tree, a session
        reconciled late). An engine with a run log for the board ran (e.g. before a crash)
        and is recorded from it; engines not scheduled are recorded as such."""
        n = 0
        for name, _ in BASELINES:
            log = self._run_log(name, board)
            if log is not None:
                n += self._record_run(board, log)  # type: ignore[arg-type]
            elif name in self.disabled:
                n += self.record_status(day, session, name, RunStatusKind.NOT_SCHEDULED,
                                        self.disabled[name], board=board)  # fmt: skip
            else:
                n += self.record_status(
                    day, session, name, RunStatusKind.SKIPPED, reason, board=board
                )
        return n

    def ensure_statuses(self, board: MarketState) -> int:
        """Repair a slot whose recommendations ran but some status wasn't written (a status
        write failed, or the process died): an engine with a run log is recorded from it;
        one without is recorded as failed. Idempotent (first status wins)."""
        if not self.registry.has_run_status:
            return 0
        day = et_date(board.as_of)
        n = 0
        for name, _ in BASELINES:
            if self.registry.run_status(status_key(day, board.session, name)) is not None:
                continue
            log = self._run_log(name, board)
            if log is not None:
                n += self._record_run(board, log)
            elif name in self.disabled:
                n += self.record_status(day, board.session, name, RunStatusKind.NOT_SCHEDULED,
                                        self.disabled[name], board=board)  # fmt: skip
            else:
                n += self.record_status(day, board.session, name, RunStatusKind.FAILED,
                                        STATUS_REPAIR_REASON, board=board)  # fmt: skip
        return n

    def _run_log(self, engine: str, board: MarketState | None) -> BaselineRun | None:
        if board is None:
            return None
        found = self.runs.find(
            self.mode, board.session, engine, board.snapshot_id, et_date(board.as_of).isoformat()
        )
        return None if found is None else self.runs.load(found)

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
                # Boards carry held names (DR-0039), so this is a board built without them
                # (e.g. before DR-0039). Never silent: logged, and assessed at the next one.
                self.timings.append(
                    f"{decision.session.value} {today}: {rec.trade_id[:12]} not managed: "
                    f"{underlying} is not on the board"
                )
                continue
            n += len(self.engine.on_snapshot(rec.trade_id, decision, execution, self.clock()))
        return n

    def held(self, day: date) -> frozenset[str]:
        """Underlyings with an open position on ``day``: added to captured boards."""
        return held_underlyings(self.registry, self.engine, day)

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
