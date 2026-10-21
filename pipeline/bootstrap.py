"""Rebuild state/ seed files on a fresh repo from the free sportsdataverse archive.

Creates state/finals_{s}.csv for the last three completed seasons and
state/player_minutes_{s}.csv for the last two. Safe to rerun; skips files that exist.
"""
import json, os, sys, urllib.request
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from model.data import load_season, BASE
from model.ratings import fit, make_prior, DEFAULTS

STATE = os.path.join(ROOT, "state")


def main():
    os.makedirs(STATE, exist_ok=True)
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    cur = int(cfg["current_season"])
    seasons = list(range(cur - 5, cur))  # warm-up chain so carryover priors settle
    need = [s for s in seasons[-3:] if not os.path.exists(os.path.join(STATE, f"finals_{s}.csv"))]
    if need:
        last = None
        for s in seasons:
            g = load_season(s)
            pr = make_prior(last, DEFAULTS, set(g.home_id) | set(g.away_id)) if last is not None else None
            last, _ = fit(g, g.date.max() + pd.Timedelta(days=1), pr, DEFAULTS)
            if s in seasons[-3:]:
                last[["o", "d", "t", "em", "games"]].to_csv(os.path.join(STATE, f"finals_{s}.csv"))
                print("bootstrap: finals", s, len(last))
    ep = os.path.join(STATE, f"elo_start_{cur}.json")
    if not os.path.exists(ep):
        from model.elo import run_elo, ELO
        g = pd.concat([load_season(s) for s in seasons], ignore_index=True)
        _, R = run_elo(g, ELO)
        R = {str(int(t)): 1500 + ELO["carry"] * (r - 1500) for t, r in R.items()}
        json.dump(R, open(ep, "w"))
        print("bootstrap: Elo season start", len(R))
    for s in (cur - 2, cur - 1):
        path = os.path.join(STATE, f"player_minutes_{s}.csv")
        if os.path.exists(path):
            continue
        local = os.path.join(ROOT, "raw", f"pb_{s}.parquet")
        os.makedirs(os.path.dirname(local), exist_ok=True)
        if not os.path.exists(local):
            urllib.request.urlretrieve(f"{BASE}/espn_mens_college_basketball_player_boxscores/player_box_{s}.parquet", local)
        d = pd.read_parquet(local, columns=["team_id", "athlete_id", "minutes"])
        d["minutes"] = pd.to_numeric(d.minutes, errors="coerce").fillna(0)
        d.groupby(["team_id", "athlete_id"]).minutes.sum().reset_index().to_csv(path, index=False)
        print("bootstrap: player minutes", s)


if __name__ == "__main__":
    main()
