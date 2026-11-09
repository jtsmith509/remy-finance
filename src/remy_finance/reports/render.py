"""Markdown reports: `report trades`, `report daily`, `report cumulative` (handoff R1.7).

Rules (handoff R1.7; EVAL-1 §5 and §7):
- One mode per report, always named in the header; prospective and replay never mix.
- Groups are engine *and* version: records of different versions are never pooled.
- Interim reports are descriptive only. They carry no p-values for P1 or P2, and the
  header says so. Confirmatory analyses run on EVAL-1's fixed schedule, not here.
- The header names the EVAL-1 commit the scoring follows and the registry chain heads.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from remy_finance.baselines.common import BaselineRun
from remy_finance.core import Mode
from remy_finance.data.timeutil import et_date
from remy_finance.registry import EventKind, Registry
from remy_finance.reports.outcomes import Status, TradeOutcome, outcomes
from remy_finance.reports.scores import COVERAGE_LEVELS, Scores, score
from remy_finance.snapshots.store import GapRecord

E = EventKind


@dataclass(frozen=True)
class Header:
    mode: Mode
    generated_at: datetime
    eval_commit: str
    heads: dict[str, tuple[int, str]]

    def lines(self, title: str) -> list[str]:
        rows = "; ".join(f"{t} {n} rows, head {h[:12]}" for t, (n, h) in self.heads.items())
        return [
            f"# {title}: {self.mode.value}",
            "",
            f"- **Mode:** {self.mode.value} (never combined with the other mode)",
            f"- **Generated:** {self.generated_at.isoformat()}",
            f"- **Scoring protocol:** EVAL-1 at commit `{self.eval_commit[:12]}`",
            f"- **Registry:** {rows}",
            "- **Interim report: descriptive only; not a test.** No p-values for P1 or P2; "
            "confirmatory analyses run only on EVAL-1's schedule.",
            "",
        ]


def header(registry: Registry, now: datetime, eval_commit: str) -> Header:
    heads = {t: registry.head(t) for t in ("recommendations", "journal")}
    return Header(registry.mode, now, eval_commit, heads)


def _pct(x: float | Decimal | None, places: int = 1) -> str:
    return "-" if x is None else f"{float(x) * 100:.{places}f}%"


def _usd(x: Decimal) -> str:
    return f"${x:,.2f}"


def _table(head: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


# ---------------------------------------------------------------------- trades


def trades_report(registry: Registry, now: datetime, eval_commit: str) -> str:
    lines = header(registry, now, eval_commit).lines("Trades")
    rows: list[tuple[object, ...]] = []
    for o in sorted(
        outcomes(registry), key=lambda o: (o.rec.decision_time, o.rec.engine, o.rec.rank)
    ):
        r = o.rec
        rows.append(
            (
                r.decision_time.strftime("%Y-%m-%d %H:%M"),
                o.engine_group,
                r.rank,
                o.underlying,
                r.candidate.structure_type.value if r.candidate.structure_type else "",
                r.expiration.isoformat(),
                r.candidate.quantity,
                _usd(r.capital_at_risk),
                _pct(r.predicted_return.mean),
                o.status.value + (f" ({o.unfilled_reason})" if o.unfilled_reason else ""),
                _usd(o.realized_pnl) if o.closed_filled else "-",
                _pct(o.realized_return),
                _pct(o.deviation),
                _flags(o),
                r.trade_id,
            )
        )
    lines += _table(
        (
            "decided (UTC)", "engine", "rank", "symbol", "structure", "expiry", "qty", "CaR",
            "predicted", "status", "P&L", "realized", "realized - predicted", "flags", "trade",
        ),
        rows,
    )  # fmt: skip
    return "\n".join(lines) + "\n"


def _flags(o: TradeOutcome) -> str:
    parts = []
    if o.assignments:
        parts.append(f"assigned x{o.assignments}")
    if o.exit_retries:
        parts.append(f"exit retries x{o.exit_retries}")
    if o.pin_flags:
        parts.append("pin risk")
    if o.corrections:
        parts.append(f"corrected x{o.corrections}")
    return ", ".join(parts)


# ---------------------------------------------------------------------- daily


def daily_report(
    registry: Registry,
    day: date,
    now: datetime,
    eval_commit: str,
    runs: Sequence[BaselineRun] = (),
    gaps: Sequence[GapRecord] = (),
) -> str:
    lines = header(registry, now, eval_commit).lines(f"Daily report {day.isoformat()}")
    by_group: dict[str, Counter[str]] = defaultdict(Counter)
    pnl: dict[str, Decimal] = defaultdict(Decimal)
    for rec in registry.recommendations():
        g = f"{rec.engine} v{rec.engine_version}"
        if et_date(rec.decision_time) == day:
            by_group[g]["recommended"] += 1
        for _, ev in registry.events(rec.trade_id):
            if et_date(ev.event_time) != day:
                continue
            by_group[g][ev.kind.value] += 1
            if ev.kind is E.TRADE_CLOSED:
                pnl[g] += sum((x.cash for _, x in registry.events(rec.trade_id)), Decimal(0))
    nulls = Counter(
        f"{r.engine} v{r.engine_version}"
        for r in runs
        if not r.selected and et_date(r.decision_time) == day
    )
    groups = sorted(set(by_group) | set(nulls))
    lines += _table(
        ("engine", "recommended", "null decisions", "filled legs", "unfilled", "assignments",
         "exits filled", "exits unfilled", "settled", "closed", "P&L of trades closed"),
        (
            (
                g,
                by_group[g]["recommended"],
                nulls[g],
                by_group[g][E.LEG_FILLED.value],
                by_group[g][E.ORDER_UNFILLED.value],
                by_group[g][E.ASSIGNMENT_ASSUMED.value],
                by_group[g][E.EXIT_FILLED.value],
                by_group[g][E.EXIT_UNFILLED.value],
                by_group[g][E.EXPIRATION_SETTLED.value],
                by_group[g][E.TRADE_CLOSED.value],
                _usd(pnl[g]),
            )
            for g in groups
        ),
    )  # fmt: skip
    day_gaps = [g for g in gaps if g.session_date == day]
    lines += ["", f"**Snapshot gaps:** {len(day_gaps)}"]
    lines += [f"- {g.session.value} {g.kind.value}: {g.reason}" for g in day_gaps]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- cumulative


@dataclass(frozen=True)
class GroupStats:
    group: str
    n: int
    filled: int
    unfilled_reasons: Counter[str]
    pending: int
    open: int
    closed: list[TradeOutcome]
    scores: list[Scores]
    pnl: Decimal
    pnl_original: Decimal
    max_drawdown: Decimal
    avg_car_outstanding: Decimal | None

    @property
    def fill_rate(self) -> float | None:
        resolved = self.filled + sum(self.unfilled_reasons.values())
        return None if resolved == 0 else self.filled / resolved


def _drawdown(closed: Sequence[TradeOutcome]) -> Decimal:
    """Maximum drawdown of cumulative realized P&L, booked per close day (EVAL-1 §2.3).
    Trades closing on the same day are one step, so their order can't create a drawdown."""
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    for o in closed:
        assert o.closed_at is not None
        by_day[et_date(o.closed_at)] += o.realized_pnl
    peak = level = worst = Decimal(0)
    for d in sorted(by_day):
        level += by_day[d]
        peak = max(peak, level)
        worst = max(worst, peak - level)
    return worst


