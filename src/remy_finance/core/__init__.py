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
from remy_finance.core.fees import FEES_1, FeeSchedule
from remy_finance.core.instruments import Leg, OptionContract
from remy_finance.core.market import (
    CorporateAction,
    DividendEvent,
    MarketState,
    OptionQuote,
    OptionVolume,
    Provenance,
    QualityNote,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.core.payoff import UndefinedRiskError
from remy_finance.core.record import Record
from remy_finance.core.structure import Candidate, Structure

__all__ = [
    "FEES_1",
    "Candidate",
    "CorporateAction",
    "CorporateActionType",
    "DividendEvent",
    "DividendType",
    "FeeSchedule",
    "Leg",
    "LookaheadError",
    "MarketState",
    "Mode",
    "OptionContract",
    "OptionQuote",
    "OptionVolume",
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
    "UndefinedRiskError",
    "UnderlyingQuote",
]
