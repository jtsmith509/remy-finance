"""Independent size/power simulation for EWC test of a board-weighted mean (repro-b).

Fully vectorized over replications. No scipy: Student-t CDF via regularized
incomplete beta (Lentz continued fraction), quantile via bisection.
"""

from __future__ import annotations

import json
import math
import time

import numpy as np


# ---------------------------------------------------------------- Student-t
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def t_checks() -> list[dict]:
    table = [
        (4, 0.975, 2.776), (9, 0.975, 2.262), (4, 0.9875, 3.495), (14, 0.975, 2.145),
        (1, 0.975, 12.706), (2, 0.975, 4.303), (9, 0.9875, 2.685), (11, 0.975, 2.201),
        (7, 0.975, 2.365), (30, 0.975, 2.042), (5, 0.995, 4.032), (11, 0.9875, 2.593),
    ]
    out = []
    for df, p, ref in table:
        q = t_ppf(p, df)
        out.append({"df": df, "p": p, "computed": round(q, 4), "table": ref, "ok": abs(q - ref) < 6e-4})
        # also CDF round-trip at the table value
        assert abs(t_cdf(ref, df) - p) < 2e-4, (df, p, t_cdf(ref, df))
    return out


# ---------------------------------------------------------------- DGP
def simulate(rng, R, W, mu, hmax, shock="normal", load="iid", q=0.10, delta=0.0):
    """Return S (R,W), n (R,W) float arrays."""
    L = W + 25  # s = -12 .. W+12
    if shock == "normal":
        g = rng.standard_normal((R, L))
    else:
        g = rng.standard_t(3, size=(R, L)) / math.sqrt(3.0)
    P = np.zeros((R, L + 1))
    np.cumsum(g, axis=1, out=P[:, 1:])
    if load == "iid":
        c = rng.standard_normal((R, W))
    else:
        phi = 0.6
        eps = rng.standard_normal((R, W)) * math.sqrt(1 - phi * phi)
        c = np.empty((R, W))
        c[:, 0] = rng.standard_normal(R)
        for t in range(1, W):
            c[:, t] = phi * c[:, t - 1] + eps[:, t]
    n = rng.poisson(10.0, size=(R, W))
    n = n * (rng.random((R, W)) >= q)
    nflat = n.ravel()
    N = int(nflat.sum())
    cell = np.repeat(np.arange(R * W), nflat)
    r_idx = cell // W
    t_idx = cell % W
    H = rng.integers(1, hmax + 1, size=N)
    z = rng.standard_normal(N)
    e = rng.standard_normal(N)
    s0 = t_idx + 12  # index of g_t in g array
    G = P[r_idx, s0 + H] - P[r_idx, s0]
    d = (mu + 0.7 * c.ravel()[cell] + 0.3 * z) * G / np.sqrt(H) + 0.5 * e + delta
    S = np.bincount(cell, weights=d, minlength=R * W).reshape(R, W)
    return S, n.astype(float), d


# ---------------------------------------------------------------- tests
def ewc_tstats(S, n, K):
    R, W = S.shape
    N = n.sum(1)
    T = S.sum(1) / N
    u = S - T[:, None] * n
    t = np.arange(W)
    j = np.arange(1, K + 1)
    basis = math.sqrt(2.0 / W) * np.cos(np.pi * j[:, None] * (t[None, :] + 0.5) / W)  # (K,W)
    Lam = u @ basis.T
    Omega = (Lam**2).mean(1)
    se = np.sqrt(W * Omega) / N
    return T / se


