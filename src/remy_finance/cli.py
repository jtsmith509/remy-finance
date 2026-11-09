"""Command-line entry point (``remy``). Commands are added stage by stage."""

from __future__ import annotations

import os
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from remy_finance import __version__
from remy_finance.core import Mode, Provenance, Session

app = typer.Typer(help="REMY Finance command-line interface.", no_args_is_help=True)
snapshot_app = typer.Typer(help="Market snapshots (R0.4).", no_args_is_help=True)
app.add_typer(snapshot_app, name="snapshot")
universe_app = typer.Typer(help="Point-in-time universe UNIV-1 (R0.5).", no_args_is_help=True)
app.add_typer(universe_app, name="universe")

SymbolsOpt = Annotated[
    str,
    typer.Option(
        help="Comma-separated underlyings; default: the stored UNIV-1 membership in force."
    ),
]
SessionOpt = Annotated[str, typer.Option(help="open or close")]


def _data_dir() -> Path:
    return Path(os.environ.get("REMY_DATA_DIR", "data"))


def _symbols(text: str, session_date: date) -> tuple[list[str], str]:
    """Explicit symbols (universe version MANUAL), else the UNIV-1 membership in force."""
    syms = sorted({s.strip().upper() for s in text.split(",") if s.strip()})
    if syms:
        return syms, "MANUAL"
    from remy_finance.universe.selection import UniverseStore

    decision = UniverseStore(_data_dir() / "universe").membership_for(session_date)
    if decision is None:
        raise typer.BadParameter(f"no --symbols and no stored universe in force on {session_date}")
    return list(decision.members), decision.version_label


def _session(text: str) -> Session:
    try:
        return Session(text.upper())
    except ValueError as e:
        raise typer.BadParameter("session must be open or close") from e


def _sources(no_events: bool, symbols: list[str]):  # type: ignore[no-untyped-def]
    """ThetaData for quotes and rates; Massive for dividends and splits (DR-0026).

    Massive's dividends and splits for all ``symbols`` are prefetched in a few batched,
    throttled requests (free plan: about 5 a minute), so building a board needs no further
    Massive calls. Per-symbol ticker events are skipped for snapshots (no batched form).
    """
    from remy_finance.data.composite import CompositeSource
    from remy_finance.data.massive import MassiveSource
    from remy_finance.data.thetadata import ThetaDataSource

    theta = ThetaDataSource()
    if no_events:
        # The events side is ThetaData itself, which raises: every symbol then carries a
        # quality note instead of silently having no dividends.
        return theta, CompositeSource(quotes=theta, events=theta)
    massive = MassiveSource(
        min_interval=float(os.environ.get("MASSIVE_MIN_INTERVAL", "12.5")),
        include_ticker_events=False,
    )
    t0 = time.perf_counter()
    massive.prefetch(symbols)
    typer.echo(
        f"prefetched Massive dividends/splits for {len(symbols)} symbols in "
        f"{massive.requests_made} requests ({time.perf_counter() - t0:.0f}s)"
    )
    return theta, CompositeSource(quotes=theta, events=massive)


def _report_movegen(state) -> None:  # type: ignore[no-untyped-def]
    from remy_finance.movegen.generator import generate

    t0 = time.perf_counter()
    cands, summary = generate(state)
    dt = time.perf_counter() - t0
    by_type = Counter(c.structure_type.value if c.structure_type else "NULL" for c in cands)
    typer.echo(
        f"movegen: {summary.n_candidates} candidates ({summary.n_priced} priced, "
        f"{summary.n_unpriceable} unpriceable, {summary.n_not_recommendable} qty 0) in {dt:.1f}s; "
        f"list_hash {summary.list_hash[:16]}"
    )
    typer.echo(f"  by type: {dict(sorted(by_type.items()))}")
    for n in summary.notes:
        typer.echo(f"  note: {n.symbol} {n.code} {n.detail}")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@snapshot_app.command("build")
