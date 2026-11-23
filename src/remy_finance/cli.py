"""Command-line entry point (``remy``). Commands are added stage by stage."""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from remy_finance import __version__
from remy_finance.core import MarketState, Mode, Provenance, Session

if TYPE_CHECKING:
    from remy_finance.history.closes import StoreFirstCloses

app = typer.Typer(help="REMY Finance command-line interface.", no_args_is_help=True)
snapshot_app = typer.Typer(help="Market snapshots (R0.4).", no_args_is_help=True)
app.add_typer(snapshot_app, name="snapshot")
universe_app = typer.Typer(help="Point-in-time universe UNIV-1 (R0.5).", no_args_is_help=True)
app.add_typer(universe_app, name="universe")
registry_app = typer.Typer(help="Paper-trade registry (R1.1).", no_args_is_help=True)
app.add_typer(registry_app, name="registry")
run_app = typer.Typer(help="Scheduled jobs (R1.6).", no_args_is_help=True)
app.add_typer(run_app, name="run")
scheduler_app = typer.Typer(help="launchd set-up (R1.6).", no_args_is_help=True)
app.add_typer(scheduler_app, name="scheduler")
report_app = typer.Typer(
    help="Reports by baseline; one mode at a time (R1.7).", no_args_is_help=True
)
app.add_typer(report_app, name="report")
replay_app = typer.Typer(help="Replay the R1 pipeline over history (R1.8).", no_args_is_help=True)
app.add_typer(replay_app, name="replay")
hist_app = typer.Typer(help="HIST-1 history store (R2, DR-0045).", no_args_is_help=True)
app.add_typer(hist_app, name="hist")

REPO_DIR = Path(__file__).resolve().parents[2]

SymbolsOpt = Annotated[
    str,
    typer.Option(
        help="Comma-separated underlyings; default: the stored UNIV-1 membership in force."
    ),
]
SessionOpt = Annotated[str, typer.Option(help="open or close")]


def _data_dir() -> Path:
    return Path(os.environ.get("REMY_DATA_DIR", "data"))


def _close1() -> StoreFirstCloses:
    """CLOSE-1 (DR-0046): official closes from HIST-1 first; a gap is fetched from
    ThetaData and stored, write-once, once public. The store is opened on the first read,
    so in the tick a bad store fails only the step that reads it."""
    from remy_finance.data.thetadata import ThetaDataSource
    from remy_finance.history import HistStore, hist_path
    from remy_finance.history.closes import StoreFirstCloses

    data = _data_dir()
    return StoreFirstCloses(lambda: HistStore(hist_path(data)), ThetaDataSource())


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


ModeOpt = Annotated[str, typer.Option(help="prospective or replay")]


def _mode(text: str) -> Mode:
    try:
        return Mode(text.upper())
    except ValueError as e:
        raise typer.BadParameter("mode must be prospective or replay") from e


def _open_registry(mode_text: str):  # type: ignore[no-untyped-def]
    from remy_finance.registry import Registry, registry_path

    mode = _mode(mode_text)
    path = registry_path(_data_dir(), mode)
    if not path.exists():
        typer.echo(f"no {mode.value} registry at {path} (create it with `remy registry init`)")
        raise typer.Exit(1)
    return Registry.open(path, mode)


@registry_app.command("init")
def registry_init(mode: ModeOpt = "prospective") -> None:
    """Create an empty registry database for one mode."""
    from remy_finance.registry import Registry, registry_path

    m = _mode(mode)
    path = registry_path(_data_dir(), m)
    Registry.create(path, m).close()
    typer.echo(f"created {m.value} registry at {path}")


