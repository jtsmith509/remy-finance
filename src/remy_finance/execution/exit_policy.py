"""Exit policies (handoff R1.3).

An exit policy decides, at each decision snapshot while a position is open, whether to
close it at that snapshot's execution quote. The engine (``execution.engine``) handles
everything that is *not* discretionary: forced closes after an assumed assignment
(ASSIGN-1) and expiration settlement (EXP-1).

``HOLD-1`` (D10) never exits on its own. The spec's least-squares Monte Carlo policy
optimizer is R4: it will implement this same interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from remy_finance.core import MarketState

if TYPE_CHECKING:
    from remy_finance.execution.engine import Position


class ExitPolicy(Protocol):
    @property
    def policy_id(self) -> str: ...

    @property
    def closing_trade_required(self) -> bool:
        """True if the policy plans a closing trade, so exit fees count in capital at risk."""
        ...

    def wants_exit(self, position: Position, board: MarketState) -> bool:
        """Decide from ``board`` (the decision snapshot) alone whether to exit now."""
        ...


class Hold1:
    """HOLD-1: hold to expiration; no discretionary exits."""

    policy_id = "HOLD-1"
    closing_trade_required = False

    def wants_exit(self, position: Position, board: MarketState) -> bool:
        return False


HOLD_1 = Hold1()
EXIT_POLICIES: Mapping[str, ExitPolicy] = MappingProxyType({HOLD_1.policy_id: HOLD_1})
