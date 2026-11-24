"""VRP estimator (DR-0049) on synthetic HIST-1 data with a known premium."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from remy_finance.data.timeutil import et_at
from remy_finance.history import HistoryView, HistSeries, HistStore
from remy_finance.history.build import sessions_between
from remy_finance.vrp.estimator import (
    WINDOW,
    Row,
    build_rows,
    fit_ols,
    last_public_session,
    shrink_weight,
    window_start,
)
from remy_finance.vrp.model import VrpStore, board_ratio, fit_refit
from tests.unit.history.helpers import obs

START, END = date(2022, 11, 1), date(2026, 11, 20)
TRUE_A, TRUE_B = 0.2, 3.0  # ratio = a + b x SPYIV30
NAME_IV = 0.30


def spy_x(i: int) -> float:
    """A slowly varying SPYIV30 between 0.12 and 0.28 (a period of ~300 sessions)."""
    return 0.20 + 0.08 * math.sin(i / 48.0)


def make_store(
    path: Path,
    names: dict[str, tuple[float, float]] | None = None,
    seed: int = 7,
    end: date = END,
) -> HistStore:
    """SPY and each name: IV and closes. A name's day-d variance is ratio(x_d) IV^2 / 252,
    so the window's expected realized-over-implied ratio is about a + b x."""
    names = names or {"AAA": (TRUE_A, TRUE_B)}
    rng = np.random.default_rng(seed)
    store = HistStore(path)
    days = sessions_between(START, end)
    out = []
    for i, d in enumerate(days):
        out.append(obs(HistSeries.ATMIV30, "SPY", d, f"{spy_x(i):.6f}"))
        out.append(obs(HistSeries.CLOSE, "SPY", d, f"{400 * math.exp(0.0001 * i):.4f}"))
    for sym, (a, b) in names.items():
        px = 100.0
        for i, d in enumerate(days):
            if i:
                var = max(a + b * spy_x(i), 0.01) * NAME_IV**2 / 252
                px *= math.exp(math.sqrt(var) * rng.standard_normal())
            out.append(obs(HistSeries.CLOSE, sym, d, f"{px:.6f}"))
            out.append(obs(HistSeries.ATMIV30, sym, d, f"{NAME_IV:.6f}"))
    store.add_observations(out)
    return store


CUTOFF = et_at(END, 17, 30)