@registry_app.command("verify")
def registry_verify(
    mode: ModeOpt = "prospective",
    export_dir: Annotated[
        Path | None, typer.Option(help="Also check rows exported here are unchanged.")
    ] = None,
) -> None:
    """Recompute the hash chains and re-check every record (exit 1 on any problem)."""
    with _open_registry(mode) as reg:
        report = reg.verify(export_dir=export_dir)
    for table, n in report.rows.items():
        typer.echo(f"{table}: {n} rows, head {report.heads[table][:16]}")
    if export_dir is not None:
        typer.echo(f"exported rows checked: {report.exported_rows_checked}")
    for p in report.problems:
        typer.echo(f"FAIL {p}")
    typer.echo("registry OK" if report.ok else f"registry FAILED ({len(report.problems)})")
    raise typer.Exit(0 if report.ok else 1)


@registry_app.command("export")
def registry_export(
    out: Annotated[Path, typer.Option(help="Export directory (registry-export checkout).")],
    mode: ModeOpt = "prospective",
    day: Annotated[str, typer.Option(help="YYYY-MM-DD; default today (ET)")] = "",
) -> None:
    """Append rows not yet exported to OUT/YYYY/MM/DD.jsonl (verifies first)."""
    from remy_finance.data.timeutil import et_date

    when = date.fromisoformat(day) if day else et_date(datetime.now().astimezone())
    with _open_registry(mode) as reg:
        report = reg.verify(export_dir=out if out.exists() else None)
        if not report.ok:
            for p in report.problems:
                typer.echo(f"FAIL {p}")
            typer.echo("not exporting: registry verification failed")
            raise typer.Exit(1)
        rows = reg.export(out, when)
    typer.echo(f"exported {len(rows)} new rows for {when}")


@registry_app.command("annotate")
def registry_annotate(
    trade: Annotated[list[str], typer.Option(help="Trade ID; repeat for several trades.")],
    defect: Annotated[str, typer.Option(help="Defect ID, e.g. V04")],
    note: Annotated[str, typer.Option(help="What the defect is and how it affects the trade")],
    mode: ModeOpt = "prospective",
) -> None:
    """Annotate trades affected by a known defect (EVAL-1 §1.7). Append-only: the trade
    stays in every analysis, and reports add results without it. Re-running is a no-op."""
    from datetime import UTC

    from remy_finance.registry.notes import annotate, defect_note

    text = defect_note(defect, note)
    now = datetime.now(UTC)
    with _open_registry(mode) as reg:
        missing = [t for t in trade if reg.recommendation(t) is None]
        if missing:
            typer.echo(f"no such trade(s) in the {mode} registry: {', '.join(missing)}")
            raise typer.Exit(1)
        for t in trade:
            ev = annotate(reg, t, text, now)
            typer.echo(f"{t}: {'annotated' if ev else 'already annotated (nothing written)'}")


@registry_app.command("migrate")
def registry_migrate(
    mode: Annotated[str, typer.Option(help="prospective, replay or both")] = "both",
) -> None:
    """Upgrade REGISTRY-1 databases to REGISTRY-2, adding the run-status table (DR-0047).

    Holds the scheduler's tick lock throughout, verifies first (with the export for the
    prospective registry), takes a backup, migrates in one transaction, then verifies
    again and checks that the recommendation and journal chain heads are unchanged."""
    import fcntl

    from remy_finance.registry import Registry, registry_path

    data = _data_dir()
    modes = [Mode.PROSPECTIVE, Mode.REPLAY] if mode == "both" else [_mode(mode)]
    lock = data / "scheduler" / "tick.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    failed = False
    with lock.open("a") as fh:
        typer.echo("waiting for the scheduler tick lock ...")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        for m in modes:
            path = registry_path(data, m)
            if not path.exists():
                typer.echo(f"{m.value.lower()}: no registry at {path}; nothing to migrate")
                continue
            export = _export_repo() if m is Mode.PROSPECTIVE else None
            export = export if export is not None and export.exists() else None
            with Registry.open(path, m) as reg:
                before = reg.verify(export_dir=export)
                if not before.ok:
                    typer.echo(f"{m.value.lower()}: verification failed, not migrating:")
                    for problem in before.problems[:10]:
                        typer.echo(f"  FAIL {problem}")
                    failed = True
                    continue
                if reg.has_run_status:
                    typer.echo(f"{m.value.lower()}: already {reg.schema}")
                    continue
                dest = reg.backup(data / "backup")
            typer.echo(f"{m.value.lower()}: backup written: {dest}")
            Registry.migrate(path)
            with Registry.open(path, m) as reg:
                after = reg.verify(export_dir=export)
                same = all(after.heads[t] == before.heads[t] for t in before.heads)
                verdict = "OK" if after.ok else "FAILED"
                typer.echo(
                    f"{m.value.lower()}: now {reg.schema}; verify {verdict}; "
                    f"chain heads {'unchanged' if same else 'CHANGED'}; "
                    f"exported rows checked {after.exported_rows_checked}"
                )
                for problem in after.problems[:10]:
                    typer.echo(f"  FAIL {problem}")
                failed |= not (after.ok and same)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    raise typer.Exit(1 if failed else 0)


