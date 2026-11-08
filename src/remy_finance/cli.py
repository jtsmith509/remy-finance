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

SymbolsOpt = Annotated[
    str, typer.Option(help="Comma-separated underlyings (until R0.5 supplies the universe).")
]
SessionOpt = Annotated[str, typer.Option(help="open or close")]


def _data_dir() -> Path:
    return Path(os.environ.get("REMY_DATA_DIR", "data"))


def _symbols(text: str) -> list[str]:
    syms = sorted({s.strip().upper() for s in text.split(",") if s.strip()})
    if not syms:
        raise typer.BadParameter("no symbols given")
    return syms


def _session(text: str) -> Session:
    try:
        return Session(text.upper())
    except ValueError as e:
        raise typer.BadParameter("session must be open or close") from e


def _sources(no_events: bool):  # type: ignore[no-untyped-def]
    from remy_finance.data.composite import CompositeSource
    from remy_finance.data.polygon import PolygonSource
    from remy_finance.data.thetadata import ThetaDataSource

    theta = ThetaDataSource()
    # Without Polygon, the events side is ThetaData itself, which raises: every symbol then
    # carries a quality note instead of silently having no dividends.
    events = theta if no_events else PolygonSource()
    return theta, CompositeSource(quotes=theta, events=events)


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
    symbols: SymbolsOpt,
    universe_version: Annotated[str, typer.Option()] = "MANUAL",
    no_events: Annotated[
        bool, typer.Option(help="Skip Polygon (dividends/CAs become gap notes)")
    ] = False,
    movegen: Annotated[
        bool, typer.Option(help="Also run the move generator and report counts/timing")
    ] = False,
) -> None:
    """Build and store REPLAY decision + execution snapshots for a historical session."""
    from remy_finance.snapshots.builder import build_replay
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    d, sess, syms = date.fromisoformat(date_), _session(session), _symbols(symbols)
    _, source = _sources(no_events)
    store = SnapshotStore(_data_dir() / "snapshots")
    prov = (Provenance(source="cli", details=(("command", "snapshot build"),)),)
    decision_id: str | None = None
    for kind in (SnapshotKind.DECISION, SnapshotKind.EXECUTION):
        t0 = time.perf_counter()
        state = build_replay(
            source,
            d,
            sess,
            kind=kind,
            universe_version=universe_version,
            underlyings=syms,
            provenance=prov,
        )
        built = time.perf_counter() - t0
        m = store.write(state, kind=kind, decision_snapshot_id=decision_id)
        typer.echo(
            f"{kind.value}: {m.snapshot_id[:16]} as_of {m.as_of.isoformat()} "
            f"quotes {m.n_option_quotes} "
            f"notes {m.n_quality_notes} (built in {built:.1f}s)"
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
    symbols: SymbolsOpt,
    universe_version: Annotated[str, typer.Option()] = "MANUAL",
    no_events: Annotated[
        bool, typer.Option(help="Skip Polygon (dividends/CAs become gap notes)")
    ] = False,
    with_execution: Annotated[
        bool, typer.Option(help="Wait and take the +5 min execution snapshot too")
    ] = False,
    movegen: Annotated[
        bool, typer.Option(help="Also run the move generator and report counts/timing")
    ] = False,
) -> None:
    """Take and store a live PROSPECTIVE snapshot (run at the scheduled time)."""
    from remy_finance.data.timeutil import et_date
    from remy_finance.snapshots.builder import build_prospective
    from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time
    from remy_finance.snapshots.store import SnapshotStore

    sess, syms = _session(session), _symbols(symbols)
    theta, source = _sources(no_events)
    store = SnapshotStore(_data_dir() / "snapshots")
    prov = (Provenance(source="cli", details=(("command", "snapshot take"),)),)
    decision_id: str | None = None
    kinds = (
        (SnapshotKind.DECISION, SnapshotKind.EXECUTION)
        if with_execution
        else (SnapshotKind.DECISION,)
    )
    for kind in kinds:
        if kind is SnapshotKind.EXECUTION:
            from datetime import UTC

            wait = (
                snapshot_time(et_date(datetime.now(UTC)), sess, kind) - datetime.now(UTC)
            ).total_seconds()
            if wait > 0:
                typer.echo(f"waiting {wait:.0f}s for the execution snapshot")
                time.sleep(wait)
        state = build_prospective(
            theta,
            source,
            sess,
            universe_version=universe_version,
            underlyings=syms,
            provenance=prov,
        )
        assert state.mode is Mode.PROSPECTIVE
        m = store.write(state, kind=kind, decision_snapshot_id=decision_id)
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
