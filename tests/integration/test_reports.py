"""Reports (R1.7) over a replay registry built by the real pipeline."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

import remy_finance.cli as cli
from remy_finance.core import MarketState, Mode, OfficialClose
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.registry import EventKind, JournalEvent, Registry, registry_path
from remy_finance.reports.outcomes import Status, outcomes
from remy_finance.reports.render import cumulative_report, daily_report, trades_report
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from tests.unit.baselines import synthetic as S

EVAL = "e" * 40


def board() -> MarketState:
    chains = [S.chain(sym=s, half_spread="0.02", tick="0.01") for s in ("AAA", "BBB", "CCC")]
    thin = S.chain(sym="DDD", half_spread="0.02", tick="0.01", size=1)  # entries can't fill
    chains.append(thin)
    return chains[0].model_copy(
        update={
            "underlyings": ("AAA", "BBB", "CCC", "DDD"),
            "underlying_quotes": tuple(q for c in chains for q in c.underlying_quotes),
            "option_quotes": tuple(q for c in chains for q in c.option_quotes),
        }
    )


@pytest.fixture
def built(tmp_path: Path) -> Path:
    decision = board()
    execution = decision.model_copy(update={"as_of": decision.as_of + timedelta(minutes=5)})
    reg = Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY)
    p = Pipeline(
        reg,
        RunLogStore(tmp_path / "runs"),
        "abcdef0123",
        lambda: decision.as_of + timedelta(seconds=30),
    )
    p.recommend(decision)
    p.clock = lambda: execution.as_of + timedelta(minutes=1)
    p.fill_entries(decision, execution)
    closes = FixtureSource()
    t = session_close(S.EXPIRY)
    for sym, px in (("AAA", "104"), ("BBB", "96"), ("CCC", "100.2"), ("DDD", "100")):
        closes.add(
            OfficialClose(
                symbol=sym,
                session_date=S.EXPIRY,
                price=Decimal(px),
                event_time=t,
                public_time=et_at(S.EXPIRY, 17, 15),
                source="f",
            )
        )
    p.clock = lambda: et_at(S.EXPIRY, 18, 0)
    p.settle_expired(closes, et_at(S.EXPIRY, 18, 0))
    # one correction, to check corrected versus original P&L
    first = next(o for o in outcomes(reg) if o.status is Status.CLOSED)
    seq = reg.events(first.trade_id)[1][0]
    reg.add_event(
        JournalEvent(
            trade_id=first.trade_id,
            mode=Mode.REPLAY,
            kind=EventKind.CORRECTION,
            event_time=et_at(S.EXPIRY, 18, 0),
            recorded_at=et_at(S.EXPIRY, 18, 0),
            cash=Decimal("-1.00"),
            note="fee was misread",
            corrects_seq=seq,
        )
    )
    reg.close()
    return tmp_path


def test_outcomes_match_the_journal(built: Path) -> None:
    with Registry.open(registry_path(built, Mode.REPLAY)) as reg:
        outs = outcomes(reg)
        assert {o.status for o in outs} == {Status.CLOSED, Status.UNFILLED}
        for o in outs:
            cash = sum((e.cash for _, e in reg.events(o.trade_id)), Decimal(0))
            assert o.realized_pnl == cash
        corrected = [o for o in outs if o.corrections]
        assert len(corrected) == 1
        assert corrected[0].realized_pnl == corrected[0].realized_pnl_original - Decimal("1.00")
        unfilled = [o for o in outs if o.status is Status.UNFILLED]
        assert unfilled and all(o.underlying == "DDD" for o in unfilled)
        assert all(o.unfilled_reason == "INSUFFICIENT_SIZE" for o in unfilled)


def test_cumulative_report(built: Path) -> None:
    now = et_at(S.EXPIRY, 19, 0)
    with Registry.open(registry_path(built, Mode.REPLAY)) as reg:
        text = cumulative_report(reg, now, EVAL)
        outs = outcomes(reg)
    assert "# Cumulative report: REPLAY" in text
    assert "descriptive only; not a test" in text
    assert f"EVAL-1 at commit `{EVAL[:12]}`" in text
    assert "MKT-1 v2" in text and "RAND-1 v2" in text
    assert "INSUFFICIENT_SIZE" in text
    assert "## By structure type" in text
    for o in outs:
        st = o.rec.candidate.structure_type
        assert st is not None and f"| {o.engine_group} | {st.value} |" in text
    assert "p-value" not in text.lower().replace("no p-values", "")
    mkt = [o for o in outs if o.rec.engine == "MKT-1" and o.closed_filled]
    pnl = sum((o.realized_pnl for o in mkt), Decimal(0))
    assert f"${pnl:,.2f}" in text


def test_trades_and_daily_reports(built: Path) -> None:
    now = et_at(S.EXPIRY, 19, 0)
    runs = RunLogStore(built / "runs").all(Mode.REPLAY)
    with Registry.open(registry_path(built, Mode.REPLAY)) as reg:
        trades = trades_report(reg, now, EVAL)
        n = len(reg.recommendations())
        decided = daily_report(reg, S.AS_OF.date(), now, EVAL, runs)
        expiry = daily_report(reg, S.EXPIRY, now, EVAL, runs)
        settled = {
            e: sum(1 for o in outcomes(reg) if o.rec.engine == e and o.closed_filled)
            for e in ("MKT-1", "RAND-1")
        }
    assert trades.count("\n| 2026-11-06") == n
    assert "corrected x1" in trades
    assert "# Daily report 2026-11-06: REPLAY" in decided
    assert "| MKT-1 v2 | 4 |" in decided  # four recommendations decided that day
    for engine, n_settled in settled.items():  # settled and closed on expiry day
        row = next(ln for ln in expiry.splitlines() if ln.startswith(f"| {engine} v2 |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[8] == cells[9] == str(n_settled) and n_settled > 0


def test_report_cli(built: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(built))
    runner = CliRunner()
    out = built / "reports" / "cumulative.md"
    r = runner.invoke(cli.app, ["report", "cumulative", "--mode", "replay", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_text().startswith("# Cumulative report: REPLAY")
    assert runner.invoke(cli.app, ["report", "trades", "--mode", "replay"]).exit_code == 0
    r = runner.invoke(cli.app, ["report", "daily", "--mode", "replay", "--day", "2026-11-06"])
    assert r.exit_code == 0 and "null decisions" in r.output
    assert runner.invoke(cli.app, ["report", "trades"]).exit_code == 1  # no prospective registry


def test_drawdown_and_quantiles_stay_within_the_data(built: Path) -> None:
    from remy_finance.reports.render import _drawdown

    with Registry.open(registry_path(built, Mode.REPLAY)) as reg:
        outs = [o for o in outcomes(reg) if o.closed_filled]
        text = cumulative_report(reg, et_at(S.EXPIRY, 19, 0), EVAL)
    # every trade closes on expiry day: one step, so the drawdown is just the day's loss
    for engine in ("MKT-1", "RAND-1"):
        day_pnl = sum((o.realized_pnl for o in outs if o.rec.engine == engine), Decimal(0))
        assert _drawdown([o for o in outs if o.rec.engine == engine]) == max(-day_pnl, 0)
    worst = min(o.realized_return for o in outs if o.realized_return is not None)
    for line in text.splitlines():
        if line.startswith("| MKT-1 v2 | 4 | ") and " / " in line:
            low = float(line.split("|")[7].split("/")[0].strip().rstrip("%")) / 100
            assert low >= float(worst) - 1e-9
