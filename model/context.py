"""Game context features: rest days, travel distance, altitude, and missing-player value."""
import numpy as np, pandas as pd

HIGH_ALT = {("Laramie", "WY"), ("Colorado Springs", "CO"), ("USAF Academy", "CO"), ("Air Force Academy", "CO"), ("Boulder", "CO"),
            ("Fort Collins", "CO"), ("Denver", "CO"), ("Greeley", "CO"), ("Albuquerque", "NM"), ("Provo", "UT"), ("Orem", "UT"),
            ("Salt Lake City", "UT"), ("Logan", "UT"), ("Ogden", "UT"), ("Reno", "NV"), ("Bozeman", "MT"), ("Pocatello", "ID"),
            ("Flagstaff", "AZ"), ("Cedar City", "UT"), ("Golden", "CO"), ("Pueblo", "CO")}
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
          "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
          "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
          "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
          "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
          "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
          "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
          "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}


def geocoder(path):
    c = pd.read_csv(path)
    c["key"] = c.CITY.str.lower().str.strip() + "|" + c.STATE_CODE
    return c.drop_duplicates("key").set_index("key")[["LATITUDE", "LONGITUDE"]]


def _st(s):
    s = str(s or "").strip()
    return STATES.get(s, s.upper()[:2] if len(s) <= 3 else s)


def km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def venues(sched, geo):
    """sched needs game_id, home_id, neutral, venue_city, venue_state. Returns per-game venue lat/lon and team home lat/lon."""
    s = sched.copy()
    s["vkey"] = s.venue_city.astype(str).str.lower().str.strip() + "|" + s.venue_state.map(_st)
    s = s.join(geo, on="vkey")
    s["alt"] = [(str(c), _st(st)) in HIGH_ALT for c, st in zip(s.venue_city, s.venue_state)]
    homes = s[~s.neutral.astype(bool) & s.LATITUDE.notna()]
    home = homes.groupby("home_id").agg(hlat=("LATITUDE", lambda x: x.mode().iloc[0]), hlon=("LONGITUDE", lambda x: x.mode().iloc[0]),
                                        halt=("alt", lambda x: x.mode().iloc[0]))
    return s.set_index("game_id")[["LATITUDE", "LONGITUDE", "alt"]], home


def context(games, vg, home):
    """Per game: rest_diff (home minus away, days capped 1..7), travel_diff (away minus home, 1000 km), alt (+1 home at altitude vs lowland visitor)."""
    g = games.copy()
    long = pd.concat([g[["game_id", "date", "home_id"]].rename(columns={"home_id": "t"}), g[["game_id", "date", "away_id"]].rename(columns={"away_id": "t"})])
    long = long.sort_values("date")
    long["rest"] = long.groupby("t").date.diff().dt.days.clip(1, 7).fillna(7)
    rest = long.set_index(["game_id", "t"]).rest
    g["rest_h"] = [rest.get((i, t), 7) for i, t in zip(g.game_id, g.home_id)]
    g["rest_a"] = [rest.get((i, t), 7) for i, t in zip(g.game_id, g.away_id)]
    g = g.join(vg, on="game_id")
    for side in ("home", "away"):
        hh = home.reindex(g[f"{side}_id"].values)
        d = km(hh.hlat.values, hh.hlon.values, g.LATITUDE.values, g.LONGITUDE.values)
        g[f"km_{side}"] = np.where(np.isnan(d), np.nan, d)
    g["km_home"] = np.where(g.neutral, g.km_home, 0.0)
    g["rest_diff"] = g.rest_h - g.rest_a
    g["travel_diff"] = ((g.km_away - g.km_home) / 1000).fillna(0)
    away_alt = home.halt.reindex(g.away_id.values).fillna(False).values.astype(bool)
    g["alt_adv"] = np.where(~g.neutral & g.alt.fillna(False).astype(bool) & ~away_alt, 1.0, 0.0)
    return g[["game_id", "rest_diff", "travel_diff", "alt_adv"]]


def game_score(df):
    """Hollinger game score from box score columns."""
    return (df.points + 0.4 * df.field_goals_made - 0.7 * df.field_goals_attempted - 0.4 * (df.free_throws_attempted - df.free_throws_made)
            + 0.7 * df.offensive_rebounds + 0.3 * df.defensive_rebounds + df.steals + 0.7 * df.assists + 0.7 * df.blocks
            - 0.4 * df.fouls - df.turnovers)


def absences(pb):
    """For each team game: summed season-to-date average game score of key players (20+ min per team game, 3+ team games)
    who did not play in the team's PREVIOUS game, the same signal available live before tip."""
    d = pb.copy()
    d["minutes"] = pd.to_numeric(d.minutes, errors="coerce").fillna(0)
    d.loc[d.did_not_play.fillna(False).astype(bool), "minutes"] = 0
    for c in ["points", "field_goals_made", "field_goals_attempted", "free_throws_attempted", "free_throws_made", "offensive_rebounds",
              "defensive_rebounds", "steals", "assists", "blocks", "fouls", "turnovers"]:
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
    d["gs"] = game_score(d)
    d["date"] = pd.to_datetime(d.game_date)
    out = []
    for tid, t in d.groupby("team_id"):
        games = t.drop_duplicates("game_id").sort_values("date")[["game_id", "date"]].reset_index(drop=True)
        piv_m = t.pivot_table(index="game_id", columns="athlete_id", values="minutes", aggfunc="sum").reindex(games.game_id).fillna(0)
        piv_g = t.pivot_table(index="game_id", columns="athlete_id", values="gs", aggfunc="sum").reindex(games.game_id).fillna(0)
        cm, cg = piv_m.cumsum().shift(1), piv_g.cumsum().shift(1)  # totals before this game
        n = np.arange(len(games))[:, None]
        mpg = cm.values / np.maximum(n, 1); gpg = cg.values / np.maximum(n, 1)
        prev_played = (piv_m.shift(1).values > 0)
        key = (mpg >= 20) & (n >= 3)
        missing = key & ~prev_played
        val = np.where(missing, gpg, 0.0).sum(1)
        out.append(pd.DataFrame(dict(game_id=games.game_id.values, team_id=tid, absent_gs=val)))
    return pd.concat(out, ignore_index=True)
