"""Enumerations used by core records. Values are part of the canonical form."""

from enum import IntEnum, StrEnum


class Right(StrEnum):
    CALL = "C"
    PUT = "P"


class Style(StrEnum):
    AMERICAN = "AMERICAN"


class Side(IntEnum):
    LONG = 1
    SHORT = -1


class Session(StrEnum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class Mode(StrEnum):
    PROSPECTIVE = "PROSPECTIVE"
    REPLAY = "REPLAY"


class Priceability(StrEnum):
    PRICED = "PRICED"
    UNPRICEABLE = "UNPRICEABLE"


class QualityFlag(StrEnum):
    STALE_QUOTE = "STALE_QUOTE"  # quote older than the QAGE-1 threshold (DR-0015)
    QUOTE_TIME_IS_REQUEST_TIME = "QUOTE_TIME_IS_REQUEST_TIME"  # vendor stamped request time
    ONE_SIDED = "ONE_SIDED"
    CROSSED = "CROSSED"
    ZERO_SIZE = "ZERO_SIZE"


class StructureType(StrEnum):
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    VERTICAL_DEBIT = "VERTICAL_DEBIT"
    VERTICAL_CREDIT = "VERTICAL_CREDIT"
    IRON_CONDOR = "IRON_CONDOR"
    LONG_CALL_BUTTERFLY = "LONG_CALL_BUTTERFLY"
    LONG_PUT_BUTTERFLY = "LONG_PUT_BUTTERFLY"
    OTHER_DEFINED_RISK = "OTHER_DEFINED_RISK"


class DividendType(StrEnum):
    ORDINARY = "ORDINARY"
    SPECIAL = "SPECIAL"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    SPECIAL_DIVIDEND = "SPECIAL_DIVIDEND"
    MERGER = "MERGER"
    SPIN_OFF = "SPIN_OFF"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    OTHER = "OTHER"