def naive_pvalues(rng, S, n, B=999, chunk=40):
    R, W = S.shape
    T = S.sum(1) / n.sum(1)
    p = np.empty(R)
    for a in range(0, R, chunk):
        b = min(R, a + chunk)
        m = b - a
        idx = rng.integers(0, W, size=(m, B, W))
        rows = np.arange(a, b)[:, None, None]
        Ns = n[rows, idx].sum(2)
        bad = Ns <= 0
        while bad.any():  # redraw degenerate draws
            ii, bb = np.nonzero(bad)
            new = rng.integers(0, W, size=(len(ii), W))
            idx[ii, bb] = new
            Ns[ii, bb] = n[a + ii[:, None], new].sum(1)
            bad = Ns <= 0
        Ss = S[rows, idx].sum(2)
        Ts = Ss / Ns
        p[a:b] = (np.abs(Ts - T[a:b, None]) >= np.abs(T[a:b, None])).mean(1)
    return p


def rate(x):
    r = float(np.mean(x))
    return r, math.sqrt(r * (1 - r) / len(x))


def main():
    t0 = time.time()
    checks = t_checks()
    for c in checks:
        print("t-check", c)
    assert all(c["ok"] for c in checks)

    R = 4000
    cells = []
    for W in (43, 95):
        for mu in (0.0, 0.5, 1.0):
            for hmax in (4, 8):
                cells.append(dict(W=W, mu=mu, hmax=hmax, shock="normal", load="iid", q=0.10))
        cells.append(dict(W=W, mu=0.5, hmax=8, shock="normal", load="ar1", q=0.10))
        cells.append(dict(W=W, mu=0.5, hmax=8, shock="t3", load="iid", q=0.10))
        cells.append(dict(W=W, mu=0.5, hmax=8, shock="normal", load="iid", q=0.35))

    rows = []
    for ci, cell in enumerate(cells):
        rng = np.random.default_rng(np.random.SeedSequence([20261123, 1, ci]))
        W = cell["W"]
        S, n, _ = simulate(rng, R, W, cell["mu"], cell["hmax"], cell["shock"], cell["load"], cell["q"])
        row = dict(cell)
        row["variant"] = f"{cell['shock']}/{cell['load']}/q={cell['q']}"
        for K, tag in ((W // 10, "k10"), (W // 8, "k8"), (W // 12, "k12")):
            ts = np.abs(ewc_tstats(S, n, K))
            for a in (0.05, 0.025):
                crit = t_ppf(1 - a / 2, K)
                r, se = rate(ts > crit)
                row[f"ewc_{tag}_{a}"] = (round(r, 4), round(se, 4))
            row[f"K_{tag}"] = K
        pn = naive_pvalues(rng, S, n)
        for a in (0.05, 0.025):
            r, se = rate(pn < a)
            row[f"naive_{a}"] = (round(r, 4), round(se, 4))
        rows.append(row)
        print(json.dumps(row), f"[{time.time() - t0:.0f}s]", flush=True)

    # pilot sd of d_b (mu=0, iid, Normal, Hmax=8)
    rng = np.random.default_rng(np.random.SeedSequence([20261123, 2]))
    _, _, d = simulate(rng, 2000, 95, 0.0, 8)
    sd = float(d.std())
    delta = 0.15 * sd
    print(f"pilot sd(d_b)={sd:.5f} (analytic sqrt(0.83)={math.sqrt(0.83):.5f}); delta={delta:.5f}")

    power = {}
    for W in (43, 95):
        rng = np.random.default_rng(np.random.SeedSequence([20261123, 3, W]))
        S, n, _ = simulate(rng, 2000, W, 0.0, 8, delta=delta)
        K = W // 10
        ts = np.abs(ewc_tstats(S, n, K))
        pe = rate(ts > t_ppf(0.975, K))
        pn = rate(naive_pvalues(rng, S, n) < 0.05)
        power[W] = dict(ewc=pe, naive=pn)
        print("power", W, power[W], flush=True)
    print(f"done {time.time() - t0:.0f}s")
    with open(__file__.replace("sim.py", "results.json"), "w") as f:
        json.dump(dict(checks=checks, rows=rows, sd=sd, delta=delta, power=power), f, indent=1)


if __name__ == "__main__":
    main()
