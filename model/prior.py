"""Roster-based preseason prior: last season ratings adjusted for returning minutes and incoming transfers."""
import numpy as np, pandas as pd

FEATS = ["em1", "em1_rm", "rm", "ti_val", "ti_min", "em2"]


def roster_features(pm, finals, season, roster=None):
    """pm: player minutes (season, team_id, athlete_id, minutes); finals: {season: ratings DataFrame}.
    roster: optional DataFrame(team_id, athlete_id) of the new-season roster (live use). If None,
    players who log minutes for the team in `season` are treated as the roster (backtest approximation)."""
    last = pm[pm.season == season - 1]
    tot = last.groupby("team_id").minutes.sum()
    last = last.assign(share=last.minutes / last.team_id.map(tot))
    if roster is None:
        roster = pm[pm.season == season][["team_id", "athlete_id"]].drop_duplicates()
    R1 = finals.get(season - 1); R2 = finals.get(season - 2)
    teams = sorted(set(roster.team_id))
    rows = []
    prev_team = last.sort_values("minutes").drop_duplicates("athlete_id", keep="last").set_index("athlete_id")
    for t in teams:
        ath = set(roster[roster.team_id == t].athlete_id)
        mine = last[last.team_id == t]
        rm = mine[mine.athlete_id.isin(ath)].share.sum() if len(mine) else np.nan
        inc = prev_team.loc[prev_team.index.intersection(list(ath))]
        inc = inc[inc.team_id != t]
        ti_min = inc.share.sum()
        old_em = inc.team_id.map(R1.em) if R1 is not None else pd.Series(dtype=float)
        ti_val = (inc.share * old_em.fillna(-10)).sum()
        em1 = R1.em.get(t, np.nan) if R1 is not None else np.nan
        em2 = R2.em.get(t, np.nan) if R2 is not None else np.nan
        rows.append(dict(team_id=t, season=season, em1=em1, rm=rm, ti_min=ti_min, ti_val=ti_val, em2=em2,
                         od1=(R1.o.get(t, np.nan) - R1.d.get(t, np.nan)) if R1 is not None else np.nan,
                         t1=R1.t.get(t, np.nan) if R1 is not None else np.nan))
    f = pd.DataFrame(rows).set_index("team_id")
    f["em2"] = f.em2.fillna(f.em1)
    f["em1_rm"] = f.em1 * f.rm
    return f


def fit_coefs(F):
    F = F.dropna(subset=FEATS + ["target"])
    X = np.c_[np.ones(len(F)), F[FEATS].values]
    beta, *_ = np.linalg.lstsq(X, F.target.values, rcond=None)
    return dict(zip(["const"] + FEATS, beta)), F


def build_prior(f, coefs, new_team_em, tempo_carry=0.7):
    f = f.copy()
    known = f.em1.notna()
    pe = pd.Series(new_team_em, index=f.index, dtype=float)
    ff = f[known].fillna({"rm": 0.5, "em2": 0})
    ff["em1_rm"] = ff.em1 * ff.rm
    pe[known] = coefs["const"] + sum(coefs[k] * ff[k] for k in FEATS)
    od = f.od1.fillna(0) * 0.4
    return pd.DataFrame({"o": pe / 2 + od / 2, "d": pe / 2 - od / 2, "t": f.t1.fillna(0) * tempo_carry})
