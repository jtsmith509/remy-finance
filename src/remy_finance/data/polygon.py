"""PolygonSource: dividends and splits from Polygon.io / Massive (DR-0014).

- Dividend ``public_time`` = declaration date at 16:00 ET. A dividend without a
  declaration date is not usable; it is recorded in ``skipped`` (never silently used).
- Splits have no announcement date in this feed, so ``public_time`` = execution date at
  09:30 ET: later than the true announcement, which is the safe direction.
- Special cash dividends are also reported as ``SPECIAL_DIVIDEND`` corporate actions.
The API key comes from ``POLYGON_API_KEY`` and is sent as a bearer token, never logged.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from remy_finance.core import CorporateAction, CorporateActionType, DividendEvent, DividendType
from remy_finance.data.timeutil import et_at

ORDINARY_CODES = {"CD"}  # Polygon dividend_type: CD regular; SC special; LT/ST capital gains


class PolygonError(RuntimeError):
    pass


class PolygonSource:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_pages: int = 50,
    ) -> None:
        key = api_key or os.environ.get("POLYGON_API_KEY")
        if not key:
            raise PolygonError("POLYGON_API_KEY is not set")
        url = base_url or os.environ.get("POLYGON_BASE_URL", "https://api.polygon.io")
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=60.0,
            transport=transport,
        )
        self._clock = clock
        self._max_pages = max_pages
        self._div_cache: dict[str, tuple[DividendEvent, ...]] = {}
        self._split_cache: dict[str, tuple[CorporateAction, ...]] = {}
        self.skipped: list[str] = []

    def _paged(self, path: str, **params: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, str] | None = {**params, "limit": "1000"}
        pages = 0
        while url is not None:
            if pages == self._max_pages:
                raise PolygonError(f"more than {self._max_pages} pages for {path}")
            r = self._client.get(url, params=query)
            if r.status_code != 200:
                raise PolygonError(f"Polygon {r.status_code} for {path}: {r.text[:200]}")
            payload = r.json(parse_float=Decimal)
            rows.extend(payload.get("results") or [])
            url, query = payload.get("next_url"), None  # next_url carries its own cursor
            pages += 1
        return rows

    def _load_dividends(self, symbol: str) -> tuple[DividendEvent, ...]:
        if symbol not in self._div_cache:
            now = self._clock()
            out: list[DividendEvent] = []
            for r in self._paged("/v3/reference/dividends", ticker=symbol):
                decl = r.get("declaration_date")
                if not decl:
                    self.skipped.append(
                        f"{symbol} ex {r.get('ex_dividend_date')}: no declaration_date"
                    )
                    continue
                public = et_at(date.fromisoformat(decl), 16)
                code = str(r.get("dividend_type", "CD"))
                out.append(
                    DividendEvent(
                        symbol=symbol,
                        ex_date=date.fromisoformat(r["ex_dividend_date"]),
                        amount=Decimal(str(r["cash_amount"])),
                        dividend_type=DividendType.ORDINARY
                        if code in ORDINARY_CODES
                        else DividendType.SPECIAL,
                        declaration_date=date.fromisoformat(decl),
                        pay_date=date.fromisoformat(r["pay_date"]) if r.get("pay_date") else None,
                        event_time=public,
                        public_time=public,
                        ingest_time=now,
                        source="polygon:v3/reference/dividends",
                    )
                )
            self._div_cache[symbol] = tuple(
                sorted(out, key=lambda d: (d.ex_date, d.canonical_json()))
            )
        return self._div_cache[symbol]

    def _load_splits(self, symbol: str) -> tuple[CorporateAction, ...]:
        if symbol not in self._split_cache:
            now = self._clock()
            out: list[CorporateAction] = []
            for r in self._paged("/v3/reference/splits", ticker=symbol):
                eff = date.fromisoformat(r["execution_date"])
                sf, st = Decimal(str(r["split_from"])), Decimal(str(r["split_to"]))
                public = et_at(eff, 9, 30)
                out.append(
                    CorporateAction(
                        symbol=symbol,
                        action_type=CorporateActionType.SPLIT
                        if st >= sf
                        else CorporateActionType.REVERSE_SPLIT,
                        effective_date=eff,
                        details=(("split_from", str(sf)), ("split_to", str(st))),
                        event_time=public,
                        public_time=public,
                        ingest_time=now,
                        source="polygon:v3/reference/splits",
                    )
                )
            self._split_cache[symbol] = tuple(sorted(out, key=lambda a: a.canonical_json()))
        return self._split_cache[symbol]

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        return tuple(d for d in self._load_dividends(symbol) if d.public_time <= known_as_of)

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        splits = [a for a in self._load_splits(symbol) if a.public_time <= known_as_of]
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
            for d in self.dividends(symbol, known_as_of)
            if d.dividend_type is DividendType.SPECIAL
        ]
        return tuple(splits + specials)
