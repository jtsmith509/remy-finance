"""``HistoryView``: the only read path from HIST-1 for models (DR-0045).

Everything is read **as of** a moment: an observation, dividend or split is visible only if
its public time is at or before ``as_of``, and asking for a session after ``as_of``'s date
raises ``LookaheadError``. Total returns are rebuilt as of ``as_of``, so an action that was
not yet public cannot change a fit made before it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from remy_finance.core import CorporateActionType, LookaheadError
from remy_finance.data.timeutil import et_date, previous_session
from remy_finance.history.records import RATE_SYMBOL, YIELD_TENOR_YEARS, HistObservation, HistSeries
from remy_finance.history.store import HistStore

SPLIT_KINDS = (CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT)
UNSUPPORTED_KINDS = (
    CorporateActionType.SYMBOL_CHANGE,
    CorporateActionType.MERGER,
    CorporateActionType.SPIN_OFF,
    CorporateActionType.OTHER,
)


@dataclass(frozen=True)
class DailyReturn:
    session: date
    log_return: float


@dataclass(frozen=True)
class ExcludedReturn:
    session: date
    reason: str


class HistoryView:
    def __init__(self, store: HistStore, as_of: datetime) -> None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        self.store = store
        self.as_of = as_of
        self.as_of_date = et_date(as_of)

    def _check(self, end: date | None) -> None:
        if end is not None and end > self.as_of_date:
            raise LookaheadError(f"HIST-1 read through {end} requested as of {self.as_of}")

    # ------------------------------------------------------------------ series

    def series(
        self, series: HistSeries, symbol: str, start: date | None = None, end: date | None = None
    ) -> list[HistObservation]:
        self._check(end)
        return self.store.observations(series, symbol, start, end, public_by=self.as_of)

    def value(self, series: HistSeries, symbol: str, session: date) -> Decimal | None:
        self._check(session)
        got = self.series(series, symbol, session, session)
        return got[0].value if got else None

    def latest(self, series: HistSeries, symbol: str) -> HistObservation | None:
        got = self.series(series, symbol, None, self.as_of_date)
        return got[-1] if got else None

    def rates(self) -> dict[float, float]:
        """Latest public Treasury yields by maturity in years (for discount factors)."""
        out: dict[float, float] = {}
        for s, years in YIELD_TENOR_YEARS.items():
            o = self.latest(s, RATE_SYMBOL)
            if o is not None:
                out[years] = float(o.value)
        return out

    # ------------------------------------------------------------------ returns

    def total_returns(
        self, symbol: str, start: date, end: date
    ) -> tuple[list[DailyReturn], list[ExcludedReturn]]:
        """Daily log total returns for sessions in [start, end], as of ``as_of``:
        r_s = ln(m_s · (C_s + D_s) / C_prev), where C_prev is the previous XNYS session's
        close, m_s the share multiplier of splits effective on s and D_s the cash dividends
        with ex-date s, counting only actions public by ``as_of``. No return is computed
        across a missing close; sessions with an unsupported action are excluded."""
        self._check(end)
        closes = {o.session: float(o.value) for o in self.series(HistSeries.CLOSE, symbol)}
        divs: dict[date, float] = {}
        for d in self.store.dividends(symbol):
            if d.public_time <= self.as_of:
                divs[d.ex_date] = divs.get(d.ex_date, 0.0) + float(d.amount)
        mult: dict[date, float] = {}
        bad: dict[date, str] = {}
        for a in self.store.actions(symbol):
            if a.public_time > self.as_of:
                continue
            det = dict(a.details)
            if a.action_type in SPLIT_KINDS:
                frm, to = float(det.get("split_from", 0)), float(det.get("split_to", 0))
                if frm > 0 and to > 0:
                    mult[a.effective_date] = mult.get(a.effective_date, 1.0) * to / frm
                else:
                    bad[a.effective_date] = "SPLIT_RATIO_MISSING"
            elif a.action_type is CorporateActionType.SPECIAL_DIVIDEND:
                if a.effective_date not in divs:
                    bad[a.effective_date] = "SPECIAL_DIVIDEND_WITHOUT_CASH_RECORD"
            elif a.action_type in UNSUPPORTED_KINDS:
                bad[a.effective_date] = f"UNSUPPORTED_ACTION:{a.action_type.value}"
        rets: list[DailyReturn] = []
        excluded: list[ExcludedReturn] = []
        sessions = sorted(s for s in closes if start <= s <= end)
        for s in sessions:
            prev = previous_session(s)
            if prev not in closes:
                excluded.append(ExcludedReturn(s, "PREVIOUS_CLOSE_MISSING"))
                continue
            if s in bad:
                excluded.append(ExcludedReturn(s, bad[s]))
                continue
            gross = mult.get(s, 1.0) * (closes[s] + divs.get(s, 0.0)) / closes[prev]
            if gross <= 0:
                excluded.append(ExcludedReturn(s, "NONPOSITIVE_GROSS_RETURN"))
                continue
            rets.append(DailyReturn(s, math.log(gross)))
        return rets, excluded
