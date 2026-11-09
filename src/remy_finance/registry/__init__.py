"""Paper-trade registry: frozen recommendations, append-only journal, hash chain, export."""

from remy_finance.registry.lifecycle import LifecycleError, check_next
from remy_finance.registry.records import (
    ADJ_0,
    EVAL_1_BINS,
    EventKind,
    JournalEvent,
    Quantile,
    Recommendation,
    ReturnBins,
    ReturnDistribution,
    derive_trade_id,
)
from remy_finance.registry.store import (
    GENESIS,
    Registry,
    RegistryError,
    Row,
    VerifyReport,
    chain_hash,
    registry_path,
)

__all__ = [
    "ADJ_0",
    "EVAL_1_BINS",
    "GENESIS",
    "EventKind",
    "JournalEvent",
    "LifecycleError",
    "Quantile",
    "Recommendation",
    "Registry",
    "RegistryError",
    "ReturnBins",
    "ReturnDistribution",
    "Row",
    "VerifyReport",
    "chain_hash",
    "check_next",
    "derive_trade_id",
    "registry_path",
]
