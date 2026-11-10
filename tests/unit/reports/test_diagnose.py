"""MKT-1 diagnostics (no writes)."""

from __future__ import annotations

from remy_finance.baselines.mkt1 import fit_expiration
from remy_finance.reports.diagnose import diagnose_board
from tests.unit.baselines import synthetic as S


def test_diagnose_reports_groups_and_top_predictions() -> None:
    board = S.chain(half_spread="0.02", tick="0.01")
    text = diagnose_board(board, top=5)
    assert "candidates" in text and "scored" in text
    assert "LONG_STRANGLE" in text and ("inside" in text or "OUTSIDE" in text)
    assert text.count("\n  ") >= 5  # five top predictions listed


def test_model_records_the_fitted_strike_range() -> None:
    model, _ = fit_expiration(S.chain(half_spread="0.02", tick="0.01"), "XYZ", S.EXPIRY)
    assert model is not None and model.fit_strikes is not None
    lo, hi = model.fit_strikes
    assert 75 <= lo < hi <= 125
