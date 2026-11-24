"""VRP refits (DR-0049): fit every name at a cutoff, store the refit write-once, and give
each board its clipped ratio rho with the fallback chain.

- **Own fits:** SPY and the sector SPDRs use their own fit, unshrunk.
- **Names:** (a, b) = w x name + (1 - w) x sector, with w = n_eff / (n_eff + 12). Without a
  usable name fit, the sector's fit; without that, no ratio ("no VRP ratio", EVAL-2 §7.2).
- **A board:** rho = clip(a + b x SPYIV30 at the previous session's public EOD value).
- **Refits** are stored write-once under ``data/vrp/VRP-<cutoff>.json`` with the hash of
  their inputs (the admitted rows); a board uses the latest refit whose cutoff is at or
  before its decision time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from remy_finance.baselines.common import to_decimal_places
from remy_finance.core import Record
from remy_finance.core.types import UtcDatetime
from remy_finance.data.timeutil import et_date, previous_session
from remy_finance.fileio import publish_file
from remy_finance.history import HistoryView, HistSeries, HistStore
from remy_finance.vrp.estimator import (
    CLIP,
    SHRINK_K,
    SPY,
    WINDOW,
    OlsFit,
    RowSet,
    build_rows,
    clip_ratio,
    fit_ols,
    pooled_rows,
    shrink_weight,
    spy_series,
)

PLACES = 10
METHOD = (
    f"VRP-1 (DR-0049): ratio = RV/IV^2 over {WINDOW} sessions, OLS on SPYIV30, Newey-West lag "
    f"{WINDOW}, shrink w = n_eff/(n_eff+{SHRINK_K:g}), clip {CLIP}"
)


def _d(x: float) -> Decimal:
    return to_decimal_places(x, PLACES)


class VrpCoefficients(Record):
    a: Decimal
    b: Decimal
    se_a: Decimal
    se_b: Decimal
    n_rows: int = Field(ge=0)
    clipped_share: Decimal

    @classmethod
    def of(cls, fit: OlsFit) -> VrpCoefficients:
        return cls(
            a=_d(fit.a),
            b=_d(fit.b),
            se_a=_d(fit.se_a),
            se_b=_d(fit.se_b),
            n_rows=fit.n_rows,
            clipped_share=_d(fit.clipped_share),
        )


class VrpName(Record):
    """One symbol's entry in a refit: its own fit, the shrinkage, and what a board uses."""

    symbol: str
    sector: str | None  # None for SPY and the sector SPDRs (own fit, unshrunk)
    own: VrpCoefficients | None  # None: too few mature rows or a failed fit
    weight: Decimal | None  # w applied to ``own``; None if not shrunk
    used: VrpCoefficients | None  # what boards apply; None: no ratio
    source: str  # "own", "shrunk", "sector" or "none"
    reason: str = ""  # why ``own`` or ``used`` is missing
    dropped: tuple[tuple[str, int], ...] = ()  # rows dropped, by reason


class VrpRefit(Record):
    SCHEMA_VERSION: ClassVar[int] = 1

    method: str
    cutoff: UtcDatetime
    names: tuple[VrpName, ...]
    sectors: tuple[tuple[str, VrpCoefficients | None], ...]
    inputs_hash: str  # sha256 of every admitted row, by symbol
    created_at: UtcDatetime

    def name(self, symbol: str) -> VrpName | None:
        return next((n for n in self.names if n.symbol == symbol), None)


def _rows_hash(row_sets: Mapping[str, RowSet]) -> str:
    h = hashlib.sha256()
    for sym in sorted(row_sets):
        for r in row_sets[sym].rows:
            h.update(f"{sym}|{r.session}|{r.response}|{r.ratio!r}|{r.x!r}\n".encode())
    return h.hexdigest()


def fit_refit(
    store: HistStore,
    symbols: Sequence[str],
    sectors: Mapping[str, str],
    cutoff: datetime,
    created_at: datetime,
    history_ok: Mapping[str, bool] | None = None,
) -> VrpRefit:
    """Fit every symbol as of ``cutoff``. ``sectors`` maps a name to its sector SPDR (the
    factor model's mapping); a symbol not in it (SPY, the SPDRs, or an unmapped name) uses
    its own fit unshrunk. ``history_ok`` says which names have DR-0044's 3 years and may
    join a sector's pooled fit (default: all)."""
    view = HistoryView(store, cutoff)
    spy = spy_series(view)
    row_sets = {s: build_rows(view, s, spy) for s in sorted(set(symbols))}
    own = {s: fit_ols(rs.rows) for s, rs in row_sets.items()}
    sector_fits: dict[str, OlsFit | None] = {}
    for sec in sorted(set(sectors.values())):
        members = [
            row_sets[n]
            for n, s in sectors.items()
            if s == sec and n in row_sets and (history_ok is None or history_ok.get(n, False))
        ]
        sector_fits[sec] = fit_ols(pooled_rows(members)) if members else None
    names = [
        _name_entry(sym, rs, own[sym], sectors.get(sym), sector_fits)
        for sym, rs in row_sets.items()
    ]
    return VrpRefit(
        method=METHOD,
        cutoff=cutoff,
        names=tuple(names),
        sectors=tuple((s, VrpCoefficients.of(f) if f else None) for s, f in sector_fits.items()),
        inputs_hash=_rows_hash(row_sets),
        created_at=created_at,
    )