def test_recovers_a_known_premium(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    rows = build_rows(HistoryView(store, CUTOFF), "AAA")
    fit = fit_ols(rows.rows)
    assert fit is not None
    assert fit.a == pytest.approx(TRUE_A, abs=0.15) and fit.b == pytest.approx(TRUE_B, abs=0.8)
    assert fit.se_b > 0 and fit.n_eff == pytest.approx(len(rows.rows) / WINDOW)
    # About 3 years of response dates, each row's window wholly before the cutoff.
    assert 700 <= len(rows.rows) <= 760
    assert max(r.response for r in rows.rows) == END
    assert min(r.response for r in rows.rows) >= date(2023, 11, 20)


def test_rows_never_use_data_after_the_cutoff(tmp_path: Path) -> None:
    """B02: changing every return after the cutoff leaves the fit unchanged."""
    cutoff = et_at(date(2025, 6, 13), 17, 30)
    a = make_store(tmp_path / "a.sqlite", seed=7)
    b = make_store(tmp_path / "b.sqlite", seed=7)
    late = [o for o in b.observations(HistSeries.CLOSE, "AAA") if o.session > date(2025, 6, 13)]
    rows_a = build_rows(HistoryView(a, cutoff), "AAA").rows
    # Build a second store identical up to the cutoff, with different returns after it.
    d = HistStore(tmp_path / "d.sqlite")
    keep = [o for s in ("AAA", "SPY") for series in (HistSeries.CLOSE, HistSeries.ATMIV30)
            for o in a.observations(series, s) if o.session <= date(2025, 6, 13)]  # fmt: skip
    changed = [o.model_copy(update={"value": o.value * 2}) for o in late]
    d.add_observations(keep + changed)
    rows_d = build_rows(HistoryView(d, cutoff), "AAA").rows
    assert rows_a == rows_d and rows_a
    assert fit_ols(rows_a) == fit_ols(rows_d)
    assert all(r.response <= date(2025, 6, 13) for r in rows_a)


def test_a_row_waits_for_its_response_close_to_be_public(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    before = build_rows(HistoryView(store, et_at(END, 17, 14)), "AAA").rows
    after = build_rows(HistoryView(store, et_at(END, 17, 15)), "AAA").rows
    assert last_public_session(et_at(END, 17, 14)) < END == last_public_session(et_at(END, 17, 15))
    assert max(r.response for r in after) == END > max(r.response for r in before)


def test_an_incomplete_window_drops_the_row(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    days = sessions_between(date(2026, 6, 1), date(2026, 6, 30))
    gone = days[10]
    path = tmp_path / "g.sqlite"
    g = HistStore(path)
    g.add_observations(
        o for s in ("AAA", "SPY") for series in (HistSeries.CLOSE, HistSeries.ATMIV30)
        for o in store.observations(series, s) if not (s == "AAA" and series is HistSeries.CLOSE
                                                       and o.session == gone)
    )  # fmt: skip
    full = build_rows(HistoryView(store, CUTOFF), "AAA")
    holed = build_rows(HistoryView(g, CUTOFF), "AAA")
    # A missing close breaks the returns of that day and the next: every window holding
    # either is dropped.
    assert holed.dropped.get("INCOMPLETE_WINDOW", 0) >= WINDOW
    assert len(full.rows) - len(holed.rows) == holed.dropped["INCOMPLETE_WINDOW"]


def test_fit_needs_enough_rows_and_varying_x() -> None:
    d0 = date(2026, 1, 2)
    few = [Row(d0 + timedelta(days=i), d0, 1.0, 0.1 + i * 0.001) for i in range(100)]
    assert fit_ols(few) is None  # under 12 effective windows
    flat = [Row(d0 + timedelta(days=i), d0, 1.0, 0.2) for i in range(400)]
    assert fit_ols(flat) is None


def test_shrinkage_fallbacks_and_the_board_ratio(tmp_path: Path) -> None:
    names = {"AAA": (0.2, 3.0), "BBB": (0.6, 1.0), "CCC": (0.4, 2.0)}
    store = make_store(tmp_path / "h.sqlite", names=names)
    # CCC loses its IV history: no own fit, so it takes its sector's.
    g = HistStore(tmp_path / "g.sqlite")
    g.add_observations(
        o for s in (*names, "SPY") for series in (HistSeries.CLOSE, HistSeries.ATMIV30)
        for o in store.observations(series, s)
        if not (s == "CCC" and series is HistSeries.ATMIV30)
    )  # fmt: skip
    sectors = {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK", "DDD": "XLE"}
    refit = fit_refit(
        g, [*names, "DDD", "SPY"], sectors, CUTOFF, datetime(2026, 11, 21, tzinfo=UTC)
    )
    aaa, ccc, ddd, spy = (refit.name(s) for s in ("AAA", "CCC", "DDD", "SPY"))
    assert aaa and ccc and ddd and spy
    assert aaa.source == "shrunk" and aaa.own and aaa.used and aaa.weight is not None
    n_eff = aaa.own.n_rows / WINDOW
    assert (
        float(aaa.weight)
        == pytest.approx(shrink_weight(n_eff), abs=1e-9)
        == pytest.approx(n_eff / (n_eff + 12))
    )
    sector = dict(refit.sectors)["XLK"]
    assert sector is not None
    expect_a = float(aaa.weight) * float(aaa.own.a) + (1 - float(aaa.weight)) * float(sector.a)
    assert float(aaa.used.a) == pytest.approx(expect_a, abs=1e-8)
    assert ccc.source == "sector" and ccc.own is None and ccc.used == sector
    assert ddd.source == "none" and ddd.used is None  # no data, and no XLE fit
    assert spy.source == "own" and spy.sector is None
    # On a board: SPYIV30 at the previous session's public EOD value, clipped.
    t = et_at(date(2026, 11, 23), 10)
    vs = VrpStore(tmp_path / "vrp")
    vs.write(refit)
    with pytest.raises(FileExistsError):
        vs.write(refit)  # write-once
    assert vs.for_decision(t) == refit
    assert vs.for_decision(CUTOFF - timedelta(seconds=1)) is None
    rho, note = board_ratio(vs.for_decision(t), g, "AAA", t)
    x = float(HistoryView(g, t).value(HistSeries.ATMIV30, "SPY", END) or 0)
    raw = float(aaa.used.a) + float(aaa.used.b) * x
    assert rho == pytest.approx(min(max(raw, 0.3), 1.5)) and "shrunk" in note
    assert board_ratio(refit, g, "DDD", t)[0] is None
    assert board_ratio(None, g, "AAA", t) == (None, "no VRP refit at or before the decision time")


def test_the_ratio_is_clipped_and_needs_a_public_spyiv30(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite", names={"HOT": (3.0, 5.0)})
    refit = fit_refit(store, ["HOT"], {"HOT": "XLK"}, CUTOFF, datetime(2026, 11, 21, tzinfo=UTC))
    hot = refit.name("HOT")
    assert hot and hot.own and float(hot.own.clipped_share) > 0.5
    t = et_at(date(2026, 11, 23), 10)
    assert board_ratio(refit, store, "HOT", t)[0] == 1.5
    # A Monday board reads Friday's SPYIV30; a board on a day whose previous session has
    # no SPYIV30 gets no ratio.
    late = et_at(date(2026, 12, 1), 10)
    rho, note = board_ratio(refit, store, "HOT", late)
    assert rho is None and "no public SPYIV30" in note


def test_cli_diagnose_prints_fits_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from remy_finance import cli
    from remy_finance.history import hist_path

    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    path = hist_path(tmp_path)
    path.parent.mkdir(parents=True)
    make_store(path)
    r = CliRunner().invoke(cli.app, ["vrp", "diagnose", "--cutoff", "2026-11-20",
                                     "--symbols", "AAA,SPY,ZZZ"])  # fmt: skip
    assert r.exit_code == 0, r.output
    lines = {
        line.split()[0]: line for line in r.output.splitlines() if line[:3] in ("AAA", "SPY", "ZZZ")
    }
    assert set(lines) == {"AAA", "SPY", "ZZZ"} and "too few mature rows" in lines["ZZZ"]
    assert "median" in r.output and ">1%" in r.output
    assert not (tmp_path / "vrp").exists()


# ------------------------------------------------------------ review regressions (VRP)

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def _copy(src: HistStore, path: Path, syms: tuple[str, ...], keep=lambda s, series, o: True):  # type: ignore[no-untyped-def]
    g = HistStore(path)
    g.add_observations(
        o for s in syms for series in (HistSeries.CLOSE, HistSeries.ATMIV30)
        for o in src.observations(series, s) if keep(s, series, o)
    )  # fmt: skip
    return g


def test_row_is_252_mean_square_of_the_21_returns_after_s(tmp_path: Path) -> None:
    """TESTS-2: window = sessions s+1..s+21, response = s+21, divided by 21."""
    store = make_store(tmp_path / "h.sqlite")
    view = HistoryView(store, CUTOFF)
    rows = build_rows(view, "AAA").rows
    days = sessions_between(date(2022, 11, 1), END)
    rets, _ = view.total_returns("AAA", days[0], END)
    r = {x.session: x.log_return for x in rets}
    for row in rows[::97]:
        i = days.index(row.session)
        assert row.response == days[i + WINDOW]
        win = days[i + 1 : i + WINDOW + 1]
        assert row.ratio == pytest.approx(
            252 * sum(r[d] ** 2 for d in win) / WINDOW / 0.09, rel=1e-12
        )


def test_window_is_on_response_dates_three_years_inclusive(tmp_path: Path) -> None:
    rows = build_rows(HistoryView(make_store(tmp_path / "h.sqlite"), CUTOFF), "AAA").rows
    assert window_start(CUTOFF) == date(2023, 11, 20)
    assert min(r.response for r in rows) == date(2023, 11, 20)
    assert min(r.session for r in rows) < date(2023, 11, 20)


def _nw_reference(rows: list[Row], lag: int) -> tuple[float, float]:
    x = np.array([r.x for r in rows])
    y = np.array([r.ratio for r in rows])
    X = np.column_stack([np.ones_like(x), x])
    inv = np.linalg.inv(X.T @ X)
    u = y - X @ (inv @ X.T @ y)
    dates = sorted({r.session for r in rows})
    g = np.array([sum(X[k] * u[k] for k, r in enumerate(rows) if r.session == d) for d in dates])
    s = sum(np.outer(g[t], g[t]) for t in range(len(g)))
    for j in range(1, lag + 1):
        w = 1 - j / (lag + 1)
        for t in range(j, len(g)):
            s = s + w * (np.outer(g[t], g[t - j]) + np.outer(g[t - j], g[t]))
    cov = inv @ s @ inv
    return math.sqrt(cov[0, 0]), math.sqrt(cov[1, 1])


def test_newey_west_matches_a_reference_and_sums_per_date() -> None:
    """TESTS-1: Bartlett weights, lag 21, scores summed per date (pooled sectors)."""
    rng = np.random.default_rng(3)
    d0 = date(2024, 1, 1)
    rows = []
    for i in range(400):
        x = 0.2 + 0.05 * math.sin(i / 30)
        e = 0.0
        for _ in range(2):
            e = 0.8 * e + rng.normal(0, 0.1)
            rows.append(Row(d0 + timedelta(days=i), d0, 0.5 + 2 * x + e + 0.3 * math.sin(i / 7), x))
    fit = fit_ols(rows)
    assert fit is not None
    se_a, se_b = _nw_reference(rows, WINDOW)
    assert fit.se_a == pytest.approx(se_a, rel=1e-9) and fit.se_b == pytest.approx(se_b, rel=1e-9)


def test_the_sector_pools_only_names_with_three_years(tmp_path: Path) -> None:
    """SPEC-3: CCC listed in 2024: left out of XLK's pool, recorded, and hashed."""
    names = {"AAA": (0.2, 3.0), "BBB": (0.6, 1.0), "CCC": (1.2, -1.0)}
    store = make_store(tmp_path / "h.sqlite", names=names)
    g = _copy(store, tmp_path / "g.sqlite", (*names, "SPY"),
              lambda s, series, o: not (s == "CCC" and o.session < date(2024, 6, 3)))  # fmt: skip
    sectors = {n: "XLK" for n in names}
    refit = fit_refit(g, list(names), sectors, CUTOFF, T0)
    assert dict(refit.sector_members)["XLK"] == ("AAA", "BBB")
    assert refit.short_history == ("CCC",)
    only_ab = fit_refit(g, ["AAA", "BBB"], {"AAA": "XLK", "BBB": "XLK"}, CUTOFF, T0)
    assert dict(refit.sectors)["XLK"] == dict(only_ab.sectors)["XLK"]
    ccc = refit.name("CCC")
    assert ccc and ccc.source == "shrunk"  # its own rows still fit; only the pool excludes it


def test_shrunk_lines_shrink_b_and_carry_their_own_clipping(tmp_path: Path) -> None:
    """TESTS-7, STATS-1: both coefficients; the applied line's clip share; no SEs."""
    names = {"AAA": (0.6, 3.0), "BBB": (5.0, 0.0)}
    store = make_store(tmp_path / "h.sqlite", names=names)
    refit = fit_refit(store, list(names), {n: "XLK" for n in names}, CUTOFF, T0)
    a, sec = refit.name("AAA"), dict(refit.sectors)["XLK"]
    assert a and a.own and a.used and a.weight is not None and sec
    w = float(a.weight)
    assert float(a.used.b) == pytest.approx(w * float(a.own.b) + (1 - w) * float(sec.b), abs=1e-8)
    assert a.used.se_a is None and a.used.se_b is None
    rows = build_rows(HistoryView(store, CUTOFF), "AAA").rows
    line = [float(a.used.a) + float(a.used.b) * r.x for r in rows]
    share = sum(1 for v in line if v < 0.3 or v > 1.5) / len(rows)
    assert float(a.used.clipped_share) == pytest.approx(share, abs=1e-9)
    assert float(a.used.clipped_share) != float(a.own.clipped_share)
    assert 0 < float(refit.clipped_share) <= 1


def test_unmapped_names_get_no_ratio_and_spdrs_their_own(tmp_path: Path) -> None:
    """SPEC-4."""
    store = make_store(tmp_path / "h.sqlite", names={"AAA": (0.2, 3.0), "XLK": (0.3, 2.0)})
    refit = fit_refit(store, ["AAA", "XLK", "SPY"], {}, CUTOFF, T0)
    aaa, xlk, spy = refit.name("AAA"), refit.name("XLK"), refit.name("SPY")
    assert aaa and aaa.used is None and aaa.own and "not in the sector mapping" in aaa.reason
    assert xlk and xlk.source == "own" and spy and spy.source == "own"


def test_the_ratio_is_clipped_below_at_0_3(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite", names={"COLD": (0.02, 0.0)})
    refit = fit_refit(store, ["COLD"], {"COLD": "XLK"}, CUTOFF, T0)
    assert board_ratio(refit, store, "COLD", et_at(date(2026, 11, 23), 10))[0] == 0.3


def test_a_board_uses_the_latest_refit_at_or_before_its_decision(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    vs = VrpStore(tmp_path / "vrp")
    c1 = et_at(date(2026, 10, 30), 17, 30)
    r1, r2 = fit_refit(store, ["AAA"], {}, c1, T0), fit_refit(store, ["AAA"], {}, CUTOFF, T0)
    vs.write(r2)
    vs.write(r1)
    assert vs.for_decision(et_at(date(2026, 11, 23), 10)) == r2
    assert vs.for_decision(CUTOFF) == r2
    assert vs.for_decision(CUTOFF - timedelta(seconds=1)) == r1


def test_missing_spyiv30_drops_the_row(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    gone = set(sessions_between(date(2026, 3, 2), date(2026, 3, 13)))
    g = _copy(store, tmp_path / "g.sqlite", ("AAA", "SPY"),
              lambda s, series, o: not (s == "SPY" and series is HistSeries.ATMIV30
                                        and o.session in gone))  # fmt: skip
    rs = build_rows(HistoryView(g, CUTOFF), "AAA")
    assert rs.dropped.get("NO_SPYIV30") == len(gone)
    assert not any(r.session in gone for r in rs.rows)


def test_the_inputs_hash_covers_rows_mapping_and_pools(tmp_path: Path) -> None:
    """TESTS-9, PIT-01: reproducible, and sensitive to every input that sets the output."""
    names = {"AAA": (0.2, 3.0), "BBB": (0.6, 1.0)}
    store = make_store(tmp_path / "h.sqlite", names=names)
    h = fit_refit(store, ["AAA", "BBB"], {"AAA": "XLK", "BBB": "XLK"}, CUTOFF, T0).inputs_hash
    assert (
        h == fit_refit(store, ["AAA", "BBB"], {"AAA": "XLK", "BBB": "XLK"}, CUTOFF, T0).inputs_hash
    )
    assert h != fit_refit(store, ["AAA", "BBB"], {"AAA": "XLK"}, CUTOFF, T0).inputs_hash
    assert h != fit_refit(store, ["AAA", "BBB"], {}, CUTOFF, T0).inputs_hash
    other = make_store(tmp_path / "o.sqlite", names=names, seed=8)
    assert (
        h != fit_refit(other, ["AAA", "BBB"], {"AAA": "XLK", "BBB": "XLK"}, CUTOFF, T0).inputs_hash
    )


def test_own_fits_carry_the_raw_ratio_median_and_share_above_1(tmp_path: Path) -> None:
    store = make_store(tmp_path / "h.sqlite")
    rows = build_rows(HistoryView(store, CUTOFF), "AAA").rows
    fit = fit_ols(rows)
    assert fit is not None
    ratios = sorted(r.ratio for r in rows)
    assert fit.median_ratio == pytest.approx(float(np.median(ratios)))
    assert fit.share_above_1 == pytest.approx(sum(x > 1 for x in ratios) / len(ratios))
    entry = fit_refit(store, ["AAA"], {"AAA": "XLK"}, CUTOFF, T0).name("AAA")
    assert entry and entry.own and entry.own.median_ratio is not None
    assert entry.used and entry.used.median_ratio is None  # a shrunk line has none
