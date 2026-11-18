"""EVAL-1 §2.2 scores of MKT-1's S_T/F forecasts (Codex R1.6 F09, DR-0040)."""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import Mode, OfficialClose
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.registry import Registry, registry_path
from remy_finance.reports.render import cumulative_report
from remy_finance.reports.underlying import (
    DENSITY_FLOOR,
    density_at,
    score_fit,
    underlying_scores,
)
from remy_finance.scheduler.runlog import RunLogStore
from tests.integration.test_reports import built
from tests.unit.baselines import synthetic as S

PRICES = {"AAA": "104", "BBB": "96", "CCC": "100.2", "DDD": "100"}


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = built.__wrapped__(tmp_path_factory.mktemp("underlying"))  # type: ignore[attr-defined]
    return path


def closes(public_hour: int = 17) -> FixtureSource:
    src = FixtureSource()
    for sym, px in PRICES.items():
        src.add(
            OfficialClose(
                symbol=sym,
                session_date=S.EXPIRY,
                price=Decimal(px),
                event_time=session_close(S.EXPIRY),
                public_time=et_at(S.EXPIRY, public_hour, 15),
                source="f",
            )
        )
    return src


class Broken:
    def official_close(self, symbol: str, session_date: object) -> None:
        raise ConnectionError("terminal down")


def test_density_and_scores_from_the_quantile_grid() -> None:
    levels, values = [0.1, 0.5, 0.9], [0.9, 1.0, 1.2]
    assert density_at(levels, values, 0.95) == pytest.approx(0.4 / 0.1)
    assert density_at(levels, values, 1.1) == pytest.approx(0.4 / 0.2)
    assert density_at(levels, values, 1.5) == DENSITY_FLOOR  # beyond the grid
    assert density_at(levels, values, 0.5) == DENSITY_FLOOR
    c, ls = score_fit(levels, values, 1.0)
    assert ls == pytest.approx(-math.log(2.0))
    assert c == pytest.approx(2 * ((0 - 0.1) * (0.9 - 1.0) + 0 + (1 - 0.9) * (1.2 - 1.0)) / 3)


def test_runs_have_forecasts_and_each_fit_is_scored_once(data: Path) -> None:
    runs = RunLogStore(data / "runs").all(Mode.REPLAY)
    mkt = [r for r in runs if r.engine == "MKT-1"]
    fits = [f for r in mkt for f in r.fits if f.ok and f.rn_quantiles]
    assert fits
    res = underlying_scores(runs, closes(), et_at(S.EXPIRY, 19, 0))
    assert len(res.scores) == len(fits)  # RAND-1's copies are not counted
    for x in res.scores:
        fit = next(f for f in fits if f.symbol == x.symbol)
        assert x.realized == pytest.approx(float(Decimal(PRICES[x.symbol]) / fit.forward))


def test_no_lookahead_closes_must_be_public_and_expired(data: Path) -> None:
    runs = RunLogStore(data / "runs").all(Mode.REPLAY)
    before_public = underlying_scores(runs, closes(), et_at(S.EXPIRY, 17, 0))
    assert not before_public.scores and before_public.waiting > 0
    before_expiry = underlying_scores(runs, closes(), et_at(S.EXPIRY - timedelta(days=1), 19, 0))
    assert not before_expiry.scores and before_expiry.not_expired > 0
    broken = underlying_scores(runs, Broken(), et_at(S.EXPIRY, 19, 0))  # type: ignore[arg-type]
    assert not broken.scores and broken.unavailable > 0


def test_cumulative_report_shows_both_cohorts(data: Path) -> None:
    runs = RunLogStore(data / "runs").all(Mode.REPLAY)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        early = cumulative_report(reg, et_at(S.EXPIRY, 19, 0), "e" * 40, runs, closes())
        late = cumulative_report(
            reg, et_at(S.EXPIRY.replace(year=2027), 19, 0), "e" * 40, runs, closes()
        )
        none = cumulative_report(reg, et_at(S.EXPIRY, 19, 0), "e" * 40)

    def rows(text: str) -> dict[str, list[str]]:
        sec = text.split("## Underlying forecasts", 1)[1].split("\n## ", 1)[0]
        return {
            ln.split("|")[2].strip(): ln.split("|")
            for ln in sec.splitlines()
            if ln.startswith("| MKT-1 v")
        }

    e, lt = rows(early), rows(late)
    assert e["EVAL-1 cohort"][3].strip() == "0"  # decided within 61 days of the cutoff
    assert int(e["operational (all)"][3]) > 0
    assert lt["EVAL-1 cohort"][3] == lt["operational (all)"][3]
    assert "insufficient clusters" in lt["EVAL-1 cohort"][6]  # one expiration week
    assert "Not computed" in none.split("## Underlying forecasts", 1)[1]
