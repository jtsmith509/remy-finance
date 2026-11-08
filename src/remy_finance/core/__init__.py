"""Core domain types: contracts, quotes, the MarketState board, structures, candidates.

Every record is immutable, serializes to canonical JSON and has a SHA-256 content hash
(CANON-1, DR-0016). Cash-relevant numbers are ``Decimal`` only.
"""

from remy_finance.core.enums import (
    CorporateActionType,
    DividendType,
    Mode,
    Priceability,
    QualityFlag,
    Right,
    Session,
    Side,
    StructureType,
    Style,
)
from remy_finance.core.errors import LookaheadError
from remy_finance.core.instruments import Candidate, Leg, OptionContract, Structure
from remy_finance.core.market import (
    CorporateAction,
    DividendEvent,
    MarketState,
    OptionQuote,
    Provenance,
    QualityNote,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.core.record import Record

__all__ = [
    "Candidate",
    "CorporateAction",
    "CorporateActionType",
    "DividendEvent",
    "DividendType",
    "Leg",
    "LookaheadError",
    "MarketState",
    "Mode",
    "OptionContract",
    "OptionQuote",
    "Priceability",
    "Provenance",
    "QualityFlag",
    "QualityNote",
    "RatePoint",
    "Record",
    "Right",
    "Session",
    "Side",
    "Structure",
    "StructureType",
    "Style",
    "UnderlyingQuote",
]