@registry_app.command("statuses")
def registry_statuses(
    mode: ModeOpt = "prospective",
    last: Annotated[int, typer.Option(help="Also list the last N slots")] = 6,
) -> None:
    """Run-status records (EVAL-2 §4.3, DR-0047): counts by engine and status, and the
    latest slots."""
    with _open_registry(mode) as reg:
        if not reg.has_run_status:
            typer.echo(f"{reg.schema}: no run-status table yet (run `remy registry migrate`)")
            raise typer.Exit(1)
        statuses = reg.run_statuses()
    counts = Counter((s.engine, s.status.value) for s in statuses)
    for (engine, status), n in sorted(counts.items()):
        typer.echo(f"{engine:8} {status:14} {n}")
    slots = sorted({(s.session_date, s.session.value) for s in statuses})[-last:]
    for day, session in slots:
        row = [s for s in statuses if (s.session_date, s.session.value) == (day, session)]
        text = "; ".join(
            f"{s.engine} {s.status.value}" + (f" ({s.reason[:60]})" if s.reason else "")
            for s in sorted(row, key=lambda x: x.engine)
        )
        typer.echo(f"{day} {session:5} {text}")


@registry_app.command("backup")
def registry_backup(mode: ModeOpt = "prospective") -> None:
    """Copy the registry to data/backup/ with SQLite's online backup (DR-0012)."""
    with _open_registry(mode) as reg:
        dest = reg.backup(_data_dir() / "backup")
    typer.echo(f"backup written: {dest}")


def _export_repo() -> Path | None:
    value = os.environ.get("REMY_EXPORT_REPO", "").strip()
    return Path(value) if value else None


@run_app.command("tick")
def run_tick() -> None:
    """One idempotent scheduler tick (launchd runs this every minute; DR-0033)."""
    from datetime import UTC

    from remy_finance.scheduler.live import live_capture, live_hist_nightly
    from remy_finance.scheduler.ops import code_commit, notify
    from remy_finance.scheduler.tick import Tick

    data = _data_dir()
    tick = Tick(
        data_dir=data,
        capture=live_capture(data),
        closes=_close1(),
        hist_nightly=live_hist_nightly(data),
        clock=lambda: datetime.now(UTC),
        commit=lambda: code_commit(REPO_DIR, require_clean=True),
        notify=notify,
        export_repo=_export_repo(),
    )
    actions = tick.run()
    if actions:
        now = datetime.now(UTC)
        log = data / "logs" / f"{now:%Y-%m-%d}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            for a in actions:
                fh.write(f"{now.isoformat()} {a}\n")
        for a in actions:
            typer.echo(a)


