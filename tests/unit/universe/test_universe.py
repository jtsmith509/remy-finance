"""R0.5 tests: UNIV-1 uses only data available at selection time, keeps names that later
delist, is deterministic, and stores write-once decisions with input hashes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from remy_finance.core import LookaheadError, OptionVolume
from remy_finance.data import FixtureSource
from remy_finance.data.thetadata import ThetaDataSource
from remy_finance.data.timeutil import et_at
from remy_finance.universe.selection import (
    UNIV_1,
    UniverseStore,
    first_session_of_month,
    select_universe,
    window_sessions,
)

SEL = first_session_of_month(2026, 11)  # 2026-11-02
WINDOW = window_sessions(SEL, 20)
AFTER = window_sessions(first_session_of_month(2026, 12), 20)  # November sessions: the "future"
RULE = UNIV_1.model_copy(update={"size": 2})


def vol(sym: str, d: date, n: int) -> OptionVolume:
    t = et_at(d, 17, 15)
    return OptionVolume(
        symbol=sym, session_date=d, contracts=n, event_time=t, public_time=t, source="fixture"
    )


def fixture() -> FixtureSource:
    recs: list[OptionVolume] = []
    for d in WINDOW:
        recs += [vol("AAA", d, 1000), vol("BBB", d, 500), vol("CCC", d, 800), vol("DEL", d, 900)]
    recs += [vol("THIN", d, 10_000) for d in WINDOW[:10]]  # too few sessions with data
    for d in AFTER:  # after selection: BBB explodes, DEL delists (no data at all)
        recs += [vol("AAA", d, 1000), vol("BBB", d, 10**9), vol("CCC", d, 800)]
    return FixtureSource(recs)


CANDS = ("AAA", "BBB", "CCC", "DEL", "THIN")


def test_selection_uses_only_prior_sessions_and_keeps_later_delisted() -> None:
    d = select_universe(fixture(), SEL, candidates=CANDS, rule=RULE)
    assert d.members == ("AAA", "DEL")  # DEL later delists but was liquid at selection time
    assert (d.window_start, d.window_end) == (WINDOW[0], WINDOW[-1]) and d.window_end < SEL
    assert d.as_of == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # 09:00 ET, before the open snapshot
    by = {s.symbol: s for s in d.scores}
    assert by["BBB"].rank == 4 and by["THIN"].eligible is False and "10 of 20" in by["THIN"].reason
    assert by["AAA"].rank == 1 and by["DEL"].rank == 2 and by["CCC"].rank == 3


def test_future_data_does_not_change_the_decision() -> None:
    without_future = FixtureSource(
        [r for r in fixture().records() if isinstance(r, OptionVolume) and r.session_date in WINDOW]
    )
    a = select_universe(fixture(), SEL, candidates=CANDS, rule=RULE)
    b = select_universe(without_future, SEL, candidates=CANDS, rule=RULE)
    assert a == b and a.inputs_hash == b.inputs_hash


class LeakyVolume(FixtureSource):
    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        t = et_at(SEL, 12)  # claims to be public after the 09:00 ET as_of
        return OptionVolume(
            symbol=underlying,
            session_date=session_date,
            contracts=1,
            event_time=t,
            public_time=t,
            source="x",
        )


def test_leaky_volume_source_raises() -> None:
    with pytest.raises(LookaheadError):
        select_universe(LeakyVolume(), SEL, candidates=CANDS, rule=RULE)


def test_deterministic_and_order_independent() -> None:
    a = select_universe(fixture(), SEL, candidates=CANDS, rule=RULE)
    b = select_universe(fixture(), SEL, candidates=tuple(reversed(CANDS)), rule=RULE)
    assert a.canonical_json() == b.canonical_json()


def test_store_is_write_once_and_membership_lookup(tmp_path: Path) -> None:
    store = UniverseStore(tmp_path)
    nov = select_universe(fixture(), SEL, candidates=CANDS, rule=RULE)
    store.write(nov)
    with pytest.raises(FileExistsError):
        store.write(nov)
    assert store.membership_for(date(2026, 11, 20)) == nov
    assert store.membership_for(date(2026, 10, 30)) is None
    assert store.membership_at(nov.as_of - timedelta(seconds=1)) is None
    assert store.membership_at(nov.as_of) == nov


def test_thetadata_option_volume_mapping() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.params["expiration"] == "*" and req.url.params["start_date"] == "20261030"
        rows = [
            {
                "contract": {
                    "symbol": "SPY",
                    "strike": 700,
                    "right": "CALL",
                    "expiration": "2026-11-20",
                },
                "data": [{"volume": 1200}],
            },
            {
                "contract": {
                    "symbol": "SPY",
                    "strike": 700,
                    "right": "PUT",
                    "expiration": "2026-11-20",
                },
                "data": [{"volume": 800}],
            },
        ]
        return httpx.Response(200, json={"response": rows})

    v = ThetaDataSource("http://t", transport=httpx.MockTransport(h)).option_volume(
        "SPY", date(2026, 10, 30)
    )
    assert v is not None and v.contracts == 2000
    assert v.public_time == datetime(2026, 10, 30, 21, 15, tzinfo=UTC)  # 17:15 EDT


def test_result_independent_of_concurrency_and_progress_reported() -> None:
    seen: list[tuple[str, int, int]] = []
    one = select_universe(fixture(), SEL, candidates=CANDS, rule=RULE, max_workers=1)
    four = select_universe(
        fixture(),
        SEL,
        candidates=CANDS,
        rule=RULE,
        max_workers=4,
        progress=lambda sym, done, total, n, w, secs: seen.append((sym, done, total)),
    )
    assert one.canonical_json() == four.canonical_json()
    assert sorted(s for s, _, _ in seen) == sorted(CANDS)
    assert [d for _, d, _ in seen] == [1, 2, 3, 4, 5] and {t for _, _, t in seen} == {5}
