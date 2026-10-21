"""Additive, recency-weighted ridge ratings for offense, defense, tempo (see methodology doc, section 4)."""
import numpy as np
import pandas as pd
from scipy.stats import norm

DEFAULTS = dict(half_life=1000.0, prior_games=2.5, carry=0.92, tempo_carry=0.7,
                sigma0=11.25, sigma_scale=True, new_team_pct=0.15, roster_blend=0.5)
# Tuned on 2014-2020 walk-forward backtests; validated on 2022-2026 holdout.


def _team_index(games, prior):
    ids = sorted(set(games.home_id) | set(games.away_id) | set(prior.index if prior is not None else []))
    return {t: i for i, t in enumerate(ids)}


def fit(games, asof, prior=None, p=DEFAULTS):
    """Fit ratings using games strictly before `asof`. prior: DataFrame index team_id cols o,d,t (deviations)."""
    g = games[games.date < asof]
    cap = p.get("cap")
    if cap and len(g):
        # compress blowouts: margin beyond `cap` counts only `cap_keep` as much (Pomeroy-style diminishing returns)
        g = g.copy()
        m = g.home_pts - g.away_pts
        excess = (m.abs() - cap).clip(lower=0) * (1 - p.get("cap_keep", 0.5)) * np.sign(m)
        g["home_pts"] = g.home_pts - excess / 2
        g["away_pts"] = g.away_pts + excess / 2
    idx = _team_index(g, prior)
    T = len(idx)
    if len(g) == 0:
        base = p.get("preseason_meta") or {}
        return _from_prior(idx, prior), dict(mu=base.get("mu", 105.0), h=base.get("h", 1.4), hn=base.get("hn", 0.0),
                                              tau=base.get("tau", 68.0), n=0)
    age = (pd.Timestamp(asof) - g.date).dt.days.values
    w = 0.5 ** (age / p["half_life"])
    hi = g.home_id.map(idx).values; ai = g.away_id.map(idx).values
    loc = np.where(g.neutral.values, 0.0, 1.0)
    n = len(g)
    # efficiency rows: home offense and away offense
    # params: o[T], d[T], mu, h(home eff per 100 for offense at home), trend
    P = 2 * T + 4
    rows_off = np.concatenate([hi, ai]); rows_def = np.concatenate([ai, hi])
    hl = np.concatenate([loc, -loc])
    nloc = np.where(g.neutral.values, 1.0, 0.0)
    nl = np.concatenate([nloc, -nloc])
    days = (g.date - g.date.min()).dt.days.values.astype(float)
    tday = np.concatenate([days, days]) / 100.0
    y = np.concatenate([100 * g.home_pts.values / g.poss.values, 100 * g.away_pts.values / g.poss.values])
    ww = np.concatenate([w, w])
    import scipy.sparse as sp
    r = np.arange(2 * n)
    rows = np.concatenate([r, r, r, r, r, r])
    cols = np.concatenate([rows_off, T + rows_def, np.full(2 * n, 2 * T), np.full(2 * n, 2 * T + 1), np.full(2 * n, 2 * T + 2), np.full(2 * n, 2 * T + 3)])
    vals = np.concatenate([np.ones(2 * n), -np.ones(2 * n), np.ones(2 * n), hl, tday, nl])
    X = sp.csr_matrix((vals, (rows, cols)), shape=(2 * n, P))
    pri = np.zeros(P)
    if prior is not None:
        pm = prior.reindex([t for t in idx])
        pri[:T] = pm.o.fillna(0).values; pri[T:2 * T] = pm.d.fillna(0).values
    lam = p["prior_games"] * np.mean(w) * 2
    L = np.zeros(P); L[:2 * T] = lam
    Xw = X.multiply(ww[:, None]).tocsr()
    A = (Xw.T @ X).toarray() + np.diag(L)
    b = Xw.T @ y + L * pri
    c1 = np.zeros(P); c1[:T] = 1; c2 = np.zeros(P); c2[T:2 * T] = 1
    A += 1e3 * (np.outer(c1, c1) + np.outer(c2, c2))
    beta = np.linalg.solve(A, b)
    o, d = beta[:T], beta[T:2 * T]
    mu = beta[2 * T] + beta[2 * T + 2] * days.max() / 100.0
    h = beta[2 * T + 1]
    hn = beta[2 * T + 3]
    # tempo: poss = tau + t_i + t_j
    rr = np.arange(n)
    Xt = sp.csr_matrix((np.concatenate([np.ones(3 * n), days / 100.0]), (np.concatenate([rr, rr, rr, rr]), np.concatenate([hi, ai, np.full(n, T), np.full(n, T + 1)]))), shape=(n, T + 2))
    prt = np.zeros(T + 2)
    if prior is not None:
        prt[:T] = prior.reindex([t for t in idx]).t.fillna(0).values
    Lt = np.zeros(T + 2); Lt[:T] = p["prior_games"] * np.mean(w)
    Xtw = Xt.multiply(w[:, None]).tocsr()
    onesT = np.r_[np.ones(T), 0, 0]
    At = (Xtw.T @ Xt).toarray() + np.diag(Lt) + 1e3 * np.outer(onesT, onesT)
    bt = Xtw.T @ g.poss.values + Lt * prt
    bt_ = np.linalg.solve(At, bt)
    inv = {i: t for t, i in idx.items()}
    gp = pd.concat([g.home_id, g.away_id]).value_counts()
    R = pd.DataFrame({"o": o, "d": d, "t": bt_[:T]}, index=[inv[i] for i in range(T)])
    R["games"] = R.index.map(gp).fillna(0).astype(int)
    R["em"] = R.o + R.d
    tau = bt_[T] + bt_[T + 1] * days.max() / 100.0
    return R, dict(mu=mu, h=h, hn=hn, tau=tau, n=n)


def _from_prior(idx, prior):
    R = pd.DataFrame(0.0, index=list(idx), columns=["o", "d", "t"])
    if prior is not None:
        R.update(prior[["o", "d", "t"]])
    R["games"] = 0; R["em"] = R.o + R.d
    return R


def make_prior(final, p=DEFAULTS, teams=None):
    """Next-season prior from last season's final ratings, regressed toward the mean."""
    pr = final[["o", "d", "t"]].copy()
    pr[["o", "d"]] *= p["carry"]; pr["t"] *= p["tempo_carry"]
    if teams is not None:
        new = [t for t in teams if t not in pr.index]
        if new:
            q = final.em.quantile(p["new_team_pct"]) * p["carry"] / 2
            pr = pd.concat([pr, pd.DataFrame({"o": q, "d": q, "t": 0.0}, index=new)])
    return pr


def predict(R, meta, home_id, away_id, neutral, p=DEFAULTS):
    """Vectorized projection. Returns margin (home minus away), total, p_home, poss."""
    get = lambda ids, c: np.array([R.at[i, c] if i in R.index else 0.0 for i in ids])
    oh, dh, th = get(home_id, "o"), get(home_id, "d"), get(home_id, "t")
    oa, da, ta = get(away_id, "o"), get(away_id, "d"), get(away_id, "t")
    neu = np.asarray(neutral)
    loc = np.where(neu, meta.get('hn', 0.0), meta['h'])
    poss = meta["tau"] + th + ta
    ph = poss / 100 * (meta["mu"] + oh - da + loc)
    pa = poss / 100 * (meta["mu"] + oa - dh - loc)
    m = ph - pa
    sig = p["sigma0"] * (np.sqrt(poss / 68.0) if p["sigma_scale"] else 1.0)
    return m, ph + pa, norm.cdf(m / sig), poss