def _name_entry(
    sym: str,
    rs: RowSet,
    fit: OlsFit | None,
    sector: str | None,
    sector_fits: Mapping[str, OlsFit | None],
) -> VrpName:
    dropped = tuple(sorted(rs.dropped.items()))
    own_c = VrpCoefficients.of(fit) if fit is not None else None
    no_own = "" if fit is not None else f"too few mature rows ({len(rs.rows)}) or a failed fit"
    base = {"symbol": sym, "sector": sector, "own": own_c, "dropped": dropped}
    if sector is None:  # SPY, a sector SPDR or an unmapped name: its own fit, unshrunk
        source = "own" if own_c is not None else "none"
        return VrpName(**base, weight=None, used=own_c, source=source, reason=no_own)
    sfit = sector_fits.get(sector)
    if fit is not None and sfit is not None:
        w = shrink_weight(fit.n_eff)
        shrunk = VrpCoefficients.of(fit).model_copy(
            update={
                "a": _d(w * fit.a + (1 - w) * sfit.a),
                "b": _d(w * fit.b + (1 - w) * sfit.b),
            }
        )
        return VrpName(**base, weight=_d(w), used=shrunk, source="shrunk")
    if fit is not None:  # no sector fit to shrink toward: the name's own
        return VrpName(
            **base, weight=None, used=own_c, source="own", reason=f"no {sector} sector fit"
        )
    if sfit is not None:
        used = VrpCoefficients.of(sfit)
        return VrpName(**base, weight=None, used=used, source="sector", reason=no_own)
    reason = f"{no_own}; no {sector} sector fit"
    return VrpName(**base, weight=None, used=None, source="none", reason=reason)


class VrpStore:
    """Write-once refits under ``root`` (``data/vrp``)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, cutoff: datetime) -> Path:
        return self.root / f"VRP-{cutoff.strftime('%Y%m%dT%H%M%SZ')}.json"

    def write(self, refit: VrpRefit) -> Path:
        path = self.path(refit.cutoff)
        publish_file(path, refit.canonical_json())  # FileExistsError if present
        return path

    def all(self) -> list[VrpRefit]:
        refits = [
            VrpRefit.from_canonical_json(p.read_text("utf-8")) for p in self.root.glob("VRP-*.json")
        ]
        return sorted(refits, key=lambda r: r.cutoff)

    def for_decision(self, decision_time: datetime) -> VrpRefit | None:
        """The latest refit whose cutoff is at or before ``decision_time``."""
        usable = [r for r in self.all() if r.cutoff <= decision_time]
        return usable[-1] if usable else None


def board_ratio(
    refit: VrpRefit | None, store: HistStore, symbol: str, decision_time: datetime
) -> tuple[float | None, str]:
    """(rho, note) for ``symbol`` on a board decided at ``decision_time``: the refit's
    coefficients at SPYIV30's previous-session public EOD value, clipped. (None, reason)
    when there's no ratio."""
    if refit is None or refit.cutoff > decision_time:
        return None, "no VRP refit at or before the decision time"
    entry = refit.name(symbol)
    if entry is None or entry.used is None:
        return None, f"no VRP ratio ({entry.reason if entry else 'not in the refit'})"
    prev = previous_session(et_date(decision_time))
    x = HistoryView(store, decision_time).value(HistSeries.ATMIV30, SPY, prev)
    if x is None:
        return None, f"no public SPYIV30 for {prev}"
    raw = float(entry.used.a) + float(entry.used.b) * float(x)
    return clip_ratio(raw), f"{entry.source}; raw {raw:.6f}"


def refit_summary(refit: VrpRefit) -> dict[str, object]:
    by_source: dict[str, int] = {}
    for n in refit.names:
        by_source[n.source] = by_source.get(n.source, 0) + 1
    return {"cutoff": refit.cutoff.isoformat(), "names": len(refit.names), "by_source": by_source}
