"""Suite-wide guard: the test suite is offline (CLAUDE.md). Any Python socket connection,
localhost included, fails the test that makes it. Adopted from Codex's review-only
plugin (docs/review/R1-extras-findings.md), which found 0 attempts at fc8b424."""

from __future__ import annotations

import socket
from typing import Any

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _blocked(self: socket.socket, address: Any) -> None:
    raise AssertionError(
        f"network or localhost connection attempted in the offline suite: {address}"
    )


def pytest_configure(config: Any) -> None:
    socket.socket.connect = _blocked  # type: ignore[method-assign]
    socket.socket.connect_ex = _blocked  # type: ignore[assignment,method-assign]


def pytest_unconfigure(config: Any) -> None:
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