@app.command()
def doctor() -> None:
    """Check the machine is ready for the prospective scheduler (exit 1 on any FAIL)."""
    import subprocess
    from datetime import UTC

    from remy_finance.scheduler.doctor import Status, run_checks, terminal_probe

    def tree_clean() -> bool | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(REPO_DIR), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.strip() == ""

    checks = run_checks(
        _data_dir(),
        datetime.now(UTC),
        terminal_probe=terminal_probe(),
        tree_clean=tree_clean,
        export_repo=_export_repo(),
    )
    for c in checks:
        typer.echo(f"{c.status.value:<4}  {c.name:<22} {c.detail}")
    raise typer.Exit(1 if any(c.status is Status.FAIL for c in checks) else 0)


@scheduler_app.command("plist")
def scheduler_plist(
    write: Annotated[
        bool, typer.Option(help="Write it to ~/Library/LaunchAgents (otherwise print it)")
    ] = False,
    export_repo: Annotated[
        str, typer.Option(help="Checkout of the registry-export branch (optional)")
    ] = "",
) -> None:
    """Render the launchd job that runs `remy run tick` every minute."""
    import shutil
    from importlib.resources import files

    uv = shutil.which("uv") or "/usr/local/bin/uv"
    template = (
        files("remy_finance.scheduler")
        .joinpath("templates/com.remy-finance.tick.plist")
        .read_text(encoding="utf-8")
    )
    data = _data_dir().resolve()
    text = template.format(
        uv=uv,
        repo=REPO_DIR,
        data_dir=data,
        export_repo=export_repo,
        theta_url=os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503"),
    )
    if not write:
        typer.echo(text)
        return
    dest = Path.home() / "Library" / "LaunchAgents" / "com.remy-finance.tick.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {dest}")
    typer.echo("load it with:")
    typer.echo(f"  launchctl bootstrap gui/$(id -u) {dest}")
    typer.echo("stop it with:")
    typer.echo("  launchctl bootout gui/$(id -u)/com.remy-finance.tick")


OutOpt = Annotated[Path | None, typer.Option(help="Also write the report to this file")]


def _eval_commit() -> str:
    import subprocess

    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_DIR),
                "log",
                "-1",
                "--format=%H",
                "--",
                "docs/evaluation/EVAL-1-scoring.md",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _emit(text: str, out: Path | None) -> None:
    typer.echo(text)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")


@report_app.command("trades")
def report_trades(mode: ModeOpt = "prospective", out: OutOpt = None) -> None:
    """Every trade: prediction, status and outcome."""
    from datetime import UTC

    from remy_finance.reports.render import trades_report

    with _open_registry(mode) as reg:
        _emit(trades_report(reg, datetime.now(UTC), _eval_commit()), out)


@report_app.command("daily")
def report_daily(
    mode: ModeOpt = "prospective",
    day: Annotated[str, typer.Option(help="YYYY-MM-DD; default today (ET)")] = "",
    out: OutOpt = None,
) -> None:
    """One day's activity: recommendations, null decisions, fills, exits, closes, gaps."""
    from datetime import UTC

    from remy_finance.data.timeutil import et_date
    from remy_finance.reports.render import daily_report
    from remy_finance.scheduler.runlog import RunLogStore
    from remy_finance.snapshots.store import SnapshotStore

    now = datetime.now(UTC)
    when = date.fromisoformat(day) if day else et_date(now)
    m = _mode(mode)
    runs = RunLogStore(_data_dir() / "runs").all(m)
    gaps = SnapshotStore(_data_dir() / "snapshots").gaps() if m is Mode.PROSPECTIVE else []
    with _open_registry(mode) as reg:
        _emit(daily_report(reg, when, now, _eval_commit(), runs, gaps), out)


@report_app.command("cumulative")
def report_cumulative(
    mode: ModeOpt = "prospective",
    out: OutOpt = None,
    closes: Annotated[
        bool,
        typer.Option(
            help="Score the underlying forecasts against CLOSE-1 official closes (gaps "
            "filled from ThetaData)."
        ),
    ] = True,
) -> None:
    """By baseline: fill rate, realized vs predicted, P&L, drawdown, scores, ranks, and
    the underlying (S_T/F) forecast scores (EVAL-1 §2.2; needs the Theta Terminal)."""
    from datetime import UTC

    from remy_finance.reports.render import cumulative_report
    from remy_finance.scheduler.runlog import RunLogStore

    source = _close1() if closes else None
    runs = RunLogStore(_data_dir() / "runs").all(_mode(mode))
    with _open_registry(mode) as reg:
        _emit(cumulative_report(reg, datetime.now(UTC), _eval_commit(), runs, source), out)


