"""Operational helpers for the scheduler: code commit, notifications, failure records,
daily markers and the registry export push (DR-0033)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from remy_finance.fileio import publish_file


class DirtyTreeError(RuntimeError):
    """Prospective recommendations must come from a committed code state."""


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=60
    )
    return out.stdout.strip()


def code_commit(repo: Path, *, require_clean: bool) -> str:
    """HEAD of ``repo``. With ``require_clean``, tracked files must have no uncommitted
    changes, so a recommendation's ``code_commit`` really names the code that made it."""
    head = _git(repo, "rev-parse", "HEAD")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        if require_clean:
            raise DirtyTreeError(f"uncommitted changes in {repo}:\n{dirty}")
        return head + "+dirty"
    return head


def notify(title: str, message: str) -> None:
    """A macOS notification (best effort; a no-op elsewhere)."""
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(message[:240])} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False, timeout=10)


def record_failure(logs_dir: Path, step: str, error: BaseException, now: datetime) -> Path:
    """A write-once failure record under ``data/logs/failures/``."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = logs_dir / "failures" / f"{stamp}-{step}.json"
    body = {
        "step": step,
        "at": now.astimezone(UTC).isoformat(),
        "error_type": type(error).__name__,
        "error": str(error)[:2000],
    }
    publish_file(path, json.dumps(body, sort_keys=True, indent=1))
    return path


class Markers:
    """Once-per-day markers (e.g. the nightly export or backup is done for a session)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, day: date, name: str) -> Path:
        return self.root / f"{day.isoformat()}-{name}.done"

    def done(self, day: date, name: str) -> bool:
        return self._path(day, name).exists()

    def mark(self, day: date, name: str, note: str = "") -> None:
        path = self._path(day, name)
        if not path.exists():
            publish_file(path, note + "\n")


def _unpushed(repo: Path) -> bool:
    """True if HEAD is not what the remote branch of the same name points at."""
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    remote = _git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()
    return not remote or remote[0] != _git(repo, "rev-parse", "HEAD")


def push_export(repo: Path, day: date, rows: int) -> str:
    """Commit and push new export lines in ``repo``, a git checkout of the
    ``registry-export`` branch. Returns HEAD once it is on the remote, or "" if there was
    nothing to commit or push.

    A push is attempted whenever HEAD is not on the remote, even if the working tree is
    clean, so an export commit whose push failed earlier is pushed by the retry (Codex
    R1.6 F01). The caller marks the day exported only after this returns."""
    _git(repo, "add", "-A", ".")
    if _git(repo, "status", "--porcelain"):
        _git(repo, "commit", "-m", f"registry export {day.isoformat()}: {rows} new rows")
    if not _unpushed(repo):
        return ""
    _git(repo, "push", "origin", "HEAD")
    return _git(repo, "rev-parse", "HEAD")
