"""MassiveSource: Massive.com (formerly Polygon.io) behind the MarketDataSource protocol.

Scope (DR-0014; source split per data type: DR-0026, proposed). Massive supplies:
- **Dividends** (``/stocks/v1/dividends``): ``public_time`` = declaration date at 16:00 ET.
  A dividend without a declaration date is not usable; it is listed in ``skipped``.
- **Corporate actions:** splits (``/stocks/v1/splits``; forward, reverse, stock dividend),
  ticker changes (``/vX/reference/tickers/{id}/events``) and special cash dividends.
  Splits and ticker changes carry no announcement date, so ``public_time`` = effective date
  at 09:30 ET: later than the true announcement, which is the safe direction.
- **Underlying NBBO** (``/v3/quotes/{ticker}``, Stocks Advanced plan): the latest quote at
  or before ``ts`` on the same ET day, stamped with its SIP time (not the request time).
- **Treasury yields** (``/fed/v1/treasury-yields``): percent to fraction; ``public_time``
  per DR-0011 (next XNYS session, 09:30 ET).
Options data is not sourced from Massive (ThetaData, DR-0013): those methods raise.

The deprecated ``/v3/reference/dividends`` and ``/v3/reference/splits`` are not used.
The key comes from ``MASSIVE_API_KEY`` (``POLYGON_API_KEY`` still accepted), is sent as a
bearer token and is never logged. ``min_interval`` throttles requests (free plan: about
5 requests per minute); 429 responses are retried with backoff.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

import httpx

from remy_finance.core import (
    CorporateAction,
    CorporateActionType,
    DividendEvent,
    DividendType,
    OptionContract,
    OptionQuote,
    OptionVolume,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.data.thetadata import UnsupportedDataError
from remy_finance.data.timeutil import NEW_YORK, et_at, et_date, next_session
from remy_finance.secrets import secret

DEFAULT_BASE_URL = "https://api.massive.com"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

ORDINARY_DISTRIBUTIONS = {"recurring", "unknown"}  # special/supplemental/irregular => SPECIAL
SPLIT_TYPES = {
    "forward_split": CorporateActionType.SPLIT,
    "reverse_split": CorporateActionType.REVERSE_SPLIT,
}
# (RatePoint symbol, response field, tenor label)
DEFAULT_RATE_SERIES: tuple[tuple[str, str, str], ...] = (
    ("TREASURY_1M", "yield_1_month", "1M"),
    ("TREASURY_3M", "yield_3_month", "3M"),
)


LARGE_STOCK_DIVIDEND = Decimal("1.25")  # OCC adjusts >= 25% stock dividends like splits


def _split_type(kind: str, split_from: Decimal, split_to: Decimal) -> CorporateActionType:
    """Massive labels some splits as stock dividends (e.g. NVDA's 4-for-1 in July 2021).
    A stock dividend of 25% or more is treated as a split; smaller ones stay OTHER."""
    if kind in SPLIT_TYPES:
        return SPLIT_TYPES[kind]
    if (
        kind == "stock_dividend"
        and split_from > 0
        and split_to / split_from >= LARGE_STOCK_DIVIDEND
    ):
        return CorporateActionType.SPLIT
    return CorporateActionType.OTHER


class MassiveError(RuntimeError):
    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"Massive {status}: {message}" if status else f"Massive: {message}")
        self.status = status


class MassiveEntitlementError(MassiveError):
    """403: the endpoint is not included in the account's current plan."""


def ns_to_utc(ns: int) -> datetime:
    return EPOCH + timedelta(microseconds=int(ns) // 1000)


def utc_to_ns(ts: datetime) -> int:
    return (ts - EPOCH) // timedelta(microseconds=1) * 1000


class MassiveSource:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_pages: int = 50,
        min_interval: float = 0.0,
        max_retries: int = 3,
        rate_series: Sequence[tuple[str, str, str]] = DEFAULT_RATE_SERIES,
        sleep: Callable[[float], None] = time.sleep,
        include_ticker_events: bool = True,
    ) -> None:
        key = api_key or secret("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")
        if not key:
            raise MassiveError(None, "MASSIVE_API_KEY is not set")
        url = (
            base_url
            or os.environ.get("MASSIVE_BASE_URL")
            or os.environ.get("POLYGON_BASE_URL")
            or DEFAULT_BASE_URL
        )
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=60.0,
            transport=transport,
        )
        self._clock = clock
        self._max_pages = max_pages
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._rate_series = tuple(rate_series)
        self._include_ticker_events = include_ticker_events
        self._sleep = sleep
        self._last_request = 0.0
        self._div_cache: dict[str, tuple[DividendEvent, ...]] = {}
        self._action_cache: dict[str, tuple[CorporateAction, ...]] = {}
        self.skipped: list[str] = []
        self.schedule_declared: list[str] = []
        self.requests_made = 0

    # ---------------------------------------------------------------- HTTP

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            try:
                r = self._client.get(path, params=params)
            except httpx.HTTPError as e:
                raise MassiveError(None, f"unreachable: {e}") from e
            if r.status_code == 429 and attempt < self._max_retries:
                self._sleep(max(self._min_interval, 15.0) * (attempt + 1))
                continue
            if r.status_code == 403:
                raise MassiveEntitlementError(403, r.text[:300])
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise MassiveError(r.status_code, r.text[:300])
            return r.json(parse_float=Decimal)
        raise MassiveError(429, "rate limited after retries")

    def _paged(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, str] | None = params
        pages = 0
        while url is not None:
            if pages == self._max_pages:
                raise MassiveError(None, f"more than {self._max_pages} pages for {path}")
            payload = self._get(url, query)
            if payload is None:
                break
            rows.extend(payload.get("results") or [])
            url, query = payload.get("next_url"), None  # next_url carries its own cursor
            pages += 1
        return rows

    # ------------------------------------------------------------ dividends

    def _load_dividends(self, symbol: str) -> tuple[DividendEvent, ...]:
        if symbol not in self._div_cache:
            rows = self._paged(
                "/stocks/v1/dividends",
                {"ticker": symbol, "limit": "5000", "sort": "ex_dividend_date.asc"},
            )
            self._div_cache[symbol] = self._parse_dividends(symbol, rows)
        return self._div_cache[symbol]

    def _parse_dividends(
        self, symbol: str, rows: list[dict[str, Any]]
    ) -> tuple[DividendEvent, ...]:
        now = self._clock()
        out: list[DividendEvent] = []
        # A declaration date shared by several different ex-dates is a *schedule*
        # announcement (e.g. SPY's whole year declared on 2 Jan): the per-payment amount
        # is not known then. Such records become public on the ex-date (DR-0027).
        ex_by_decl: dict[str, set[str]] = {}
        for r in rows:
            if r.get("declaration_date"):
                ex_by_decl.setdefault(str(r["declaration_date"]), set()).add(
                    str(r.get("ex_dividend_date"))
                )
        for r in rows:
            ex = r.get("ex_dividend_date")
            decl = r.get("declaration_date")
            if not decl:
                self.skipped.append(f"{symbol} ex {ex}: no declaration_date")
                continue
            if str(r.get("currency", "USD")) != "USD":
                self.skipped.append(f"{symbol} ex {ex}: currency {r.get('currency')}")
                continue
            if len(ex_by_decl.get(str(decl), ())) > 1:
                public = et_at(date.fromisoformat(str(ex)), 9, 30)
                self.schedule_declared.append(f"{symbol} ex {ex}: schedule declared {decl}")
            else:
                public = et_at(date.fromisoformat(decl), 16)
            dist = str(r.get("distribution_type", "unknown"))
            out.append(
                DividendEvent(
                    symbol=symbol,
                    ex_date=date.fromisoformat(str(ex)),
                    amount=Decimal(str(r["cash_amount"])),
                    dividend_type=DividendType.ORDINARY
                    if dist in ORDINARY_DISTRIBUTIONS
                    else DividendType.SPECIAL,
                    declaration_date=date.fromisoformat(decl),
                    pay_date=date.fromisoformat(r["pay_date"]) if r.get("pay_date") else None,
                    event_time=public,
                    public_time=public,
                    ingest_time=now,
                    source="massive:stocks/v1/dividends",
                )
            )
        return tuple(sorted(out, key=lambda d: (d.ex_date, d.canonical_json())))

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        return tuple(d for d in self._load_dividends(symbol) if d.public_time <= known_as_of)

    # ----------------------------------------------------- corporate actions

    def _splits(self, symbol: str, now: datetime) -> list[CorporateAction]:
        rows = self._paged(
            "/stocks/v1/splits", {"ticker": symbol, "limit": "5000", "sort": "execution_date.asc"}
        )
        return self._parse_splits(symbol, rows, now)

    def _parse_splits(
        self, symbol: str, rows: list[dict[str, Any]], now: datetime
    ) -> list[CorporateAction]:
        out: list[CorporateAction] = []
        for r in rows:
            eff = date.fromisoformat(str(r["execution_date"]))
            kind = str(r.get("adjustment_type", ""))
            public = et_at(eff, 9, 30)
            out.append(
                CorporateAction(
                    symbol=symbol,
                    action_type=_split_type(
                        kind, Decimal(str(r["split_from"])), Decimal(str(r["split_to"]))
                    ),
                    effective_date=eff,
                    details=(
                        ("adjustment_type", kind),
                        ("split_from", str(r["split_from"])),
                        ("split_to", str(r["split_to"])),
                    ),
                    event_time=public,
                    public_time=public,
                    ingest_time=now,
                    source="massive:stocks/v1/splits",
                )
            )
        return out

    def _ticker_changes(self, symbol: str, now: datetime) -> list[CorporateAction]:
        payload = self._get(f"/vX/reference/tickers/{symbol}/events", {"types": "ticker_change"})
        events = ((payload or {}).get("results") or {}).get("events") or []
        changes = sorted(
            (str(e["date"]), str((e.get("ticker_change") or {}).get("ticker", "")))
            for e in events
            if e.get("type") == "ticker_change"
        )
        out: list[CorporateAction] = []
        # The earliest event is the ticker the asset started with in Massive's records,
        # not a change; a change is an event whose ticker differs from the one before.
        for (_, previous), (when, ticker) in pairwise(changes):
            if ticker == previous:
                continue
            eff = date.fromisoformat(when)
            public = et_at(eff, 9, 30)
            out.append(
                CorporateAction(
                    symbol=symbol,
                    action_type=CorporateActionType.SYMBOL_CHANGE,
                    effective_date=eff,
                    details=(("from_ticker", previous), ("to_ticker", ticker)),
                    event_time=public,
                    public_time=public,
                    ingest_time=now,
                    source="massive:vX/reference/tickers/events",
                )
            )
        return out

    def _load_actions(self, symbol: str) -> tuple[CorporateAction, ...]:
        if symbol not in self._action_cache:
            now = self._clock()
            actions = self._splits(symbol, now)  # a splits failure raises
            actions += self._events_if_enabled(symbol, now)
            self._action_cache[symbol] = tuple(sorted(actions, key=lambda a: a.canonical_json()))
        return self._action_cache[symbol]

    def _events_if_enabled(self, symbol: str, now: datetime) -> list[CorporateAction]:
        if not self._include_ticker_events:
            return []
        try:
            return self._ticker_changes(symbol, now)
        except MassiveError as e:
            self.skipped.append(f"{symbol}: ticker events unavailable ({e})")
            return []

    def prefetch(self, symbols: Sequence[str], chunk: int = 25) -> None:
        """Load dividends and splits for many symbols in a few batched requests.

        Uses ``ticker.any_of`` (up to ``chunk`` tickers per request) so a 50-name board needs
        about 4 requests instead of about 150; on the free plan (about 5 requests a minute)
        that is the difference between under a minute and about 30 minutes. Later
        ``dividends()``/``corporate_actions()`` calls for these symbols are served from cache.
        Ticker events have no batched form; they are fetched per symbol only when
        ``include_ticker_events`` is on.
        """
        wanted = sorted(
            {s for s in symbols if s not in self._div_cache or s not in self._action_cache}
        )
        for i in range(0, len(wanted), chunk):
            group = wanted[i : i + chunk]
            joined = ",".join(group)
            now = self._clock()
            div_rows = self._paged(
                "/stocks/v1/dividends",
                {"ticker.any_of": joined, "limit": "5000", "sort": "ex_dividend_date.asc"},
            )
            split_rows = self._paged(
                "/stocks/v1/splits",
                {"ticker.any_of": joined, "limit": "5000", "sort": "execution_date.asc"},
            )
            for sym in group:
                mine = [r for r in div_rows if str(r.get("ticker")) == sym]
                self._div_cache[sym] = self._parse_dividends(sym, mine)
                splits = self._parse_splits(
                    sym, [r for r in split_rows if str(r.get("ticker")) == sym], now
                )
                actions = splits + self._events_if_enabled(sym, now)
                self._action_cache[sym] = tuple(sorted(actions, key=lambda a: a.canonical_json()))

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        """Splits and ticker changes, plus special cash dividends as SPECIAL_DIVIDEND actions.

        The feeds are independent: a dividend-feed or events failure keeps the splits (and
        is logged in ``skipped``); a splits failure raises.
        """
        actions = [a for a in self._load_actions(symbol) if a.public_time <= known_as_of]
        try:
            divs = self.dividends(symbol, known_as_of)
        except MassiveError as e:
            self.skipped.append(f"{symbol}: special dividends unavailable ({e})")
            return tuple(actions)
        specials = [
            CorporateAction(
                symbol=symbol,
                action_type=CorporateActionType.SPECIAL_DIVIDEND,
                effective_date=d.ex_date,
                details=(("amount", str(d.amount)),),
                event_time=d.event_time,
                public_time=d.public_time,
                ingest_time=d.ingest_time,
                source=d.source,
            )
            for d in divs
            if d.dividend_type is DividendType.SPECIAL
        ]
        return tuple(actions + specials)

    # --------------------------------------------------------- underlying NBBO

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None:
        """Latest NBBO at or before ``ts`` on the same ET calendar day (SIP-stamped)."""
        day = et_date(ts)
        start = datetime(day.year, day.month, day.day, tzinfo=NEW_YORK).astimezone(UTC)
        payload = self._get(
            f"/v3/quotes/{symbol}",
            {
                "timestamp.gte": str(utc_to_ns(start)),
                "timestamp.lte": str(utc_to_ns(ts)),
                "order": "desc",
                "sort": "timestamp",
                "limit": "1",
            },
        )
        rows = (payload or {}).get("results") or []
        if not rows:
            return None
        r = rows[0]
        return UnderlyingQuote(
            symbol=symbol,
            bid=Decimal(str(r["bid_price"])),
            ask=Decimal(str(r["ask_price"])),
            bid_size=int(Decimal(str(r.get("bid_size", 0)))),
            ask_size=int(Decimal(str(r.get("ask_size", 0)))),
            quote_time=ns_to_utc(int(r["sip_timestamp"])),
            ingest_time=self._clock(),
            source="massive:v3/quotes",
        )

    # ------------------------------------------------------------- rates

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]:
        end = et_date(known_as_of)
        rows = self._paged(
            "/fed/v1/treasury-yields",
            {
                "date.gte": (end - timedelta(days=14)).isoformat(),
                "date.lte": end.isoformat(),
                "sort": "date.asc",
                "limit": "100",
            },
        )
        now = self._clock()
        out: list[RatePoint] = []
        for r in rows:
            obs = date.fromisoformat(str(r["date"]))
            if obs.weekday() >= 5:
                continue
            public = et_at(next_session(obs), 9, 30)
            if public > known_as_of:
                continue
            for symbol, field, tenor in self._rate_series:
                if r.get(field) is None:
                    continue
                out.append(
                    RatePoint(
                        symbol=symbol,
                        tenor=tenor,
                        observation_date=obs,
                        rate=Decimal(str(r[field])) / 100,
                        event_time=et_at(obs, 17),
                        public_time=public,
                        ingest_time=now,
                        source="massive:fed/v1/treasury-yields",
                    )
                )
        return tuple(out)

    # ------------------------------------------- not sourced from Massive

    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        raise UnsupportedDataError("options come from ThetaData (DR-0013), not Massive")

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        raise UnsupportedDataError("options come from ThetaData (DR-0013), not Massive")

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        raise UnsupportedDataError("options come from ThetaData (DR-0013), not Massive")

    # ---------------------- calendar and close checks (verification use only)

    def upcoming_holidays(self) -> list[dict[str, Any]]:
        """Forward-looking only (Massive does not serve historical holidays)."""
        payload = self._get("/v1/marketstatus/upcoming")
        return list(payload or [])

    def market_status(self) -> dict[str, Any]:
        return dict(self._get("/v1/marketstatus/now") or {})

    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, Decimal]:
        payload = self._get(
            f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
            {"adjusted": "false", "sort": "asc", "limit": "5000"},
        )
        out: dict[date, Decimal] = {}
        for bar in (payload or {}).get("results") or []:
            out[ns_to_utc(int(bar["t"]) * 1_000_000).astimezone(NEW_YORK).date()] = Decimal(
                str(bar["c"])
            )
        return out