@replay_app.command("run")
def replay_run(
    start: Annotated[str, typer.Option(help="First decision date, YYYY-MM-DD")],
    end: Annotated[str, typer.Option(help="Last decision date, YYYY-MM-DD")],
    tail: Annotated[
        bool, typer.Option(help="Keep managing and settling positions after END")
    ] = True,
    workers: Annotated[int, typer.Option(help="Symbols fetched concurrently")] = 4,
) -> None:
    """Run the full R1 pipeline over [START, END] into the REPLAY registry (DR-0034).

    Safe to re-run or resume: stored boards are reused and every step is idempotent."""
    from datetime import UTC, timedelta

    from remy_finance.data.composite import CompositeSource
    from remy_finance.data.massive import MassiveSource
    from remy_finance.data.thetadata import ThetaDataSource
    from remy_finance.data.timeutil import et_date
    from remy_finance.scheduler.ops import code_commit
    from remy_finance.scheduler.replay import ReplayDriver
    from remy_finance.snapshots.builder import build_replay
    from remy_finance.snapshots.schedule import SnapshotKind

    theta = ThetaDataSource()
    massive = MassiveSource(
        min_interval=float(os.environ.get("MASSIVE_MIN_INTERVAL", "12.5")),
        include_ticker_events=False,
    )
    source = CompositeSource(quotes=theta, events=massive)
    prov = (Provenance(source="cli", details=(("command", "replay run"),)),)

    def build(
        d: date, session: Session, kind: SnapshotKind, symbols: Sequence[str], version: str
    ) -> MarketState:
        massive.prefetch(symbols)  # cached after the first board of a membership
        return build_replay(
            source,
            d,
            session,
            kind=kind,
            universe_version=version,
            underlyings=symbols,
            provenance=prov,
            max_workers=workers,
        )

    driver = ReplayDriver(
        data_dir=_data_dir(),
        build=build,
        closes=_close1(),
        code_commit=code_commit(REPO_DIR, require_clean=False),
        log=typer.echo,
        last_day=et_date(datetime.now(UTC)) - timedelta(days=1),
    )
    s = driver.run(date.fromisoformat(start), date.fromisoformat(end), tail=tail)
    typer.echo(
        f"replay done in {s.seconds / 60:.0f} min: {s.sessions} sessions + "
        f"{s.tail_sessions} tail ({s.tail_sessions_skipped} without boards); boards "
        f"{s.boards_built} built, {s.boards_reused} reused; {s.recommended} recommended, "
        f"{s.settled} settled, {s.still_open} still open (last day {s.stopped_at})"
    )
    for n in s.notes:
        typer.echo(f"note: {n}")


@replay_app.command("archive")
def replay_archive(
    label: Annotated[str, typer.Option(help="Archive folder name, e.g. 2026-08-movegen1-mkt1v1")],
) -> None:
    """Move the REPLAY registry and replay run logs to data/archive/LABEL (DR-0037).

    Nothing is deleted. Stored snapshots stay where they are, so the next replay reuses them.
    The PROSPECTIVE registry is never touched."""
    import re
    import shutil

    if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
        raise typer.BadParameter("label: letters, digits, '.', '_' and '-' only")
    data = _data_dir()
    dest = data / "archive" / label
    if dest.exists():
        typer.echo(f"{dest} already exists; choose another label")
        raise typer.Exit(1)
    from remy_finance.registry import registry_path

    reg = registry_path(data, Mode.REPLAY)
    moves = [p for p in (reg, Path(f"{reg}-wal"), Path(f"{reg}-shm")) if p.exists()]
    runs = data / "runs" / "replay"
    if runs.exists():
        moves.append(runs)
    if not moves:
        typer.echo("nothing to archive: no replay registry or replay run logs")
        raise typer.Exit(1)
    dest.mkdir(parents=True)
    for p in moves:
        shutil.move(str(p), str(dest / p.name))
        typer.echo(f"moved {p} -> {dest / p.name}")
    typer.echo("done; the next `remy replay run` starts a fresh replay registry")


