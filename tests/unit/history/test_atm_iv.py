"""30-day ATM IV derivation (DR-0045) on synthetic Black-76 chains."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from remy_finance.history.atm_iv import atm_iv30, rows_from_vendor
from tests.unit.history.helpers import chain, rates, years

S = date(2026, 11, 2)


def test_flat_vol_is_recovered() -> None:
    res = atm_iv30(chain(S, 100.0, {14: 0.25, 45: 0.25}), 100.0, S, rates())
    assert res.iv30 == pytest.approx(0.25, abs=1e-6)
    assert len(res.used) == 2
    for e in res.used:
        assert e.forward == pytest.approx(100.0 / math.exp(-0.04 * e.years), rel=1e-9)


def test_term_structure_interpolates_total_variance_in_time() -> None:
    res = atm_iv30(chain(S, 100.0, {16: 0.20, 44: 0.30}), 100.0, S, rates())
    t1, t2, t = years(S, S + timedelta(days=16)), years(S, S + timedelta(days=44)), 30 / 365
    w = 0.04 * t1 + (0.09 * t2 - 0.04 * t1) * (t - t1) / (t2 - t1)
    assert res.iv30 == pytest.approx(math.sqrt(w / t), abs=1e-6)


def test_exact_30_days_is_used_alone_and_spreads_do_not_bias() -> None:
    res = atm_iv30(chain(S, 50.0, {30: 0.4, 60: 0.2}, half_spread=0.02), 50.0, S, rates())
    assert [e.dte for e in res.used] == [30]
    assert res.iv30 == pytest.approx(0.4, abs=1e-3)


def test_no_bracket_is_missing_with_a_reason() -> None:
    assert atm_iv30(chain(S, 100.0, {35: 0.2, 50: 0.2}), 100.0, S, rates()).reason == (
        "NO_BRACKET_30D"
    )
    assert atm_iv30(chain(S, 100.0, {3: 0.2, 200: 0.2}), 100.0, S, rates()).reason == (
        "NO_VALID_EXPIRATION"
    )
    no_rate = atm_iv30(chain(S, 100.0, {14: 0.2, 45: 0.2}), 100.0, S, {})
    assert no_rate.iv30 is None and set(no_rate.rejected.values()) == {"NO_RATE"}


def test_far_forward_and_thin_chain_are_rejected() -> None:
    far = atm_iv30(chain(S, 100.0, {14: 0.2, 45: 0.2}), 150.0, S, rates())  # stale spot
    assert far.iv30 is None and "FORWARD_FAR_FROM_SPOT" in far.rejected.values()
    thin = chain(S, 100.0, {14: 0.2, 45: 0.2}, strikes=(95.0, 105.0))
    assert atm_iv30(thin, 100.0, S, rates()).iv30 is None


def test_vendor_rows_are_parsed_and_malformed_rows_skipped() -> None:
    rows = rows_from_vendor(
        [
            {"expiration": "2026-12-18", "strike": "100", "right": "CALL", "bid": 1, "ask": 1.1},
            {"expiration": "2026-12-18", "strike": "100", "right": "P", "bid": 2, "ask": 2.2},
            {"strike": "100"},
        ]
    )
    assert [(r.call, r.mid) for r in rows] == [(True, 1.05), (False, 2.1)]
