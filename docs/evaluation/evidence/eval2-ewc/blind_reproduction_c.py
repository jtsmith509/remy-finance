"""repro-c: independent EWC size/power simulation (numpy only)."""
import math, json, sys, time
import numpy as np

# ---------- Student-t via regularized incomplete beta (Lentz CF) ----------
def _betacf(a, b, x, itmax=300, eps=1e-15):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0; d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d; h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c; c = tiny if abs(c) < tiny else c
        d = 1.0 / d; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c; c = tiny if abs(c) < tiny else c
        d = 1.0 / d; de = d * c; h *= de
        if abs(de - 1.0) < eps:
            break
    return h

def betainc_reg(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(lbt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b

def t_cdf(t, df):
    x = df / (df + t * t)
    tail = 0.5 * betainc_reg(df / 2.0, 0.5, x)
    return 1 - tail if t > 0 else tail

def t_ppf(p, df):
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p: lo = mid
        else: hi = mid
    return 0.5 * (lo + hi)

def two_sided_p(tstat, df):
    # vectorized over tstat array
    return np.array([2.0 * (1.0 - t_cdf(abs(v), df)) for v in tstat])

# ---------- DCT matrix ----------
def cos_matrix(W, K):
    t = np.arange(W)
    j = np.arange(1, K + 1)[:, None]
    return math.sqrt(2.0 / W) * np.cos(np.pi * j * (t + 0.5) / W)  # K x W

# ---------- DGP ----------
def simulate(rng, R, W, mu, Hmax, cproc="iid", shock="normal", q=0.10, delta=0.0):
    """Return S (R x W), n (R x W)."""
    n = rng.poisson(10, size=(R, W))
    n = n * (rng.random((R, W)) >= q)
    G = W + 25  # s = -12..W+12 -> index s+12
    if shock == "normal":
        g = rng.standard_normal((R, G))
    else:
        g = rng.standard_t(3, size=(R, G)) / math.sqrt(3.0)
    cs = np.concatenate([np.zeros((R, 1)), np.cumsum(g, axis=1)], axis=1)
    if cproc == "iid":
        c = rng.standard_normal((R, W))
    else:
        phi = 0.6
        eps = rng.standard_normal((R, W))
        c = np.empty((R, W)); c[:, 0] = eps[:, 0]
        s = math.sqrt(1 - phi * phi)
        for t in range(1, W):
            c[:, t] = phi * c[:, t - 1] + s * eps[:, t]
    nb = n.ravel()
    tot = int(nb.sum())
    rep_idx = np.repeat(np.repeat(np.arange(R), W), nb)
    wk_idx = np.repeat(np.tile(np.arange(W), R), nb)
    H = rng.integers(1, Hmax + 1, size=tot)
    z = rng.standard_normal(tot); e = rng.standard_normal(tot)
    start = wk_idx + 12
    Gsum = cs[rep_idx, start + H] - cs[rep_idx, start]
    d = (mu + 0.7 * c[rep_idx, wk_idx] + 0.3 * z) * Gsum / np.sqrt(H) + 0.5 * e + delta
    S = np.bincount(rep_idx * W + wk_idx, weights=d, minlength=R * W).reshape(R, W)
    return S, n.astype(float), d

# ---------- procedures ----------
def ewc_p(S, n, K):
    R, W = S.shape
    N = n.sum(1)
    T = S.sum(1) / N
    u = S - T[:, None] * n
    M = cos_matrix(W, K)
    Lam = u @ M.T  # R x K
    Om = (Lam ** 2).mean(1)
    se = np.sqrt(W * Om) / N
    return two_sided_p(T / se, K)

def naive_p(S, n, rng, B=999, chunk=200):
    R, W = S.shape
    out = np.empty(R)
    T = S.sum(1) / n.sum(1)
    for r in range(R):
        idx = rng.integers(0, W, size=(B, W))
        Nb = n[r][idx].sum(1)
        bad = Nb == 0
        while bad.any():
            idx[bad] = rng.integers(0, W, size=(bad.sum(), W))
            Nb = n[r][idx].sum(1); bad = Nb == 0
        Tb = S[r][idx].sum(1) / Nb
        out[r] = np.mean(np.abs(Tb - T[r]) >= abs(T[r]))
    return out

def rate(p, a):
    r = float(np.mean(p < a)) if True else 0
    return r

def se_of(rhat, R):
    return math.sqrt(rhat * (1 - rhat) / R)

def main():
    t0 = time.time()
    # t checks
    checks = [(4, .975, 2.776), (9, .975, 2.262), (4, .9875, 3.495), (14, .975, 2.145),
              (1, .975, 12.706), (2, .975, 4.303), (5, .975, 2.571), (10, .975, 2.228),
              (30, .975, 2.042), (9, .9875, 2.685), (11, .975, 2.201), (7, .99, 2.998)]
    chk = []
    for df, p, tab in checks:
        v = t_ppf(p, df)
        chk.append((df, p, round(v, 4), tab, abs(v - tab) < 1.5e-3))
    print("t checks:", chk)
    assert all(c[-1] for c in chk)

    R = 4000; Rn = 4000; B = 999
    rows = []
    cells = []
    for W in (43, 95):
        for mu in (0, 0.5, 1.0):
            for Hmax in (4, 8):
                cells.append(dict(W=W, mu=mu, Hmax=Hmax, cproc="iid", shock="normal", q=0.10, variant="base"))
    for W in (43, 95):
        cells.append(dict(W=W, mu=0.5, Hmax=8, cproc="ar1", shock="normal", q=0.10, variant="AR1_c"))
        cells.append(dict(W=W, mu=0.5, Hmax=8, cproc="iid", shock="t3", q=0.10, variant="t3_shocks"))
        cells.append(dict(W=W, mu=0.5, Hmax=8, cproc="iid", shock="normal", q=0.35, variant="q0.35"))
    # sanity: pure iid (mu=0,Hmax=1)
    for W in (43, 95):
        cells.append(dict(W=W, mu=0, Hmax=1, cproc="iid", shock="normal", q=0.10, variant="iid_check_Hmax1"))

    for ci, c in enumerate(cells):
        rng = np.random.default_rng([20261123, ci])
        S, n, _ = simulate(rng, R, c["W"], c["mu"], c["Hmax"], c["cproc"], c["shock"], c["q"])
        W = c["W"]
        Ks = {"k10": W // 10, "k8": W // 8, "k12": W // 12}
        pe = {k: ewc_p(S, n, K) for k, K in Ks.items()}
        pn = naive_p(S[:Rn], n[:Rn], np.random.default_rng([777, ci]), B=B)
        row = dict(W=W, mu=c["mu"], Hmax=c["Hmax"], variant=c["variant"],
                   ewc_size_05=rate(pe["k10"], .05), ewc_size_025=rate(pe["k10"], .025),
                   ewc_k8_size_05=rate(pe["k8"], .05), ewc_k12_size_05=rate(pe["k12"], .05),
                   ewc_k8_size_025=rate(pe["k8"], .025), ewc_k12_size_025=rate(pe["k12"], .025),
                   naive_size_05=rate(pn, .05), naive_size_025=rate(pn, .025),
                   mc_se=se_of(.05, R), Ks=Ks)
        rows.append(row)
        print(json.dumps(row), f"[{time.time()-t0:.0f}s]", flush=True)

    # power
    rng = np.random.default_rng([99, 1])
    _, _, dpilot = simulate(rng, 2000, 95, 0.0, 8, "iid", "normal", 0.10)
    sd = float(dpilot.std())
    delta = 0.15 * sd
    print("pilot sd", sd, "delta", delta)
    power = {}
    for W in (43, 95):
        rng = np.random.default_rng([31337, W])
        S, n, _ = simulate(rng, 2000, W, 0.0, 8, "iid", "normal", 0.10, delta=delta)
        pe = ewc_p(S, n, W // 10)
        pn = naive_p(S, n, np.random.default_rng([4242, W]), B=B)
        power[f"W{W}_ewc"] = rate(pe, .05); power[f"W{W}_naive"] = rate(pn, .05)
    print("power", json.dumps(power))
    json.dump(dict(t_checks=chk, rows=rows, power=power, sd=sd, delta=delta),
              open(__file__.replace("ewc_sim.py", "results.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