@replay_app.command("bench")
def replay_bench(
    date_: Annotated[str, typer.Option("--date", help="A stored replay session, YYYY-MM-DD")],
    session: SessionOpt = "open",
) -> None:
    """Time movegen, scoring and both baselines on a stored REPLAY decision board.

    Writes nothing: the timing check for the live run's 5-minute recommendation budget."""
    from datetime import timedelta

    from remy_finance.baselines.mkt1 import run_mkt1, score_board
    from remy_finance.baselines.rand1 import run_rand1
    from remy_finance.movegen.generator import generate
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    d, sess = date.fromisoformat(date_), _session(session)
    store = SnapshotStore(_data_dir() / "snapshots")
    path = store.find(Mode.REPLAY, d, sess, SnapshotKind.DECISION)
    if path is None:
        typer.echo(f"no stored REPLAY decision board for {d} {sess.value}")
        raise typer.Exit(1)
    t = time.perf_counter()
    board = store.load(path)
    typer.echo(f"load {time.perf_counter() - t:.1f}s: {len(board.option_quotes)} quotes")
    t = time.perf_counter()
    cands, summary = generate(board)
    gen = time.perf_counter() - t
    by_type = Counter(c.structure_type.value for c in cands if c.structure_type)
    typer.echo(f"movegen {gen:.1f}s: {len(cands)} candidates")
    for name, n in by_type.most_common():
        typer.echo(f"  {name:<22} {n:>9,}")
    t = time.perf_counter()
    score = score_board(board, cands)
    typer.echo(f"scoring {time.perf_counter() - t:.1f}s: {len(score.scored)} scored")
    receipt = board.as_of + timedelta(seconds=30)
    t = time.perf_counter()
    for run in (run_mkt1, run_rand1):
        run(
            board,
            cands,
            code_commit="bench-no-commit",
            receipt_time=receipt,
            score=score,
            candidates_hash=summary.list_hash,
        )
    base = time.perf_counter() - t
    step = gen + base
    typer.echo(f"both baselines {base:.1f}s; recommendation step {step:.0f}s")
    # Live tick: decision fetch, then this step, then the execution capture, which starts
    # at decision as_of + 5 min at the earliest and must finish by scheduled + 15 min.
    if step <= 240:
        typer.echo("OK: the execution quote can be taken at about decision + 5 min")
    elif step <= 600:
        typer.echo("SLOW: the execution quote will be later than decision + 5 min (still valid)")
    else:
        typer.echo("TOO SLOW: the execution slot may miss its window and become a gap")


@replay_app.command("diagnose")
def replay_diagnose(
    date_: Annotated[str, typer.Option("--date", help="A stored replay session, YYYY-MM-DD")],
    session: SessionOpt = "open",
    top: Annotated[int, typer.Option(help="How many of the highest predictions to list")] = 15,
) -> None:
    """Where do MKT-1's positive predictions come from? (stored REPLAY board; writes nothing)

    For every scored candidate: predicted mean by structure type, split by whether all its
    legs lie inside the strike range MKT-1's smile was fitted to (OUTSIDE = extrapolated
    wings), and the highest predictions with their legs, prices and moneyness."""
    from remy_finance.reports.diagnose import diagnose_board
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    d, sess = date.fromisoformat(date_), _session(session)
    store = SnapshotStore(_data_dir() / "snapshots")
    path = store.find(Mode.REPLAY, d, sess, SnapshotKind.DECISION)
    if path is None:
        typer.echo(f"no stored REPLAY decision board for {d} {sess.value}")
        raise typer.Exit(1)
    typer.echo(diagnose_board(store.load(path), top=top))


