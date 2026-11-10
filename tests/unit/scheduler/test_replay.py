"""Replay driver (R1.8) and the measured power inputs, on synthetic replay boards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import MarketState, Mode, OfficialClose, Session
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.registry import Registry, registry_path
from remy_finance.reports.measured import design_effect, measure, measured_report
from remy_finance.reports.outcomes import Status, outcomes
from remy_finance.scheduler.replay import ReplayDriver
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time
from remy_finance.universe.selection import UniverseDecision, UniverseStore
from tests.unit.baselines import synthetic as S

SYMS = ("AAA", "BBB", "CCC")
START, END = date(2026, 11, 9), date(2026, 11, 11)


def build(
    d: date, session: Session, kind: SnapshotKind, symbols: Sequence[str], version: str
) -> MarketState:
    chains = [S.chain(sym=s, half_spread="0.02", tick="0.01") for s in symbols]
    return chains[0].model_copy(
        update={
            "as_of": snapshot_time(d, session, kind),
            "mode": Mode.REPLAY,
            "session": session,
            "universe_version": version,
            "underlyings": tuple(symbols),
            "underlying_quotes": tuple(q for c in chains for q in c.underlying_quotes),
            "option_quotes": tuple(q for c in chains for q in c.option_quotes),
        }
    )


@pytest.fixture
def data(tmp_path: Path) -> Path:
    UniverseStore(tmp_path / "universe").write(
        UniverseDecision(
            rule_version="UNIV-1",
            candidate_list_version="T",
            selection_session=date(2026, 11, 2),
            as_of=et_at(date(2026, 11, 2), 9, 0),
            window_start=date(2026, 10, 2),
            window_end=date(2026, 10, 30),
            scores=(),
            members=SYMS,
            inputs_hash="0" * 64,
        )
    )
    return tmp_path


def closes() -> FixtureSource:
    src = FixtureSource()
    t = session_close(S.EXPIRY)
    for sym, px in zip(SYMS, ("104", "96", "100.2"), strict=True):
        src.add(
            OfficialClose(
                symbol=sym,
                session_date=S.EXPIRY,
                price=Decimal(px),
                event_time=t,
                public_time=et_at(S.EXPIRY, 17, 15),
                source="f",
            )
        )
    return src


def driver(data: Path, **kw: object) -> ReplayDriver:
    return ReplayDriver(
        data_dir=data,
        build=build,
        closes=closes(),
        code_commit="abcdef0123",
        log=lambda _: None,
        **kw,
    )  # type: ignore[arg-type]


def test_replay_month_runs_end_to_end_and_settles_everything(data: Path) -> None:
    s = driver(data).run(START, END)
    assert s.sessions == 3
    assert s.recommended == 3 * 2 * 3  # 3 days x 2 baselines x 3 names (cap: none at the close)
    assert s.boards_built >= 12 and s.still_open == 0
    assert s.stopped_at == S.EXPIRY
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        outs = outcomes(reg)
        assert reg.verify().ok
    assert {o.status for o in outs} <= {Status.CLOSED, Status.UNFILLED}
    by_exit = [o for o in outs if o.closed_filled and o.assignments]
    assert by_exit, "a deep ITM short leg on the static chain is assumed assigned (ASSIGN-1)"
    assert all(o.forced_exits == 1 for o in by_exit)
    assert s.settled == sum(1 for o in outs if o.closed_filled) - len(by_exit)
    for o in outs:  # simulated receipt: inside the decision-to-execution gap, never late
        assert o.rec.decision_time < o.rec.receipt_time < o.rec.decision_time.replace(minute=5)
        assert o.unfilled_reason != "recorded after the execution quote was taken"


def test_replay_is_resumable_and_idempotent(data: Path) -> None:
    driver(data).run(START, END)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        before = (reg.head("recommendations"), reg.head("journal"))
    again = driver(data).run(START, END)
    assert again.boards_built == 0 and again.boards_reused > 0
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        assert (reg.head("recommendations"), reg.head("journal")) == before


def test_tail_stops_when_data_runs_out(data: Path) -> None:
    s = driver(data, last_day=date(2026, 11, 17)).run(START, END)
    assert s.still_open > 0 and any("no data after" in n for n in s.notes)
    assert s.stopped_at == date(2026, 11, 17)


def test_missing_universe_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="universe build --month 2026-11"):
        driver(tmp_path).run(START, END)


def test_measured_power_inputs(data: Path) -> None:
    driver(data).run(START, END)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        outs = outcomes(reg)
    mkt = [o for o in outs if o.rec.engine == "MKT-1"]
    m = measure("MKT-1 v1", mkt, trading_days=3)
    assert m.recs_per_day == pytest.approx(3.0)
    assert m.fill_rate is not None and 0 < m.fill_rate <= 1
    assert m.sigma is not None and m.sigma > 0
    text = measured_report(outs, 3, "REPLAY")
    assert "Planning inputs only; not evidence of edge" in text
    assert "| MKT-1 v1 |" in text and "| prior (power-estimate.md) |" in text


def test_design_effect() -> None:
    deff, k = design_effect([1.0, 1.0, -1.0, -1.0], ["a", "a", "b", "b"])
    assert k == 2 and deff == pytest.approx(3.0)
    assert design_effect([1.0, 2.0, 3.0], ["a", "a", "a"]) == (None, 1)
    deff, _ = design_effect([1.0, -1.0, 1.0, -1.0], ["a", "a", "b", "b"])
    assert deff == pytest.approx(0.0)


def test_replay_bench_times_a_stored_board_and_writes_nothing(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import remy_finance.cli as cli
    from remy_finance.snapshots.store import SnapshotStore

    monkeypatch.setenv("REMY_DATA_DIR", str(data))
    board = build(START, Session.OPEN, SnapshotKind.DECISION, SYMS, "T")
    SnapshotStore(data / "snapshots").write(board)
    r = CliRunner().invoke(cli.app, ["replay", "bench", "--date", START.isoformat()])
    assert r.exit_code == 0, r.output
    assert "movegen" in r.output and "LONG_STRANGLE" in r.output and "OK:" in r.output
    assert not (data / "registry").exists() and not (data / "runs").exists()
    r = CliRunner().invoke(cli.app, ["replay", "bench", "--date", "2026-08-03"])
    assert r.exit_code == 1 and "no stored REPLAY decision board" in r.output
