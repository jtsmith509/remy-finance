"""Audit: independent re-implementation + stress DGP with cross-week dependence.
beta_b = mu + 0.7*c_t + 0.3*z_b, c_t AR(1) with coefficient phi (stationary var 1).
mu=0, phi=0 reproduces the agents' DGP. Null holds for any mu, phi (g indep of beta, E g=0)."""
import sys, json
import numpy as np

PAD = 12


def gen(rng, W, Hmax, law, mu=0.0, phi=0.0, pzero=0.10, delta=0.0):
    g = rng.standard_normal(W + 25) if law == "normal" else rng.standard_t(3, W + 25) / np.sqrt(3)
    cg = np.concatenate([[0.0], np.cumsum(g)])
    n = rng.poisson(10, W)
    n[rng.random(W) < pzero] = 0
    e = rng.standard_normal(W)
    c = np.empty(W); c[0] = e[0]
    for t in range(1, W):
        c[t] = phi * c[t - 1] + np.sqrt(1 - phi ** 2) * e[t]
    wk = np.repeat(np.arange(W), n); N = wk.size
    H = rng.integers(1, Hmax + 1, N)
    st = wk + PAD
    shock = (cg[st + H] - cg[st]) / np.sqrt(H)
    beta = mu + 0.7 * c[wk] + 0.3 * rng.standard_normal(N)
    d = beta * shock + rng.normal(0, 0.5, N) + delta
    return np.bincount(wk, weights=d, minlength=W), n.astype(float)


def hac(S, n, m=8):
    sn = n.sum(); T = S.sum() / sn; u = S - T * n; W = len(u)
    lrv = u @ u / W
    for j in range(1, m + 1):
        lrv += 2 * (1 - j / (m + 1)) * (u[j:] @ u[:-j]) / W
    return T, np.sqrt(max(lrv, 0) * W) / sn


def boot(rng, S, n, kind, B, L=9):
    W = len(S)
    if kind == "naive":
        idx = rng.integers(0, W, (B, W))
    elif kind == "stat":
        starts = rng.integers(0, W, (B, W)); flag = rng.random((B, W)) < 1 / L; flag[:, 0] = True
        pos = np.arange(W)[None, :]
        last = np.maximum.accumulate(np.where(flag, pos, 0), axis=1)
        idx = (np.take_along_axis(starts, last, 1) + pos - last) % W
    elif kind == "cmbb":  # circular MBB
        k = -(-W // L); s = rng.integers(0, W, (B, k))
        idx = ((s[:, :, None] + np.arange(L)[None, None, :]) % W).reshape(B, -1)[:, :W]
    num, den = S[idx].sum(1), n[idx].sum(1)
    ok = den > 0
    return num[ok] / den[ok]


FB = {43: {0.05: 2.562, 0.025: 3.065}, 95: {0.05: 2.230, 0.025: 2.583}}


def run(W, Hmax, law, mu, phi, R, seed, B=499, pzero=0.10, delta=0.0):
    rng = np.random.default_rng(seed)
    rej = {k: 0 for k in ("naive", "stat", "cmbb", "hac_fb", "hac_t")}
    cov = {k: 0 for k in rej}
    Ts, r1 = [], []
    tq = {43: 2.018, 95: 1.986}[W]
    for _ in range(R):
        S, n = gen(rng, W, Hmax, law, mu, phi, pzero, delta)
        if n.sum() == 0:
            continue
        T, se = hac(S, n)
        Ts.append(T)
        u = S - T * n; r1.append((u[1:] @ u[:-1]) / (u @ u))
        rej["hac_fb"] += abs(T) / se > FB[W][0.05]; cov["hac_fb"] += abs(T) <= FB[W][0.05] * se
        rej["hac_t"] += abs(T) / se > tq; cov["hac_t"] += abs(T) <= tq * se
        for k in ("naive", "stat", "cmbb"):
            Tb = boot(rng, S, n, k, B)
            p = np.mean(np.abs(Tb - T) >= abs(T))
            lo, hi = np.quantile(Tb, [0.025, 0.975])
            rej[k] += p <= 0.05; cov[k] += lo <= 0 <= hi
    Rn = len(Ts)
    return {"W": W, "Hmax": Hmax, "law": law, "mu": mu, "phi": phi, "pzero": pzero, "delta": delta,
            "R": Rn, "size05": {k: rej[k] / Rn for k in rej}, "cov95": {k: cov[k] / Rn for k in cov},
            "mean_lag1_autocorr_u": float(np.mean(r1))}


if __name__ == "__main__":
    out = []
    cfgs = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (0.0, 0.6), (1.0, 0.6)]
    R = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    s = 7000
    for W in (43, 95):
        for mu, phi in cfgs:
            s += 1
            r = run(W, 8, "normal", mu, phi, R, s)
            out.append(r); print(json.dumps(r), flush=True)
    # t3, Hmax 4, and heavy coverage gaps at mu=1
    for W in (43, 95):
        for (Hm, law, pz) in ((8, "t3", 0.10), (4, "normal", 0.10), (8, "normal", 0.35)):
            s += 1
            r = run(W, Hm, law, 1.0, 0.0, R, s, pzero=pz)
            out.append(r); print(json.dumps(r), flush=True)
    json.dump(out, open(__file__.replace("stress.py", "stress.json"), "w"), indent=1)
