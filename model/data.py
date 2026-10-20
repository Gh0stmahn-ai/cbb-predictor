"""Load ESPN men's college basketball team box scores + schedules into one game table.

Source: sportsdataverse-data releases (free, public). One row per game with both teams.
"""
import os
import numpy as np
import pandas as pd

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
RAW = os.path.join(os.path.dirname(__file__), "..", "raw")


def _fetch(kind, season):
    os.makedirs(RAW, exist_ok=True)
    if kind == "box":
        fn, url = f"team_box_{season}.parquet", f"{BASE}/espn_mens_college_basketball_team_boxscores/team_box_{season}.parquet"
    else:
        fn, url = f"sched_{season}.parquet", f"{BASE}/espn_mens_college_basketball_schedules/mbb_schedule_{season}.parquet"
    path = os.path.join(RAW, fn)
    if not os.path.exists(path):
        import urllib.request
        urllib.request.urlretrieve(url, path)
    return pd.read_parquet(path)


def possessions(fga, orb, to, fta, c=0.44):
    return fga - orb + to + c * fta


def load_season(season):
    box = _fetch("box", season)
    sch = _fetch("sched", season)
    box = box[box["team_score"].notna() & box["field_goals_attempted"].notna()].copy()
    box["poss_est"] = possessions(box.field_goals_attempted, box.offensive_rebounds,
                                  box.total_turnovers.fillna(box.turnovers), box.free_throws_attempted)
    home = box[box.team_home_away == "home"].set_index("game_id")
    away = box[box.team_home_away == "away"].set_index("game_id")
    ids = home.index.intersection(away.index)
    home, away = home.loc[ids], away.loc[ids]
    g = pd.DataFrame({
        "game_id": ids,
        "season": season,
        "date": pd.to_datetime(home.game_date.values),
        "home_id": home.team_id.values, "away_id": away.team_id.values,
        "home_name": home.team_short_display_name.values, "away_name": away.team_short_display_name.values,
        "home_pts": home.team_score.values.astype(float), "away_pts": away.team_score.values.astype(float),
        "home_poss_est": home.poss_est.values, "away_poss_est": away.poss_est.values,
        "season_type": home.season_type.values,
    })
    g["poss"] = (g.home_poss_est + g.away_poss_est) / 2
    s = sch.drop_duplicates("game_id").set_index("game_id")
    g["neutral"] = g.game_id.map(s["neutral_site"]).fillna(False).astype(bool).values
    g["conf_game"] = g.game_id.map(s["conference_competition"]).fillna(False).astype(bool).values
    g["notes"] = g.game_id.map(s["notes_headline"]).fillna("").values
    g["home_conf"] = g.game_id.map(s["home_conference_id"]).values
    g["away_conf"] = g.game_id.map(s["away_conference_id"]).values
    # Keep D-I vs D-I only: a team counts as D-I if it appears in >= 15 box score games this season
    counts = pd.concat([g.home_id, g.away_id]).value_counts()
    d1 = set(counts[counts >= 15].index)
    g = g[g.home_id.isin(d1) & g.away_id.isin(d1)]
    g = g[(g.poss > 40) & (g.poss < 110)]  # drop corrupt rows
    g["ncaa_tourney"] = g.notes.str.contains("Men's Basketball Championship", case=False)
    return g.sort_values("date").reset_index(drop=True)


def load_all(seasons):
    return pd.concat([load_season(s) for s in seasons], ignore_index=True)
