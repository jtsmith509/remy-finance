"""Atomic, write-once file publication shared by the stores (DR-0024)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def publish_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically and never overwrite: temp file + fsync, then a
    hard link (which fails if ``path`` exists), then remove the temp name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.chmod(0o444)
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
