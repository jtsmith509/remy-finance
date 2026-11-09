"""Live wiring for the prospective tick: ThetaData + Massive capture, ThetaData closes."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from remy_finance.core import MarketState, Provenance, Session
from remy_finance.data.timeutil import et_date


def live_capture(data_dir: Path) -> Callable[[Session], MarketState]:
    """Capture a prospective board of the UNIV-1 membership in force (DR-0026 sources)."""

    def capture(session: Session) -> MarketState:
        from datetime import UTC, datetime

        from remy_finance.data.composite import CompositeSource
        from remy_finance.data.massive import MassiveSource
        from remy_finance.data.thetadata import ThetaDataSource
        from remy_finance.snapshots.builder import build_prospective
        from remy_finance.universe.selection import UniverseStore

        today = et_date(datetime.now(UTC))
        decision = UniverseStore(data_dir / "universe").membership_for(today)
        if decision is None:
            raise RuntimeError(f"no UNIV-1 membership in force on {today}")
        symbols = list(decision.members)
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
