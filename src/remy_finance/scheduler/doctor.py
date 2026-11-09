"""`remy doctor`: is the Mac mini ready to run the prospective scheduler? (handoff R1.6)

Checks: Theta Terminal reachable; the Massive key present (environment or Keychain; never
printed); disk space; the universe in force today; the latest snapshot versus the latest
slot that should exist; registry chains (and the export, if configured); trades past
expiration still open; the scheduler heartbeat; recent failures; a clean code tree.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_date, previous_session
from remy_finance.registry import Registry, registry_path
from remy_finance.secrets import secret_source
from remy_finance.snapshots.schedule import (
    PROSPECTIVE_MAX_DELAY,
    SnapshotKind,
    is_session,
    snapshot_time,
)
from remy_finance.snapshots.store import SnapshotStore


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def latest_due_slot(now: datetime) -> tuple[datetime, Session] | None:
    """The most recent decision slot whose capture window has closed."""
    day = et_date(now)
    for _ in range(10):
        if is_session(day):
            for session in (Session.CLOSE, Session.OPEN):
                t = snapshot_time(day, session, SnapshotKind.DECISION)
                if t + PROSPECTIVE_MAX_DELAY <= now:
                    return t, session
        day = previous_session(day)
    return None


def run_checks(
    data_dir: Path,
    now: datetime,
    *,
    terminal_probe: Callable[[], str | None],
    tree_clean: Callable[[], bool | None],
    export_repo: Path | None = None,
) -> list[Check]:
    out: list[Check] = []

    err = terminal_probe()
    out.append(Check("Theta Terminal", Status.FAIL if err else Status.OK, err or "reachable"))

    src = secret_source("MASSIVE_API_KEY")
    out.append(
        Check(
            "Massive key",
            Status.FAIL if src == "missing" else Status.OK,
            f"found in {src}" if src != "missing" else "not in the environment or Keychain",
        )
    )

    data_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(data_dir).free / 1e9
    disk = Status.FAIL if free_gb < 2 else Status.WARN if free_gb < 10 else Status.OK
    out.append(Check("Disk space", disk, f"{free_gb:.1f} GB free under {data_dir}"))

    from remy_finance.universe.selection import UniverseStore

    today = et_date(now)
    membership = UniverseStore(data_dir / "universe").membership_for(today)
    out.append(
        Check(
            "Universe",
            Status.OK if membership else Status.FAIL,
            f"{membership.version_label}: {len(membership.members)} names"
            if membership
            else f"no UNIV-1 membership in force on {today}",
        )
    )

    store = SnapshotStore(data_dir / "snapshots")
    due = latest_due_slot(now)
    if due is not None:
        t, session = due
        d = et_date(t)
        if store.find(Mode.PROSPECTIVE, d, session, SnapshotKind.DECISION):
            out.append(Check("Last snapshot", Status.OK, f"{d} {session.value} stored"))
        elif store.has_gap(d, session, SnapshotKind.DECISION):
            out.append(Check("Last snapshot", Status.WARN, f"{d} {session.value} is a gap"))
        else:
            out.append(
                Check(
                    "Last snapshot", Status.WARN, f"{d} {session.value}: neither stored nor a gap"
                )
            )

    for mode in (Mode.PROSPECTIVE, Mode.REPLAY):
        path = registry_path(data_dir, mode)
        if not path.exists():
            out.append(Check(f"Registry ({mode.value.lower()})", Status.WARN, "not created yet"))
            continue
        with Registry.open(path, mode) as reg:
            exp = export_repo if mode is Mode.PROSPECTIVE else None
            report = reg.verify(export_dir=exp if exp and exp.exists() else None)
            rows = ", ".join(f"{k} {v}" for k, v in report.rows.items())
            detail = rows if report.ok else f"{len(report.problems)} problems: {report.problems[0]}"
            out.append(
                Check(
                    f"Registry ({mode.value.lower()})",
                    Status.OK if report.ok else Status.FAIL,
                    detail,
                )
            )
            if mode is Mode.PROSPECTIVE:
                from remy_finance.execution.engine import PaperEngine

                engine = PaperEngine(reg)
                stale = [
                    r.trade_id
                    for r in reg.recommendations()
                    if r.expiration < today - timedelta(days=1)
                    and engine.position(r.trade_id).is_open
                ]
                out.append(
                    Check(
                        "Unsettled expirations",
                        Status.WARN if stale else Status.OK,
                        f"{len(stale)} open past expiration" if stale else "none",
                    )
                )

    beat = data_dir / "scheduler" / "last_tick"
    if beat.exists():
        last = datetime.fromisoformat(beat.read_text(encoding="utf-8").strip())
        age = now - last
        status = Status.OK if age < timedelta(minutes=5) else Status.WARN
        out.append(Check("Scheduler heartbeat", status, f"last tick {age} ago"))
    else:
        out.append(Check("Scheduler heartbeat", Status.WARN, "no tick has run yet"))

    failures = data_dir / "logs" / "failures"
    cutoff = (now - timedelta(days=1)).strftime("%Y%m%dT%H%M%S")
    recent = (
        [p for p in failures.glob("*.json") if p.name[:15] >= cutoff] if failures.exists() else []
    )
    out.append(
        Check(
            "Failures (24 h)",
            Status.WARN if recent else Status.OK,
            f"{len(recent)}: {', '.join(sorted(p.name for p in recent)[-3:])}"
            if recent
            else "none",
        )
    )

    clean = tree_clean()
    out.append(
        Check(
            "Code tree",
            Status.OK if clean else Status.WARN,
            "clean"
            if clean
            else "uncommitted changes: recommendations will be skipped"
            if clean is False
            else "not a git checkout",
        )
    )
    return out


def terminal_probe(base_url: str | None = None) -> Callable[[], str | None]:
    def probe() -> str | None:
        import httpx

        url = base_url or os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503")
        try:
            r = httpx.get(f"{url}/v3/stock/snapshot/quote", params={"symbol": "SPY"}, timeout=5)
        except httpx.HTTPError as e:
            return f"{url}: {type(e).__name__}"
        return None if r.status_code == 200 else f"{url}: HTTP {r.status_code}"

    return probe
