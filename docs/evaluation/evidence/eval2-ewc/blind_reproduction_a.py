"""Independent size/power study of the EWC test vs naive iid week bootstrap.

Exact-null DGP with cross-week dependence via overlapping multi-week holds and
a persistent common tilt mu. Deterministic seeds per cell.
"""
from __future__ import annotations

import json
import math
import sys
import time
from multiprocessing import Pool

import numpy as np

# ---------------------------------------------------------------- Student t


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_sf2(tval: float, df: float) -> float:
    """Two-sided p-value P(|T_df| >= |t|)."""
    x = df / (df + tval * tval)
    return betainc_reg(df / 2.0, 0.5, x)


def t_cdf(tval: float, df: float) -> float:
    p2 = t_sf2(tval, df)
    return 1.0 - 0.5 * p2 if tval >= 0 else 0.5 * p2


def t_ppf(p: float, df: float) -> float:
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


T_TABLE = [  # (df, p, table value)
    (4, 0.975, 2.776), (9, 0.975, 2.262), (4, 0.9875, 3.495), (14, 0.975, 2.145),
    (1, 0.975, 12.706), (2, 0.975, 4.303), (5, 0.995, 4.032), (10, 0.95, 1.812),
    (7, 0.9875, 2.841), (11, 0.975, 2.201), (30, 0.975, 2.042), (3, 0.9, 1.638),
]


def check_t() -> str:
    out = []
    ok = True
    for df, p, tab in T_TABLE:
        q = t_ppf(p, df)
        back = t_cdf(tab, df)
        out.append(f"t_{{{df},{p}}}={q:.4f} (table {tab}, cdf(table)={back:.5f})")
        if abs(q - tab) > 6e-4 * max(1, tab):
            ok = False
    # also exact closed forms: df=1 Cauchy, df=2
    c1 = t_cdf(1.0, 1)  # 0.75
    c2 = t_cdf(2.0, 2)  # 0.5+ 2/(2*sqrt(6))
    out.append(f"cdf(1;1)={c1:.10f} (exact .75); cdf(2;2)={c2:.10f} (exact {0.5 + 1 / math.sqrt(6):.10f})")
    ok = ok and abs(c1 - 0.75) < 1e-10 and abs(c2 - 0.5 - 1 / math.sqrt(6)) < 1e-10
    return ("ALL OK. " if ok else "MISMATCH. ") + "; ".join(out)


# ---------------------------------------------------------------- DGP


def simulate(rng: np.random.Generator, W: int, mu: float, Hmax: int, q: float,
             shock: str, cload: str, delta: float = 0.0):
    """Return (S, n) arrays of length W."""
    # weekly shocks g_s for s=-12..W+12 ; index s+12
    ns = W + 25
    if shock == "normal":
        g = rng.standard_normal(ns)
    else:
        g = rng.standard_t(3, size=ns) / math.sqrt(3.0)
    if cload == "iid":
        c = rng.standard_normal(W)
    else:
        c = np.empty(W)
        c[0] = rng.standard_normal()
        sc = math.sqrt(1 - 0.36)
        for t in range(1, W):
            c[t] = 0.6 * c[t - 1] + sc * rng.standard_normal()
    n = rng.poisson(10, size=W)
    empty = rng.random(W) < q
    n[empty] = 0
    S = np.zeros(W)
    csum = np.concatenate([[0.0], np.cumsum(g)])  # csum[k] = sum g[0..k-1]
    for t in range(W):
        nt = int(n[t])
        if nt == 0:
            continue
        H = rng.integers(1, Hmax + 1, size=nt)
        z = rng.standard_normal(nt)
        e = rng.standard_normal(nt)
        start = t + 12
        gsum = csum[start + H] - csum[start]
        d = (mu + 0.7 * c[t] + 0.3 * z) * gsum / np.sqrt(H) + 0.5 * e + delta
        S[t] = d.sum()
    return S, n


# ---------------------------------------------------------------- tests


def ewc_p(S: np.ndarray, n: np.ndarray, K: int) -> float:
    W = len(S)
    N = n.sum()
    T = S.sum() / N
    u = S - T * n
    tt = np.arange(W) + 0.5
    lam2 = 0.0
    for j in range(1, K + 1):
        L = math.sqrt(2.0 / W) * float(np.sum(np.cos(math.pi * j * tt / W) * u))
        lam2 += L * L
    omega = lam2 / K
    se = math.sqrt(W * omega) / N
    return t_sf2(T / se, K)


