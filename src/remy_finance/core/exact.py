"""Exact Decimal arithmetic for cash and payoff calculations (DR-0025).

Python's default Decimal context keeps 28 significant digits and silently rounds. For
payoff maths that can cancel catastrophically (e.g. S_T = 1e29 erased a spread's $500
payoff). ``exact_decimal`` sets a local precision wide enough for the operands' full digit
span plus headroom, and traps ``Inexact``: inside it, + - x and max either give the exact
result or raise ``decimal.Inexact``. They never silently round.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from decimal import Decimal, Inexact, localcontext

HEADROOM_DIGITS = 40  # covers products with multipliers, ratios, quantities and sums


def _span(values: Iterable[Decimal | int]) -> int:
    hi, lo = 0, 0
    for v in values:
        d = v if isinstance(v, Decimal) else Decimal(v)
        if not d.is_finite() or d == 0:
            continue
        exponent = d.as_tuple().exponent
        assert isinstance(exponent, int)
        hi = max(hi, d.adjusted())
        lo = min(lo, exponent)
    return hi - lo + 1


@contextmanager
def exact_decimal(*values: Decimal | int) -> Iterator[None]:
    """Run the block with enough precision for ``values`` and ``Inexact`` trapped."""
    with localcontext() as ctx:
        ctx.prec = max(28, _span(values) + HEADROOM_DIGITS)
        ctx.traps[Inexact] = True
        yield