@report_app.command("power-inputs")
def report_power_inputs(mode: ModeOpt = "replay", out: OutOpt = None) -> None:
    """Measured fill rate, sigma and design effect for the power-estimate update."""
    from remy_finance.data.timeutil import et_date
    from remy_finance.reports.measured import measured_report
    from remy_finance.reports.outcomes import outcomes
    from remy_finance.scheduler.runlog import RunLogStore

    m = _mode(mode)
    days = {et_date(r.decision_time) for r in RunLogStore(_data_dir() / "runs").all(m)}
    with _open_registry(mode) as reg:
        _emit(measured_report(outcomes(reg), len(days), m.value), out)


if __name__ == "__main__":
    app()


# ---------------------------------------------------------------------- HIST-1 (R2)


@hist_app.command("build")
def hist_build(
    start: Annotated[str, typer.Option(help="First session, YYYY-MM-DD")] = "2022-11-01",
    end: Annotated[str, typer.Option(help="Last session, YYYY-MM-DD; default yesterday (ET)")] = "",
    symbols: Annotated[
        str, typer.Option(help="Comma-separated; default UNIV-CANDIDATES-1, SPY, sector SPDRs")
    ] = "",
    workers: Annotated[int, typer.Option(help="Parallel ThetaData chain requests")] = 4,
    no_iv: Annotated[bool, typer.Option("--no-iv", help="Skip the ATM IV derivation")] = False,
) -> None:
    """Backfill HIST-1: closes, Treasury yields, dividends and splits, then 30-day ATM IV.
    Resumable: re-running skips what is stored. Needs the Theta Terminal and Massive."""
    from datetime import UTC, timedelta

    from remy_finance.data.massive import MassiveSource
    from remy_finance.data.thetadata import ThetaDataSource
    from remy_finance.data.timeutil import et_date
    from remy_finance.history import HistStore, hist_path
    from remy_finance.history.build import ThetaMassiveHistSource, base_symbols, build

    names = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else base_symbols()
    )
    last = date.fromisoformat(end) if end else et_date(datetime.now(UTC)) - timedelta(days=1)
    store = HistStore(hist_path(_data_dir()))
    massive = MassiveSource(
        min_interval=float(os.environ.get("MASSIVE_MIN_INTERVAL", "12.5")),
        include_ticker_events=True,
    )
    source = ThetaMassiveHistSource(ThetaDataSource(), massive)
    s = build(
        store, source, names, date.fromisoformat(start), last, workers=workers,
        with_iv=not no_iv, log=typer.echo,
    )  # fmt: skip
    typer.echo(
        f"HIST-1 done in {s.seconds / 60:.0f} min: closes +{s.closes_added}, yields "
        f"+{s.yields_added}, events +{s.events_added} (unusable dividends "
        f"+{s.unusable_added}), ATM IV +{s.iv_added} "
        f"(missing {s.iv_missing}), conflicts {s.conflicts}, fetch errors "
        f"{len(s.fetch_errors)} (re-run to retry)"
    )
    if s.not_public:
        typer.echo(f"  {s.not_public} sessions skipped: their close isn't public yet (17:15 ET)")
    if s.closes_absent:
        typer.echo(
            f"  {len(s.closes_absent)} (symbol, session) without a close (not yet listed, "
            "delisted or delayed); they are retried by the next run"
        )
    for e in s.fetch_errors[:20]:
        typer.echo(f"  {e}")


