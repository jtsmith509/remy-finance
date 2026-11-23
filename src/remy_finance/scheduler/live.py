"""Live wiring for the prospective tick: ThetaData + Massive capture, ThetaData closes."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from remy_finance.core import MarketState, Provenance, Session
from remy_finance.data.timeutil import et_date

if TYPE_CHECKING:
    from remy_finance.history.build import BuildSummary


def live_capture(
    data_dir: Path,
) -> Callable[[Session, frozenset[str], frozenset[str]], MarketState]:
    """Capture a prospective board of the UNIV-1 membership in force plus the held names
    (DR-0039) and the tracked names (DR-0046), from the DR-0026 sources."""

    def capture(session: Session, held: frozenset[str], tracked: frozenset[str]) -> MarketState:
        from datetime import UTC, datetime

        from remy_finance.data.composite import CompositeSource
        from remy_finance.data.massive import MassiveSource
        from remy_finance.data.thetadata import ThetaDataSource
        from remy_finance.scheduler.tracked import board_plan, plan_provenance
        from remy_finance.snapshots.builder import build_prospective
        from remy_finance.universe.selection import UniverseStore

        today = et_date(datetime.now(UTC))
        decision = UniverseStore(data_dir / "universe").membership_for(today)
        if decision is None:
            raise RuntimeError(f"no UNIV-1 membership in force on {today}")
        plan = board_plan(list(decision.members), held, tracked)
        symbols = plan.symbols
        theta = ThetaDataSource()
        massive = MassiveSource(
            min_interval=float(os.environ.get("MASSIVE_MIN_INTERVAL", "12.5")),
            include_ticker_events=False,
        )
        t0 = time.perf_counter()
        massive.prefetch(symbols)
        prov = (
            Provenance(
                source="scheduler",
                details=(
                    ("command", "run tick"),
                    ("massive_prefetch_seconds", f"{time.perf_counter() - t0:.0f}"),
                ),
            ),
            *plan_provenance(plan),
        )
        return build_prospective(
            theta,
            CompositeSource(quotes=theta, events=massive),
            session,
            universe_version=decision.version_label,
            underlyings=symbols,
            provenance=prov,
        )

    return capture


def live_hist_nightly(
    data_dir: Path,
) -> Callable[[date, frozenset[str], datetime | None], BuildSummary]:
    """The nightly HIST-1 / CLOSE-1 update (DR-0046): the base names, the UNIV-1 members in
    force and the tracked names, over the last sessions. Ticker events (one Massive request
    per name) are fetched on the last session of each week only."""

    def run(day: date, tracked: frozenset[str], deadline: datetime | None = None) -> BuildSummary:
        from remy_finance.data.massive import MassiveSource
        from remy_finance.data.thetadata import ThetaDataSource
        from remy_finance.history import HistStore, hist_path
        from remy_finance.history.build import (
            ThetaMassiveHistSource,
            base_symbols,
            build,
            last_session_of_week,
            nightly_window,
        )
        from remy_finance.universe.selection import UniverseStore

        decision = UniverseStore(data_dir / "universe").membership_for(day)
        members = list(decision.members) if decision is not None else []
        names = sorted({*base_symbols(), *members, *tracked})
        massive = MassiveSource(
            min_interval=float(os.environ.get("MASSIVE_MIN_INTERVAL", "12.5")),
            include_ticker_events=last_session_of_week(day),
        )
        start, end = nightly_window(day)
        return build(
            HistStore(hist_path(data_dir)),
            ThetaMassiveHistSource(ThetaDataSource(), massive),
            names,
            start,
            end,
            workers=4,
            log=lambda _: None,
            deadline=deadline,
        )

    return run