def _avg_car(filled: Sequence[TradeOutcome], now: datetime) -> Decimal | None:
    if not filled:
        return None
    start = min(et_date(o.filled_at) for o in filled if o.filled_at)
    end = et_date(now)
    days = (end - start).days + 1
    total = Decimal(0)
    for i in range(days):
        d = start + timedelta(days=i)
        for o in filled:
            assert o.filled_at is not None
            if et_date(o.filled_at) <= d and (o.closed_at is None or et_date(o.closed_at) > d):
                total += o.rec.capital_at_risk
    return (total / days).quantize(Decimal("0.01"))


def group_stats(group: str, items: Sequence[TradeOutcome], now: datetime) -> GroupStats:
    closed = [o for o in items if o.closed_filled]
    filled = [o for o in items if o.filled]
    return GroupStats(
        group=group,
        n=len(items),
        filled=len(filled),
        unfilled_reasons=Counter(
            o.unfilled_reason or "?" for o in items if o.status is Status.UNFILLED
        ),
        pending=sum(1 for o in items if o.status is Status.PENDING),
        open=sum(1 for o in items if o.status is Status.OPEN),
        closed=closed,
        scores=[
            score(o.rec.predicted_return, o.realized_return, o.trade_id)  # type: ignore[arg-type]
            for o in closed
        ],
        pnl=sum((o.realized_pnl for o in closed), Decimal(0)),
        pnl_original=sum((o.realized_pnl_original for o in closed), Decimal(0)),
        max_drawdown=_drawdown(closed),
        avg_car_outstanding=_avg_car(filled, now),
    )


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None

    def ranks(v: Sequence[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    if len(set(rx)) < 2 or len(set(ry)) < 2:
        return None
    return statistics.correlation(rx, ry)


def cumulative_report(registry: Registry, now: datetime, eval_commit: str) -> str:
    lines = header(registry, now, eval_commit).lines("Cumulative report")
    groups: dict[str, list[TradeOutcome]] = defaultdict(list)
    for o in outcomes(registry):
        groups[o.engine_group].append(o)
    stats = [group_stats(g, groups[g], now) for g in sorted(groups)]
    cutoff = et_date(now)

    lines += ["## Activity and fills", ""]
    lines += _table(
        ("engine", "recommended", "filled", "fill rate", "unfilled (reason: n)", "pending",
         "open", "closed", f"EVAL-1 eligible at {cutoff}"),
        (
            (
                s.group, s.n, s.filled, _pct(s.fill_rate),
                ", ".join(f"{k}: {v}" for k, v in sorted(s.unfilled_reasons.items())) or "-",
                s.pending, s.open, len(s.closed),
                sum(1 for o in groups[s.group] if o.closed_filled and o.eval1_eligible(cutoff)),
            )
            for s in stats
        ),
    )  # fmt: skip

    lines += ["", "## Realized versus predicted net return on capital at risk (closed trades)", ""]
    rows: list[tuple[object, ...]] = []
    for s in stats:
        rr = [float(o.realized_return) for o in s.closed if o.realized_return is not None]
        pr = [float(o.rec.predicted_return.mean) for o in s.closed]
        qs = statistics.quantiles(rr, n=20, method="inclusive") if len(rr) >= 2 else []
        rows.append(
            (
                s.group,
                len(rr),
                _pct(statistics.fmean(pr)) if pr else "-",
                _pct(statistics.fmean(rr)) if rr else "-",
                _pct(statistics.median(rr)) if rr else "-",
                _pct(statistics.fmean(sc.bias for sc in s.scores)) if s.scores else "-",
                f"{_pct(qs[0])} / {_pct(qs[4])} / {_pct(qs[14])} / {_pct(qs[18])}" if qs else "-",
            )
        )
    lines += _table(
        ("engine", "closed", "mean predicted", "mean realized", "median realized",
         "mean realized - predicted", "realized 5% / 25% / 75% / 95%"),
        rows,
    )  # fmt: skip

    lines += ["", "## P&L, drawdown and events", ""]
    lines += _table(
        ("engine", "P&L (corrected)", "P&L (original)", "max drawdown",
         "drawdown / avg CaR outstanding", "assignments", "forced exits", "exit retries",
         "pin-risk flags", "corrections"),
        (
            (
                s.group, _usd(s.pnl), _usd(s.pnl_original), _usd(s.max_drawdown),
                _pct(s.max_drawdown / s.avg_car_outstanding)
                if s.avg_car_outstanding else "-",
                sum(o.assignments for o in groups[s.group]),
                sum(o.forced_exits for o in groups[s.group]),
                sum(o.exit_retries for o in groups[s.group]),
                sum(o.pin_flags for o in groups[s.group]),
                sum(o.corrections for o in groups[s.group]),
            )
            for s in stats
        ),
    )  # fmt: skip

    lines += ["", "## Forecast scores (EVAL-1 §2.1; descriptive)", ""]
    cov_head = [f"below {_pct(lv, 0)} (exp {_pct(lv, 0)})" for lv in COVERAGE_LEVELS[:2]] + [
        f"above {_pct(lv, 0)} (exp {_pct(1 - lv, 0)})" for lv in COVERAGE_LEVELS[2:]
    ]
    rows = []
    for s in stats:
        sc = s.scores
        logs = [x.log_score for x in sc if x.log_score is not None]
        cov = [
            _pct(sum(x.exceed[lv] for x in sc if lv in x.exceed) / len(sc)) if sc else "-"
            for lv in COVERAGE_LEVELS
        ]
        rows.append(
            (
                s.group,
                len(sc),
                f"{statistics.fmean(x.crps for x in sc):.4f}" if sc else "-",
                f"{statistics.fmean(logs):.3f}" if logs else "-",
                *cov,
            )
        )
    lines += _table(("engine", "n", "mean CRPS", "mean log score", *cov_head), rows)
    lines += ["", "**PIT histogram** (10 bins; a calibrated forecast is flat):", ""]
    lines += _table(
        ("engine", *(f"{i / 10:.1f}-{(i + 1) / 10:.1f}" for i in range(10))),
        (
            (
                s.group,
                *(sum(1 for x in s.scores if min(int(x.pit * 10), 9) == i) for i in range(10)),
            )
            for s in stats
        ),
    )

    lines += ["", "## Selection by rank (closed trades)", ""]
    rows = []
    for s in stats:
        by_rank: dict[int, list[float]] = defaultdict(list)
        for o in s.closed:
            if o.realized_return is not None:
                by_rank[o.rec.rank].append(float(o.realized_return))
        rho = _spearman(
            [float(o.rec.predicted_return.mean) for o in s.closed],
            [float(o.realized_return) for o in s.closed if o.realized_return is not None],
        )
        rows.append(
            (
                s.group,
                *(
                    f"{_pct(statistics.fmean(by_rank[k]))} (n={len(by_rank[k])})"
                    if by_rank[k]
                    else "-"
                    for k in range(1, 6)
                ),
                "-" if rho is None else f"{rho:.3f}",
            )
        )
    lines += _table(
        ("engine", "rank 1", "rank 2", "rank 3", "rank 4", "rank 5",
         "Spearman(predicted, realized)"),
        rows,
    )  # fmt: skip
    lines += [
        "",
        "RAND-1's rank is its draw order, not a preference (DR-0031).",
    ]
    return "\n".join(lines) + "\n"