@hist_app.command("status")
def hist_status() -> None:
    """Counts per series and symbol, missing values by reason, and conflicts."""
    from collections import Counter

    from remy_finance.history import HistStore, hist_path

    path = hist_path(_data_dir())
    if not path.exists():
        typer.echo(f"no HIST-1 store at {path} (run `remy hist build`)")
        raise typer.Exit(1)
    store = HistStore(path)
    typer.echo(f"HIST-1 at {path}: {store.counts()}")
    reasons = Counter((m[0], m[3]) for m in store.missing())
    for (series, reason), n in sorted(reasons.items()):
        typer.echo(f"  missing {series} {reason}: {n}")
    unusable = Counter((u[0], u[3]) for u in store.unusable())
    for (kind, reason), n in sorted(unusable.items()):
        typer.echo(f"  unusable {kind} {reason}: {n} (those sessions are excluded from returns)")
    for c in store.conflicts()[:20]:
        typer.echo(f"  conflict {c.series} {c.symbol} {c.session}: kept {c.kept_hash[:12]}")


@hist_app.command("nightly")
def hist_nightly(
    day: Annotated[str, typer.Option(help="Session, YYYY-MM-DD; default the latest")] = "",
) -> None:
    """Run the scheduler's nightly HIST-1 / CLOSE-1 update by hand (DR-0046). The tick
    runs it after 17:30 ET; this is for a missed night or a check."""
    from datetime import UTC

    from remy_finance.core import Mode
    from remy_finance.data.timeutil import et_at, et_date, previous_session
    from remy_finance.registry import Registry, registry_path
    from remy_finance.scheduler.live import live_hist_nightly
    from remy_finance.scheduler.pipeline import Pipeline
    from remy_finance.scheduler.runlog import RunLogStore
    from remy_finance.scheduler.tracked import forecast_targets
    from remy_finance.snapshots.schedule import is_session

    now = datetime.now(UTC)
    if day:
        d = date.fromisoformat(day)
    else:
        today = et_date(now)
        d = today if is_session(today) and now >= et_at(today, 17, 30) else previous_session(today)
    data = _data_dir()
    runs = RunLogStore(data / "runs")
    tracked = forecast_targets(runs, Mode.PROSPECTIVE, d)
    path = registry_path(data, Mode.PROSPECTIVE)
    if path.exists():
        with Registry.open(path) as reg:
            tracked |= Pipeline(reg, runs, "", lambda: now).held(d)
    s = live_hist_nightly(data)(d, tracked, None)
    typer.echo(
        f"HIST-1 nightly {d}: {s.symbols} names ({len(tracked)} tracked), closes "
        f"+{s.closes_added}, IV +{s.iv_added} (missing {s.iv_missing}), events "
        f"+{s.events_added}, conflicts {s.conflicts}, closes absent {len(s.closes_absent)}, "
        f"not yet public {s.not_public}, fetch errors {len(s.fetch_errors)}"
    )
    for e in s.fetch_errors[:20]:
        typer.echo(f"  {e}")


@hist_app.command("coverage")
def hist_coverage(
    fill: Annotated[
        bool, typer.Option("--fill", help="Fetch the missing closes from ThetaData into CLOSE-1")
    ] = False,
) -> None:
    """Every official close the records need (positions' and MKT-1 fits' expirations, both
    registries): how many are in CLOSE-1, and which are missing (R2 brief P12)."""

    from datetime import UTC, timedelta

    from remy_finance.data.timeutil import et_date
    from remy_finance.history.closes import needed_closes

    data = _data_dir()
    last = et_date(datetime.now(UTC)) - timedelta(days=1)
    needed = needed_closes(data, last)
    store = _close1()
    plain = store.stored
    missing = sorted(k for k in needed if plain.official_close(*k) is None)
    typer.echo(f"CLOSE-1 coverage through {last}: {len(needed) - len(missing)}/{len(needed)}")
    if fill and missing:
        for sym, d in missing:
            store.official_close(sym, d)
        missing = sorted(k for k in needed if plain.official_close(*k) is None)
        typer.echo(f"  filled {store.stats.filled} from ThetaData; still missing {len(missing)}")
    for sym, d in missing[:30]:
        typer.echo(f"  missing {sym} {d}")
