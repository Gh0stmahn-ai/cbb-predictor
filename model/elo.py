"""Second, independent model: Elo with margin of victory (FiveThirtyEight-style update).
Ratings move game by game; predictions use ratings frozen at the start of each Monday week."""
import numpy as np, pandas as pd

ELO = dict(k=18.0, home=75.0, carry=0.9, scale=24.9, new=1350.0)  # tuned on 2014 to 2020


def _mult(mov, diff):
    return np.log(abs(mov) + 1) * 2.2 / (diff * 0.001 + 2.2)


def run_elo(games, p=ELO, seasons_out=None):
    """Walk-forward over all games. Returns DataFrame game_id, elo_margin (home minus away, pre-week)."""
    R = {}
    out = []
    for s in sorted(games.season.unique()):
        gs = games[games.season == s].sort_values("date")
        R = {t: 1500 + p["carry"] * (r - 1500) for t, r in R.items()}
        start = gs.date.min() - pd.Timedelta(days=gs.date.min().weekday())
        for mon in pd.date_range(start, gs.date.max() + pd.Timedelta(days=7), freq="7D"):
            wk = gs[(gs.date >= mon) & (gs.date < mon + pd.Timedelta(days=7))]
            if wk.empty:
                continue
            rh = np.array([R.get(t, p["new"]) for t in wk.home_id]); ra = np.array([R.get(t, p["new"]) for t in wk.away_id])
            hadv = np.where(wk.neutral.values, 0.0, p["home"])
            out.append(pd.DataFrame(dict(game_id=wk.game_id.values, elo_diff=rh - ra + hadv)))
            for r in wk.itertuples():
                h0, a0 = R.get(r.home_id, p["new"]), R.get(r.away_id, p["new"])
                d = h0 - a0 + (0 if r.neutral else p["home"])
                e = 1 / (1 + 10 ** (-d / 400))
                mov = r.home_pts - r.away_pts
                res = 1.0 if mov > 0 else 0.0
                w_diff = d if mov > 0 else -d
                ch = p["k"] * _mult(mov, w_diff) * (res - e)
                R[r.home_id] = h0 + ch; R[r.away_id] = a0 - ch
    o = pd.concat(out, ignore_index=True)
    o["elo_margin"] = o.elo_diff / p["scale"]
    return o, R


def update(R, games, p=ELO):
    """Apply completed games (sorted by date) to ratings dict R in place."""
    for r in games.sort_values("date").itertuples():
        h0, a0 = R.get(r.home_id, p["new"]), R.get(r.away_id, p["new"])
        d = h0 - a0 + (0 if r.neutral else p["home"])
        e = 1 / (1 + 10 ** (-d / 400))
        mov = r.home_pts - r.away_pts
        ch = p["k"] * _mult(mov, d if mov > 0 else -d) * ((1.0 if mov > 0 else 0.0) - e)
        R[r.home_id] = h0 + ch; R[r.away_id] = a0 - ch
    return R


def margin(R, home_ids, away_ids, neutral, p=ELO):
    rh = np.array([R.get(t, p["new"]) for t in home_ids]); ra = np.array([R.get(t, p["new"]) for t in away_ids])
    return (rh - ra + np.where(np.asarray(neutral, bool), 0.0, p["home"])) / p["scale"]
