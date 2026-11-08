"""Exceptions shared across REMY Finance."""


class LookaheadError(Exception):
    """Raised when data stamped after the decision time would be read or embedded.

    Deliberately *not* a ``ValueError``: pydantic wraps ``ValueError`` raised inside
    validators into ``ValidationError``, and a look-ahead must surface as itself.
    """
