"""MKT-1 diagnostics on one board: where do positive predictions come from? (no writes)

The sanity property says MKT-1's predicted net returns should cluster near
-(half-spreads + fees) / capital at risk. This breaks predictions down by structure type
and by whether every leg lies inside the strike range the smile was fitted to: outside it
the SVI wings are extrapolated, which is where a fit artifact would show up first.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

from remy_finance.baselines.mkt1 import BoardScore, Scored, score_board
from remy_finance.core import MarketState, Side
from remy_finance.movegen.generator import generate


def inside_fit(score: BoardScore, s: Scored) -> bool:
    m = score.model_for(s.candidate)
    lo, hi = m.fit_strikes or (0.0, float("inf"))
    return all(lo <= float(lg.contract.strike) <= hi for lg in s.candidate.legs)


def diagnose_board(board: MarketState, top: int = 15) -> str:
    cands, _ = generate(board)
    score = score_board(board, cands)
    ok = sum(1 for f in score.fits if f.ok)
    out = [
        f"{board.as_of.isoformat()} {board.session.value}: {len(cands)} candidates, "
        f"{len(score.scored)} scored; fits {ok} ok / {len(score.fits) - ok} rejected",
    ]
    rejected = Counter(re.sub(r"-?\d+(\.\d+)?", "#", f.reason) for f in score.fits if not f.ok)
    out += [f"  rejected {reason}: {n}" for reason, n in rejected.most_common()]

    groups: dict[tuple[str, bool], list[float]] = {}
    for s in score.scored:
        stype = s.candidate.structure_type
        key = (stype.value if stype else "-", inside_fit(score, s))
        groups.setdefault(key, []).append(s.mean)
    out += ["", f"{'structure':<22} {'legs':<8} {'n':>8} {'median':>8} {'p90':>8} "
            f"{'max':>8} {'> 0':>7}"]  # fmt: skip
    for (name, ins), xs in sorted(groups.items()):
        xs.sort()
        p90 = xs[int(0.9 * (len(xs) - 1))]
        pos = sum(1 for x in xs if x > 0) / len(xs)
        out.append(
            f"{name:<22} {'inside' if ins else 'OUTSIDE':<8} {len(xs):>8} "
            f"{statistics.median(xs):>8.1%} {p90:>8.1%} {xs[-1]:>8.1%} {pos:>7.1%}"
        )

    out += ["", f"top {top} predictions (what MKT-1 would rank first):"]
    for s in sorted(score.scored, key=lambda s: -s.mean)[:top]:
        m = score.model_for(s.candidate)
        lo, hi = m.fit_strikes or (0.0, 0.0)
        struct = s.candidate.structure
        assert struct is not None
        legs = ", ".join(
            ("+" if lg.side is Side.LONG else "-")
            + f"{lg.contract.strike}{lg.contract.right.value[0]}@{p}"
            for lg, p in zip(struct.legs, struct.entry_prices, strict=True)
        )
        k_over_f = "/".join(f"{float(lg.contract.strike) / m.forward:.2f}" for lg in struct.legs)
        where = "inside" if inside_fit(score, s) else "OUTSIDE"
        out.append(
            f"  {s.mean:>7.1%} {m.symbol:<5} exp {m.expiration} {legs}  K/F {k_over_f}  "
            f"fit {lo:g}-{hi:g} {where}"
        )
    return "\n".join(out)
