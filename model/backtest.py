import numpy as np, pandas as pd
from scipy.stats import norm
from model.ratings import fit, make_prior, predict, DEFAULTS


def mondays(season_games):
    start = season_games.date.min()
    first = start - pd.Timedelta(days=start.weekday())  # Monday on/before opener
    end = season_games.date.max() + pd.Timedelta(days=7)
    return pd.date_range(first, end, freq="7D")


def roster_prior_for(s, F, p):
    from model.prior import fit_coefs, build_prior
    tr = F[F.season < s]
    if tr.dropna(subset=["em1", "target"]).empty:
        return None
    coefs, _ = fit_coefs(tr)
    f = F[F.season == s]
    new_em = tr[tr.em1.isna()].target.mean() if tr.em1.isna().any() else -12.0
    return build_prior(f, coefs, new_em, p["tempo_carry"])


def blend_roster(prior, rp, blend):
    if rp is None:
        return prior
    em_c = prior.o + prior.d
    em_r = (rp.o + rp.d).reindex(prior.index).fillna(em_c)
    adj = blend * (em_r - em_c)
    return prior.assign(o=prior.o + adj / 2, d=prior.d + adj / 2)


def run(games, seasons, p=DEFAULTS, verbose=False, F=None):
    out = []
    prior, last_final = None, None
    allseasons = sorted(games.season.unique())
    for s in allseasons:
        gs = games[games.season == s]
        teams = set(gs.home_id) | set(gs.away_id)
        prior = make_prior(last_final, p, teams) if last_final is not None else None
        if F is not None and p.get("roster_blend", 0) > 0 and prior is not None:
            prior = blend_roster(prior, roster_prior_for(s, F, p), p["roster_blend"])
        if s in seasons:
            for mon in mondays(gs):
                wk = gs[(gs.date >= mon) & (gs.date < mon + pd.Timedelta(days=7))]
                if len(wk) == 0:
                    continue
                R, meta = fit(gs, mon, prior, p)
                m, tot, ph, poss = predict(R, meta, wk.home_id.values, wk.away_id.values, wk.neutral.values, p)
                o = wk[["game_id", "season", "date", "home_id", "away_id", "neutral", "ncaa_tourney", "home_pts", "away_pts", "poss"]].copy()
                o["week_of"] = mon; o["pred_margin"] = m; o["pred_total"] = tot; o["p_home"] = ph; o["pred_poss"] = poss
                out.append(o)
        last_final, _ = fit(gs, gs.date.max() + pd.Timedelta(days=1), prior, p)
    return pd.concat(out, ignore_index=True)


def score(df):
    y = (df.home_pts > df.away_pts).astype(float)
    act = df.home_pts - df.away_pts
    p = df.p_home.clip(1e-4, 1 - 1e-4)
    return pd.Series({
        "n": len(df),
        "margin_mae": np.mean(np.abs(act - df.pred_margin)),
        "margin_rmse": np.sqrt(np.mean((act - df.pred_margin) ** 2)),
        "total_mae": np.mean(np.abs(df.home_pts + df.away_pts - df.pred_total)),
        "logloss": -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)),
        "brier": np.mean((p - y) ** 2),
        "accuracy": np.mean((p > 0.5) == (y == 1)),
    })
