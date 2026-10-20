"""Live ESPN ingestion: schedules, final box scores, and betting lines (free public endpoints).

ESPN does not keep odds on finished games, so lines must be captured BEFORE tip and archived.
"""
import json, time, urllib.request
import numpy as np, pandas as pd

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
UA = {"User-Agent": "Mozilla/5.0 (cbb-predictor research project)"}


def _get(url, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))


def d1_teams():
    d = _get(f"{SITE}/teams?groups=50&limit=500")
    rows = []
    for t in d["sports"][0]["leagues"][0]["teams"]:
        t = t["team"]
        logo = (t.get("logos") or [{}])[0].get("href", "")
        rows.append(dict(team_id=int(t["id"]), name=t.get("shortDisplayName"), full=t.get("displayName"), location=t.get("location"),
                         abbr=t.get("abbreviation"), color=t.get("color"), logo=logo))
    return pd.DataFrame(rows).set_index("team_id")


def _et_date(iso):
    """Game date in US Eastern time (ESPN start times are UTC)."""
    return pd.Timestamp(iso).tz_convert("America/New_York").strftime("%Y-%m-%d")


def _parse_odds(comp, home_abbr, away_abbr):
    odds = comp.get("odds") or []
    if not odds:
        return {}
    o = odds[0]
    out = {"provider": (o.get("provider") or {}).get("name"), "details": o.get("details"),
           "total": o.get("overUnder")}
    spread = o.get("spread")
    det = (o.get("details") or "").strip()
    home_line = None
    # details look like "DUKE -7.5" or "EVEN"; convert to home spread (negative = home favored)
    if det.upper().startswith("EVEN") or det.upper() == "PK":
        home_line = 0.0
    elif det:
        parts = det.rsplit(" ", 1)
        try:
            val = float(parts[1])
            fav = parts[0].strip().upper()
            if fav == (home_abbr or "").upper():
                home_line = val
            elif fav == (away_abbr or "").upper():
                home_line = -val
        except (IndexError, ValueError):
            pass
    if home_line is None and spread is not None:
        try:
            home_line = float(spread)  # ESPN 'spread' is quoted from the home side
        except (TypeError, ValueError):
            pass
    out["home_spread"] = home_line
    for side in ("home", "away"):
        ml = (o.get(f"{side}TeamOdds") or {}).get("moneyLine")
        out[f"{side}_ml"] = ml
    return out


def scoreboard(date_str):
    """date_str YYYYMMDD -> list of game dicts (D-I group)."""
    d = _get(f"{SITE}/scoreboard?dates={date_str}&groups=50&limit=500")
    games = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        comp = {x["homeAway"]: x for x in c["competitors"]}
        h, a = comp.get("home"), comp.get("away")
        if not h or not a:
            continue
        st = c["status"]["type"]
        notes = " ".join(n.get("headline", "") for n in c.get("notes", []))
        g = dict(game_id=int(e["id"]), season=e["season"]["year"], season_type=e["season"]["type"],
                 start=e["date"], date=_et_date(e["date"]),
                 home_id=int(h["team"]["id"]), away_id=int(a["team"]["id"]),
                 home_name=h["team"].get("shortDisplayName"), away_name=a["team"].get("shortDisplayName"),
                 home_abbr=h["team"].get("abbreviation"), away_abbr=a["team"].get("abbreviation"),
                 home_conf=h["team"].get("conferenceId"), away_conf=a["team"].get("conferenceId"),
                 home_rank=(h.get("curatedRank") or {}).get("current"), away_rank=(a.get("curatedRank") or {}).get("current"),
                 neutral=bool(c.get("neutralSite")), conf_game=bool(c.get("conferenceCompetition")),
                 notes=notes, state=st.get("state"), completed=bool(st.get("completed")),
                 home_pts=float(h["score"]) if st.get("completed") else None,
                 away_pts=float(a["score"]) if st.get("completed") else None)
        g["odds"] = _parse_odds(c, g["home_abbr"], g["away_abbr"])
        games.append(g)
    return games


def _stat(stats, key):
    for s in stats:
        if s["name"] == key:
            return s.get("displayValue")
    return None


def boxscore(game_id):
    """Team box for one finished game -> dict with possessions inputs, or None."""
    d = _get(f"{SITE}/summary?event={game_id}")
    teams = d.get("boxscore", {}).get("teams", [])
    if len(teams) != 2:
        return None
    out = {}
    for t in teams:
        st = t.get("statistics", [])
        fg = _stat(st, "fieldGoalsMade-fieldGoalsAttempted"); ft = _stat(st, "freeThrowsMade-freeThrowsAttempted")
        try:
            fga = float(fg.split("-")[1]); fta = float(ft.split("-")[1])
            orb = float(_stat(st, "offensiveRebounds")); to = float(_stat(st, "totalTurnovers") or _stat(st, "turnovers"))
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        out[t["homeAway"]] = fga - orb + to + 0.44 * fta
    if "home" not in out or "away" not in out:
        return None
    players = []
    for t in d.get("boxscore", {}).get("players", []):
        tid = int(t["team"]["id"])
        for block in t.get("statistics", [])[:1]:
            names = block.get("names") or []
            mi = names.index("MIN") if "MIN" in names else 0
            for a in block.get("athletes", []):
                st = a.get("stats") or []
                try:
                    mins = float(st[mi]) if st and st[mi] not in ("", "--") else 0.0
                except (ValueError, IndexError):
                    mins = 0.0
                players.append(dict(team_id=tid, athlete_id=int(a["athlete"]["id"]), name=a["athlete"].get("displayName"),
                                    minutes=0.0 if a.get("didNotPlay") else mins, starter=bool(a.get("starter"))))
    return dict(home_poss_est=out["home"], away_poss_est=out["away"], poss=(out["home"] + out["away"]) / 2, players=players)


def conferences():
    d = _get(f"{SITE}/groups")
    out = {}
    for conf in d["groups"][0]["children"]:
        for t in conf.get("teams", []):
            out[int(t["id"])] = conf["name"]
    return out


def tournament_field(dates):
    """Real NCAA field from ESPN First Four + first-round games. dates: list of YYYYMMDD.
    A First Four game is identified as a tournament game between two teams with the same seed."""
    ff, r64 = [], []
    for d in dates:
        for x in scoreboard(d):
            note = x["notes"].upper()
            if "MEN'S BASKETBALL CHAMPIONSHIP" not in note or x["home_rank"] is None:
                continue
            reg = next((r for r in ["Midwest", "East", "South", "West"] if r.upper() in note), None)
            same = x["home_rank"] == x["away_rank"]
            for side in ("home", "away"):
                row = dict(team_id=x[f"{side}_id"], seed=x[f"{side}_rank"], region=reg)
                (ff if same else r64).append(row)
    ffids = {r["team_id"] for r in ff}
    field = pd.DataFrame(ff + [r for r in r64 if r["team_id"] not in ffids], columns=["team_id", "seed", "region"])
    return field.drop_duplicates("team_id").dropna(subset=["region", "seed"])
