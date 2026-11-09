"""Secret lookup: an environment variable, else the macOS Keychain (CLAUDE.md rule 6).

launchd jobs don't inherit shell profiles, so the scheduler reads keys from the Keychain
rather than from the plist. Store one with:

    security add-generic-password -s remy-finance -a MASSIVE_API_KEY -w

Values are never logged or printed; ``secret_source`` only says where one was found.
"""

from __future__ import annotations

import os
import subprocess
import sys

KEYCHAIN_SERVICE = "remy-finance"


def _keychain(name: str) -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def secret(name: str) -> str | None:
    return os.environ.get(name) or _keychain(name)


def secret_source(name: str) -> str:
    """'environment', 'keychain' or 'missing' (never the value)."""
    if os.environ.get(name):
        return "environment"
    return "keychain" if _keychain(name) else "missing"
