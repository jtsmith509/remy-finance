"""End to end on a synthetic replay board: MKT-1 and RAND-1 recommend, the paper engine
fills at the execution board and settles at expiration, and the registry verifies."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from remy_finance.baselines.common import underlyings_picked_today
from remy_finance.baselines.mkt1 import run_mkt1
from remy_finance.baselines.rand1 import run_rand1
from remy_finance.core import Mode, OfficialClose
from remy_finance.data.timeutil import et_date
from remy_finance.execution.engine import PaperEngine, TradeSummary, official_close_time
from remy_finance.movegen.generator import generate
from remy_finance.registry import EventKind, Registry, registry_path
from tests.unit.baselines import synthetic as S


def test_recommend_fill_settle(tmp_path: Path) -> None:
    decision = S.chain(half_spread="0.02", tick="0.01")
    execution = decision.model_copy(update={"as_of": decision.as_of + timedelta(minutes=5)})
    reg = Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY)
    engine = PaperEngine(reg)
    receipt = decision.as_of + timedelta(seconds=30)
    cands, _ = generate(decision)
    day = et_date(decision.as_of)

    trade_ids = []
    for run in (run_mkt1, run_rand1):
        engine_name = "MKT-1" if run is run_mkt1 else "RAND-1"
        recs, log = run(
            decision,
            cands,
            code_commit="abcdef0",
            receipt_time=receipt,
            excluded_underlyings=underlyings_picked_today(reg, engine_name, day),
        )
        assert len(recs) == 1 and log.selected == (recs[0].trade_id,)
        for r in recs:
            reg.add_recommendation(r)
            trade_ids.append(r.trade_id)
    # a second run the same day finds the underlying used: a logged null decision
    again, log = run_mkt1(
        decision,
        cands,
        code_commit="abcdef0",
        receipt_time=receipt,
        excluded_underlyings=underlyings_picked_today(reg, "MKT-1", day),
    )
    assert again == [] and log.selected == () and "0 of 5" in log.shortfall

    t = official_close_time(S.EXPIRY)
    close = OfficialClose(
        symbol="XYZ",
        session_date=S.EXPIRY,
        price=Decimal("101.37"),
        event_time=t,
        public_time=t + timedelta(hours=1),
        source="synthetic",
    )
    now = t + timedelta(hours=2)
    for tid in trade_ids:
        engine.submit(tid, now)
        engine.fill_entry(tid, execution, now)
        engine.settle(tid, close, now)
        events = [e for _, e in reg.events(tid)]
        assert events[-1].kind is EventKind.TRADE_CLOSED
        summary = TradeSummary.from_canonical_json(events[-1].evidence or "")
        assert summary.entry_filled
        assert summary.realized_pnl == sum((e.cash for e in events), Decimal(0))
    assert reg.verify().ok
