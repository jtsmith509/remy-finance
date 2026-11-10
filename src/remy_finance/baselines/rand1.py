"""Seeded random baseline RAND-1 (handoff R1.5, DR-0031).

Uniformly samples K priceable, recommendable candidates per snapshot, subject to the same
SELECT-1 caps as MKT-1 (K = 5, one per underlying per baseline per day).

- **Seed:** ``sha256("<snapshot_id>\\nRAND-1")``, recorded on every recommendation and in
  the run log. Same inputs give the same picks.
- **Sampling:** each eligible candidate gets the key ``sha256(seed + "\\n" + candidate
  hash)``; candidates are taken in key order, skipping capped underlyings. Ordering by a
  cryptographic hash is a uniformly random permutation, and unlike a library PRNG its
  output can never change with the Python version.
- **Forecasts:** the predicted distribution of each pick is MKT-1's market-implied one, so
  RAND-1 differs from MKT-1 only in *selection*. The eligible set is therefore the
  candidates MKT-1 could score (a fitted expiration and no bug signal).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime

from pydantic import Field

from remy_finance.baselines.common import (
    SELECT_1,
    BaselineRun,
    SelectionRule,
    apply_caps,
    build_recommendation,
)
from remy_finance.baselines.mkt1 import (
    MKT_1,
    BoardScore,
    Mkt1Config,
    Scored,
    score_board,
    shortfall_note,
)
from remy_finance.core import Candidate, MarketState, Record
from remy_finance.movegen.generator import list_hash
from remy_finance.registry import ADJ_0, Recommendation


class Rand1Config(Record):
    version: str = "RAND-1"
    engine_version: str = "2"  # forecasts from MKT-1 v2 (DR-0037)
    selection: SelectionRule = SELECT_1
    forecast_model: Mkt1Config = Field(default=MKT_1)


RAND_1 = Rand1Config()


def seed_for(snapshot_id: str, version: str = "RAND-1") -> str:
    return hashlib.sha256(f"{snapshot_id}\n{version}".encode()).hexdigest()


def sample_key(seed: str, candidate_hash: str) -> str:
    return hashlib.sha256(f"{seed}\n{candidate_hash}".encode()).hexdigest()


def run_rand1(
    board: MarketState,
    candidates: Sequence[Candidate],
    *,
    code_commit: str,
    receipt_time: datetime,
    excluded_underlyings: frozenset[str] = frozenset(),
    cfg: Rand1Config = RAND_1,
    score: BoardScore | None = None,
    candidates_hash: str | None = None,
) -> tuple[list[Recommendation], BaselineRun]:
    if score is None or score.cfg != cfg.forecast_model:
        score = score_board(board, candidates, cfg.forecast_model)
    seed = seed_for(board.snapshot_id, cfg.version)
    keyed = sorted(
        ((sample_key(seed, s.candidate.content_hash()), s) for s in score.scored),
        key=lambda kv: kv[0],
    )
    by_id: dict[int, Scored] = {id(s.candidate): s for _, s in keyed}
    picks = apply_caps((s.candidate for _, s in keyed), cfg.selection, excluded_underlyings)
    artifact = cfg.content_hash()
    recs: list[Recommendation] = []
    for rank, c in enumerate(picks, start=1):
        forecast = score.forecast(by_id[id(c)])
        recs.append(
            build_recommendation(
                candidate=c,
                board=board,
                engine=cfg.version,
                engine_version=cfg.engine_version,
                code_commit=code_commit,
                artifact_hash=artifact,
                receipt_time=receipt_time,
                rank=rank,  # draw order, not a preference
                method="RAND-1 selection; MKT-1 market-implied forecast",
                forecast=forecast,
                search_config=(
                    ("ranking_adjustment", ADJ_0),
                    ("selection", cfg.selection.version),
                    ("sampling", "sha256(seed, candidate hash) order"),
                    ("forecast_model", f"{cfg.forecast_model.version}@{score.cfg.content_hash()}"),
                ),
                random_seed=seed,
            )
        )
    run = BaselineRun(
        engine=cfg.version,
        engine_version=cfg.engine_version,
        artifact_hash=artifact,
        mode=board.mode,
        snapshot_id=board.snapshot_id,
        decision_time=board.as_of,
        recorded_at=receipt_time,
        movegen_list_hash=candidates_hash or list_hash(tuple(candidates)),
        n_candidates=len(candidates),
        n_recommendable=len(score.usable),
        n_scored=len(score.scored),
        excluded_underlyings=tuple(sorted(excluded_underlyings)),
        selected=tuple(r.trade_id for r in recs),
        shortfall=shortfall_note(len(recs), cfg.selection.k, score, excluded_underlyings),
        fits=score.fits,
        bug_signals=score.bug_signals,
        random_seed=seed,
        flags=("AMERICAN_EXERCISE_PREMIUM_IGNORED",),
    )
    return recs, run