def snapshot_build(
    date_: Annotated[str, typer.Option("--date", help="Session date YYYY-MM-DD")],
    session: SessionOpt,
    symbols: SymbolsOpt = "",
    no_events: Annotated[
        bool, typer.Option(help="Skip Massive (dividends/CAs become gap notes)")
    ] = False,
    movegen: Annotated[
        bool, typer.Option(help="Also run the move generator and report counts/timing")
    ] = False,
    workers: Annotated[
        int, typer.Option(help="Symbols fetched concurrently (ThetaData Standard: 4)")
    ] = 4,
) -> None:
    """Build and store REPLAY decision + execution snapshots for a historical session.

    Safe to re-run: snapshots already stored for the slot are reused, never rebuilt."""
    from remy_finance.snapshots.builder import build_replay
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    d, sess = date.fromisoformat(date_), _session(session)
    syms, universe_version = _symbols(symbols, d)
    _, source = _sources(no_events, syms)
    store = SnapshotStore(_data_dir() / "snapshots")
    prov = (Provenance(source="cli", details=(("command", "snapshot build"),)),)
    decision_id: str | None = None
    for kind in (SnapshotKind.DECISION, SnapshotKind.EXECUTION):
        existing = store.find(Mode.REPLAY, d, sess, kind)
        if existing is not None:  # resume: a stored snapshot is final, never rebuilt
            m = store.load_manifest(existing)
            typer.echo(f"{kind.value}: already stored {m.snapshot_id[:16]} ({existing}); skipping")
            if kind is SnapshotKind.DECISION:
                decision_id = m.snapshot_id
                if movegen:
                    _report_movegen(store.load(existing))
            continue
        typer.echo(f"{kind.value}: building {len(syms)} symbols with {workers} workers ...")
        t0 = time.perf_counter()

        def progress(sym: str, done: int, total: int, n: int, secs: float, t0: float = t0) -> None:
            elapsed = time.perf_counter() - t0
            eta = elapsed / done * (total - done)
            typer.echo(
                f"  [{done:>2}/{total}] {sym:<6} {n:>6} quotes {secs:5.1f}s  "
                f"elapsed {elapsed / 60:4.1f}m  eta {eta / 60:4.1f}m"
            )

        state = build_replay(
            source,
            d,
            sess,
            kind=kind,
            universe_version=universe_version,
            underlyings=syms,
            provenance=prov,
            max_workers=workers,
            progress=progress,
        )
        built = time.perf_counter() - t0
        m = store.write(state, kind=kind, decision_snapshot_id=decision_id)
        typer.echo(
            f"{kind.value}: {m.snapshot_id[:16]} as_of {m.as_of.isoformat()} "
            f"quotes {m.n_option_quotes} notes {m.n_quality_notes} (built in {built:.1f}s)"
        )
        for n in state.quality_notes:
            typer.echo(f"  note: {n.scope} {n.code} {n.detail[:120]}")
        if kind is SnapshotKind.DECISION:
            decision_id = m.snapshot_id
            if movegen:
                _report_movegen(state)


@snapshot_app.command("take")
def snapshot_take(
    session: SessionOpt,
    symbols: SymbolsOpt = "",
    no_events: Annotated[
        bool, typer.Option(help="Skip Massive (dividends/CAs become gap notes)")
    ] = False,
    with_execution: Annotated[
        bool, typer.Option(help="Wait and take the +5 min execution snapshot too")
    ] = False,
    movegen: Annotated[
        bool, typer.Option(help="Also run the move generator and report counts/timing")
    ] = False,
    wait: Annotated[
        bool, typer.Option(help="If started early (up to 3 h), wait for the scheduled time")
    ] = False,
) -> None:
    """Take and store a live PROSPECTIVE snapshot within 10 minutes of its scheduled time."""
    from datetime import UTC, timedelta

    from remy_finance.data.timeutil import NEW_YORK, et_date
    from remy_finance.snapshots.builder import build_prospective
    from remy_finance.snapshots.schedule import (
        OutsideWindowError,
        SnapshotKind,
        prospective_window,
        snapshot_time,
    )
    from remy_finance.snapshots.store import (
        GapRecordedError,
        LateSnapshotError,
        SnapshotExistsError,
        SnapshotStore,
    )

    sess = _session(session)
    now = datetime.now(UTC)
    try:
        start, end = prospective_window(now, sess)
    except OutsideWindowError as e:
        typer.echo(f"not taking a snapshot: {e}")
        raise typer.Exit(2) from None
    window = f"{start.astimezone(NEW_YORK):%H:%M}-{end.astimezone(NEW_YORK):%H:%M} ET"
    today = et_date(now)
    store = SnapshotStore(_data_dir() / "snapshots")
    kinds = (
        (SnapshotKind.DECISION, SnapshotKind.EXECUTION)
        if with_execution
        else (SnapshotKind.DECISION,)
    )

    def miss(remaining: tuple[SnapshotKind, ...], reason: str) -> None:
        """Record each missed slot as a gap (DR-0022/0024); never over a stored snapshot."""
        for k in remaining:
            try:
                gap = store.record_gap(today, sess, k, reason[:300])
                typer.echo(f"{k.value}: gap recorded ({gap.reason})")
            except SnapshotExistsError:
                typer.echo(f"{k.value}: already stored; no gap recorded")

    if now > end:
        started = f"{now.astimezone(NEW_YORK):%H:%M:%S} ET"
        typer.echo(f"not taking a snapshot: too late, the {sess.value} window today was {window}")
        miss(kinds, f"missed: take started at {started}, after the {window} window")
        raise typer.Exit(2)
    if now < start:
        delay = (start - now).total_seconds()
        if not wait or delay > timedelta(hours=3).total_seconds():
            typer.echo(f"not taking a snapshot: too early, the window is {window} (use --wait)")
            raise typer.Exit(2)
    syms, universe_version = _symbols(symbols, today)
    theta, source = _sources(no_events, syms)  # prefetch before waiting for the window
    if now < start:
        delay = (start - datetime.now(UTC)).total_seconds()
        if delay > 0:
            typer.echo(f"waiting {delay / 60:.1f} min for the {window} window")
            time.sleep(delay)
    prov = (Provenance(source="cli", details=(("command", "snapshot take"),)),)
    decision_id: str | None = None
    for i, kind in enumerate(kinds):
        existing = store.find(Mode.PROSPECTIVE, today, sess, kind)
        if existing is not None:  # re-run: a stored snapshot is final
            m = store.load_manifest(existing)
            typer.echo(f"{kind.value}: already stored {m.snapshot_id[:16]}; skipping")
            if kind is SnapshotKind.DECISION:
                decision_id = m.snapshot_id
            continue
        if store.has_gap(today, sess, kind):
            typer.echo(f"{kind.value}: this slot is already recorded as a gap")
            miss(kinds[i + 1 :], f"missed: the {kinds[i].value} slot is a gap")
            raise typer.Exit(2)
        if kind is SnapshotKind.EXECUTION:
            exec_delay = (snapshot_time(today, sess, kind) - datetime.now(UTC)).total_seconds()
            if exec_delay > 0:
                typer.echo(f"waiting {exec_delay:.0f}s for the execution snapshot")
                time.sleep(exec_delay)
        try:
            state = build_prospective(
                theta,
                source,
                sess,
                universe_version=universe_version,
                underlyings=syms,
                provenance=prov,
            )
            m = store.write(state, kind=kind, decision_snapshot_id=decision_id)
        except LateSnapshotError as e:
            typer.echo(f"{kind.value}: capture overran its window: {e}")
            miss(kinds[i:], f"missed: capture overran the window ({e})")
            raise typer.Exit(2) from None
        except GapRecordedError:
            typer.echo(f"{kind.value}: a gap was recorded for this slot while capturing")
            miss(kinds[i + 1 :], f"missed: the {kind.value} slot is a gap")
            raise typer.Exit(2) from None
        except Exception as e:  # the slot is missed whatever the cause; then surface the error
            miss(kinds[i:], f"missed: capture failed: {type(e).__name__}: {e}")
            raise
        typer.echo(
            f"{kind.value}: {m.snapshot_id[:16]} as_of {m.as_of.isoformat()} "
            f"quotes {m.n_option_quotes} "
            f"notes {m.n_quality_notes}"
        )
        for n in state.quality_notes:
            typer.echo(f"  note: {n.scope} {n.code} {n.detail[:120]}")
        if kind is SnapshotKind.DECISION:
            decision_id = m.snapshot_id
            if movegen:
                _report_movegen(state)


