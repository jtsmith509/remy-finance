"""ThetaDataSource: option/stock quotes and rates from a local Theta Terminal v3 (R0.1).

Verified behaviour this adapter relies on (docs/memos/R0.1-data-verification.md):
- ``option/at_time/quote`` with ``expiration=*`` needs ``start_date == end_date`` and
  ignores ``max_dte``; rows carry real quote timestamps (naive ET).
- ``stock/at_time/quote`` stamps rows with the *requested* time (flagged).
- ``interest_rate/history/eod`` returns percent, one row per calendar day, no publication
  time; ``public_time`` follows DR-0011 (next business day 09:30 ET).
- ThetaData has no dividend or corporate-action data (use PolygonSource, DR-0014).
JSON is parsed with ``parse_float=Decimal``: no price ever passes through a float.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    OptionContract,
    OptionQuote,
    OptionVolume,
    QualityFlag,
    RatePoint,
    Right,
    UnderlyingQuote,
)
from remy_finance.data.timeutil import et_at, et_date, et_naive_to_utc, et_time_of_day, next_session

NO_DATA = 472
RATE_TENORS = {
    "SOFR": "ON",
    "TREASURY_M1": "1M",
    "TREASURY_M3": "3M",
    "TREASURY_M6": "6M",
    "TREASURY_Y1": "1Y",
}


class ThetaDataError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"ThetaData error {status}: {message}")
        self.status = status


class UnsupportedDataError(NotImplementedError):
    """This source does not provide the requested data (route it to another source)."""


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _flatten(payload: Any) -> list[dict[str, Any]]:
    items = payload.get("response", []) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("data"), list):
            for d in item["data"]:
                rows.append({**(item.get("contract") or {}), **d})
        elif isinstance(item, dict):
            rows.append(item)
    return rows


def _right(value: str) -> Right:
    v = value.upper()
    if v in ("C", "CALL"):
        return Right.CALL
    if v in ("P", "PUT"):
        return Right.PUT
    raise ValueError(f"unknown right {value!r}")


class ThetaDataSource:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 300.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rate_symbols: Sequence[str] = ("SOFR",),
    ) -> None:
        url = base_url or os.environ.get("THETA_TERMINAL_URL", "http://127.0.0.1:25503")
        self._client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout, transport=transport)
        self._clock = clock
        self._rate_symbols = tuple(rate_symbols)

    def _get(self, path: str, **params: str) -> list[dict[str, Any]]:
        try:
            r = self._client.get(path, params={**params, "format": "json"})
        except httpx.HTTPError as e:
            raise ThetaDataError(None, f"Theta Terminal unreachable: {e}") from e
        if r.status_code == NO_DATA:
            return []
        if r.status_code != 200:
            raise ThetaDataError(r.status_code, r.text[:300])
        return _flatten(r.json(parse_float=Decimal))

    # -------------------------------------------------------------- mapping

    @staticmethod
    def _contract(underlying: str, row: dict[str, Any]) -> OptionContract:
        root = str(row["symbol"]).upper()
        standard = root == underlying and not root[-1].isdigit()
        return OptionContract.build(
            underlying,
            date.fromisoformat(str(row["expiration"])),
            Decimal(str(row["strike"])),
            _right(str(row["right"])),
            root=root,
            standard_deliverable=standard,
        )

    def _option_quote(
        self, underlying: str, row: dict[str, Any], source: str, now: datetime
    ) -> OptionQuote:
        return OptionQuote(
            contract=self._contract(underlying, row),
            bid=Decimal(str(row["bid"])),
            ask=Decimal(str(row["ask"])),
            bid_size=int(row["bid_size"]),
            ask_size=int(row["ask_size"]),
            quote_time=et_naive_to_utc(str(row["timestamp"])),
            ingest_time=now,
            source=source,
        )

    def _underlying_quote(
        self, symbol: str, row: dict[str, Any], source: str, now: datetime, *, request_time: bool
    ) -> UnderlyingQuote:
        return UnderlyingQuote(
            symbol=symbol,
            bid=Decimal(str(row["bid"])),
            ask=Decimal(str(row["ask"])),
            bid_size=int(row["bid_size"]),
            ask_size=int(row["ask_size"]),
            quote_time=et_naive_to_utc(str(row["timestamp"])),
            ingest_time=now,
            source=source,
            quality_flags=(QualityFlag.QUOTE_TIME_IS_REQUEST_TIME,) if request_time else (),
        )

    # ------------------------------------------------------------- protocol

    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        rows = self._get(
            "/v3/option/list/contracts/quote", symbol=underlying, date=_ymd(et_date(as_of))
        )
        return tuple(
            sorted((self._contract(underlying, r) for r in rows), key=lambda c: c.osi_symbol)
        )

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        session = et_date(ts)
        rows = self._get(
            "/v3/option/at_time/quote",
            symbol=underlying,
            expiration="*",
            start_date=_ymd(session),
            end_date=_ymd(session),
            time_of_day=et_time_of_day(ts),
        )
        now = self._clock()
        quotes = [
            self._option_quote(underlying, r, "thetadata:option/at_time/quote", now) for r in rows
        ]
        return tuple(q for q in quotes if et_date(q.quote_time) == session)

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None:
        session = et_date(ts)
        rows = self._get(
            "/v3/stock/at_time/quote",
            symbol=symbol,
            start_date=_ymd(session),
            end_date=_ymd(session),
            time_of_day=et_time_of_day(ts),
        )
        if not rows:
            return None
        return self._underlying_quote(
            symbol, rows[-1], "thetadata:stock/at_time/quote", self._clock(), request_time=True
        )

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        raise UnsupportedDataError("ThetaData has no dividend data (R0.1); use PolygonSource")

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        raise UnsupportedDataError(
            "ThetaData has no corporate-action data (R0.1); use PolygonSource"
        )

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]:
        end = et_date(known_as_of)
        out: list[RatePoint] = []
        now = self._clock()
        for sym in self._rate_symbols:
            rows = self._get(
                "/v3/interest_rate/history/eod",
                symbol=sym,
                start_date=_ymd(end - timedelta(days=14)),
                end_date=_ymd(end),
            )
            for r in rows:
                obs = date.fromisoformat(str(r["created"])[:10])
                if obs.weekday() >= 5:  # forward-filled weekend rows
                    continue
                public = et_at(next_session(obs), 9, 30)
                if public > known_as_of:
                    continue
                out.append(
                    RatePoint(
                        symbol=sym,
                        tenor=RATE_TENORS.get(sym, sym),
                        observation_date=obs,
                        rate=Decimal(str(r["rate"])) / 100,
                        event_time=et_at(obs, 17),
                        public_time=public,
                        ingest_time=now,
                        source="thetadata:interest_rate/history/eod",
                    )
                )
        return tuple(out)

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        """Sum of contract volume across the chain from the session's EOD report (17:15 ET)."""
        rows = self._get(
            "/v3/option/history/eod",
            symbol=underlying,
            expiration="*",
            start_date=_ymd(session_date),
            end_date=_ymd(session_date),
        )
        if not rows:
            return None
        published = et_at(session_date, 17, 15)
        return OptionVolume(
            symbol=underlying,
            session_date=session_date,
            contracts=sum(int(r.get("volume") or 0) for r in rows),
            event_time=published,
            public_time=published,
            ingest_time=self._clock(),
            source="thetadata:option/history/eod",
        )

    # ------------------------------------------------- live (prospective) only

    def live_chain_quotes(self, underlying: str) -> tuple[OptionQuote, ...]:
        """Latest NBBO for the full chain now. The caller sets ``as_of`` after this returns."""
        rows = self._get("/v3/option/snapshot/quote", symbol=underlying, expiration="*")
        now = self._clock()
        return tuple(
            self._option_quote(underlying, r, "thetadata:option/snapshot/quote", now) for r in rows
        )

    def live_underlying_quote(self, symbol: str) -> UnderlyingQuote | None:
        rows = self._get("/v3/stock/snapshot/quote", symbol=symbol)
        if not rows:
            return None
        return self._underlying_quote(
            symbol, rows[-1], "thetadata:stock/snapshot/quote", self._clock(), request_time=False
        )