def naive_p(rng: np.random.Generator, S: np.ndarray, n: np.ndarray, B: int = 999) -> float:
    W = len(S)
    T = S.sum() / n.sum()
    idx = rng.integers(0, W, size=(B, W))
    Nb = n[idx].sum(axis=1)
    bad = Nb == 0
    while bad.any():  # redraw degenerate draws
        idx[bad] = rng.integers(0, W, size=(int(bad.sum()), W))
        Nb = n[idx].sum(axis=1)
        bad = Nb == 0
    Tb = S[idx].sum(axis=1) / Nb
    return float(np.mean(np.abs(Tb - T) >= abs(T)))


# ---------------------------------------------------------------- cells


def run_cell(args):
    cid, W, mu, Hmax, q, shock, cload, R, delta = args
    rng = np.random.default_rng(np.random.SeedSequence([20261123, cid]))
    Ks = {"k10": W // 10, "k8": W // 8, "k12": W // 12}
    rej = {f"{k}_{a}": 0 for k in Ks for a in ("05", "025")}
    rej.update({"naive_05": 0, "naive_025": 0})
    for _ in range(R):
        S, n = simulate(rng, W, mu, Hmax, q, shock, cload, delta)
        for k, K in Ks.items():
            p = ewc_p(S, n, K)
            rej[f"{k}_05"] += p <= 0.05
            rej[f"{k}_025"] += p <= 0.025
        p = naive_p(rng, S, n)
        rej["naive_05"] += p <= 0.05
        rej["naive_025"] += p <= 0.025
    res = {k: v / R for k, v in rej.items()}
    res.update(dict(cid=cid, W=W, mu=mu, Hmax=Hmax, q=q, shock=shock, cload=cload, R=R,
                    delta=delta, K10=Ks["k10"], K8=Ks["k8"], K12=Ks["k12"]))
    return res


def pilot_sd(seed: int = 7, nweeks: int = 200000) -> float:
    """sd of d_b under null mu=0, iid c, Normal, Hmax=8 (pooled over boards)."""
    rng = np.random.default_rng(seed)
    W = nweeks
    g = rng.standard_normal(W + 25)
    c = rng.standard_normal(W)
    csum = np.concatenate([[0.0], np.cumsum(g)])
    nb = 10
    t = np.repeat(np.arange(W), nb)
    H = rng.integers(1, 9, size=t.size)
    z = rng.standard_normal(t.size)
    e = rng.standard_normal(t.size)
    gs = csum[t + 12 + H] - csum[t + 12]
    d = (0.7 * c[t] + 0.3 * z) * gs / np.sqrt(H) + 0.5 * e
    return float(d.std())


def main() -> None:
    R = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    RP = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    tcheck = check_t()
    print(tcheck, flush=True)
    cells = []
    cid = 0
    for W in (43, 95):
        for mu in (0.0, 0.5, 1.0):
            for Hmax in (4, 8):
                cells.append((cid, W, mu, Hmax, 0.10, "normal", "iid", R, 0.0)); cid += 1
    for W in (43, 95):
        cells.append((cid, W, 0.5, 8, 0.10, "normal", "ar1", R, 0.0)); cid += 1
        cells.append((cid, W, 0.5, 8, 0.10, "t3", "iid", R, 0.0)); cid += 1
        cells.append((cid, W, 0.5, 8, 0.35, "normal", "iid", R, 0.0)); cid += 1
    sd = pilot_sd()
    delta = 0.15 * sd
    print(f"pilot sd(d_b)={sd:.5f} (theory sqrt(0.49+0.09+0.25)={math.sqrt(0.83):.5f}); delta={delta:.5f}", flush=True)
    pcells = [(100 + i, W, 0.0, 8, 0.10, "normal", "iid", RP, delta) for i, W in enumerate((43, 95))]
    t0 = time.time()
    with Pool(4) as pool:
        out = pool.map(run_cell, cells + pcells, chunksize=1)
    print(f"elapsed {time.time() - t0:.1f}s", flush=True)
    for r in out:
        se05 = math.sqrt(0.05 * 0.95 / r["R"])
        se025 = math.sqrt(0.025 * 0.975 / r["R"])
        r["mcse05"], r["mcse025"] = se05, se025
        print(f"W={r['W']:3d} mu={r['mu']} H={r['Hmax']} q={r['q']} {r['shock']:6s} {r['cload']:3s} "
              f"d={r['delta']:.3f} R={r['R']} | K10({r['K10']}) {r['k10_05']:.4f}/{r['k10_025']:.4f} "
              f"K8({r['K8']}) {r['k8_05']:.4f} K12({r['K12']}) {r['k12_05']:.4f} | naive "
              f"{r['naive_05']:.4f}/{r['naive_025']:.4f} | se {se05:.4f}/{se025:.4f}")
    with open(__file__.replace("ewc_size.py", "results.json"), "w") as f:
        json.dump(dict(tcheck=tcheck, pilot_sd=sd, delta=delta, cells=out), f, indent=1)


if __name__ == "__main__":
    main()