@universe_app.command("build")
def universe_build(
    month: Annotated[str, typer.Option(help="YYYY-MM: selects on the month's first XNYS session")],
    workers: Annotated[
        int, typer.Option(help="Symbols fetched concurrently (ThetaData Standard: 4)")
    ] = 4,
) -> None:
    """Select and store the UNIV-1 membership for a month (point-in-time, write-once)."""
    from remy_finance.data.thetadata import ThetaDataSource
    from remy_finance.universe.selection import (
        UniverseStore,
        first_session_of_month,
        select_universe,
    )

    y, m = (int(x) for x in month.split("-"))
    session = first_session_of_month(y, m)
    t0 = time.perf_counter()
    typer.echo(f"selecting UNIV-1 for {session} with {workers} workers ...")

    def progress(sym: str, done: int, total: int, n: int, window: int, secs: float) -> None:
        elapsed = time.perf_counter() - t0
        eta = elapsed / done * (total - done)
        typer.echo(
            f"[{done:>2}/{total}] {sym:<6} {n:>2}/{window} sessions  {secs:5.1f}s  "
            f"elapsed {elapsed / 60:4.1f}m  eta {eta / 60:4.1f}m"
        )

    decision = select_universe(ThetaDataSource(), session, max_workers=workers, progress=progress)
    path = UniverseStore(_data_dir() / "universe").write(decision)
    typer.echo(
        f"{decision.version_label}: {len(decision.members)} members, window "
        f"{decision.window_start}..{decision.window_end}, inputs {decision.inputs_hash[:16]} "
        f"({time.perf_counter() - t0:.0f}s) -> {path}"
    )
    for s in sorted(decision.scores, key=lambda s: (s.rank is None, s.rank or 0, s.symbol)):
        avg = "-" if s.average_contracts is None else f"{s.average_contracts:,.0f}"
        mark = "*" if s.symbol in decision.members else " "
        typer.echo(
            f" {mark} {s.rank or '-':>3} {s.symbol:<6} avg {avg:>12}  "
            f"days {s.sessions_with_data} {s.reason}"
        )


@snapshot_app.command("verify")
def snapshot_verify() -> None:
    """Re-verify every stored snapshot (file hashes and snapshot ids)."""
    from remy_finance.snapshots.store import SnapshotStore

    store = SnapshotStore(_data_dir() / "snapshots")
    n = bad = 0
    for manifest in sorted(store.root.glob("*/*/*/manifest.json")):
        n += 1
        try:
            store.load(manifest.parent)
        except Exception as e:
            bad += 1
            typer.echo(f"FAIL {manifest.parent}: {e}")
    typer.echo(f"verified {n} snapshots, {bad} failed; gaps recorded: {len(store.gaps())}")
    raise typer.Exit(1 if bad else 0)


if __name__ == "__main__":
    app()
