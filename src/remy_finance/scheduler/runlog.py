"""Write-once store of baseline run logs: the per-run decision log (DR-0028 §11, DR-0033).

One file per (mode, engine, decision snapshot):
``data/runs/<mode>/<YYYY-MM-DD>/<SESSION>-<engine>-<snapshot_id[:16]>.json``, holding the
``BaselineRun`` record's canonical JSON. Files are published atomically and never
overwritten (``publish_file``), so a re-run finds the log and skips the baseline.
"""

from __future__ import annotations

from pathlib import Path

from remy_finance.baselines.common import BaselineRun
from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_date
from remy_finance.fileio import publish_file


class RunLogStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path(self, mode: Mode, session: Session, run: BaselineRun) -> Path:
        day = et_date(run.decision_time).isoformat()
        name = f"{session.value}-{run.engine}-{run.snapshot_id[:16]}.json"
        return self.root / mode.value.lower() / day / name

    def find(
        self, mode: Mode, session: Session, engine: str, snapshot_id: str, day: str
    ) -> Path | None:
        name = f"{session.value}-{engine}-{snapshot_id[:16]}.json"
        p = self.root / mode.value.lower() / day / name
        return p if p.exists() else None

    def write(self, mode: Mode, session: Session, run: BaselineRun) -> Path:
        if run.mode is not mode:
            raise ValueError(f"{run.mode.value} run log in the {mode.value} store")
        path = self.path(mode, session, run)
        publish_file(path, run.canonical_json())  # raises FileExistsError if present
        return path

    def load(self, path: Path) -> BaselineRun:
        return BaselineRun.from_canonical_json(path.read_text(encoding="utf-8"))

    def all(self, mode: Mode) -> list[BaselineRun]:
        base = self.root / mode.value.lower()
        return [self.load(p) for p in sorted(base.glob("*/*.json"))] if base.exists() else []
