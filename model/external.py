"""Live external sources (keys come from GitHub secrets via environment variables).

torvik_current: Bart Torvik's latest adjusted ratings via cbbdata (third model in the second stage).
cbbd_lines:     betting lines from CollegeBasketballData.com, used where ESPN has no line.
Both return empty results on any failure so the site keeps running on its own model.
"""
import io, json, os, urllib.parse, urllib.request
import pandas as pd
from model import net

ALIAS = {"Appalachian St.": "App State", "Arkansas Little Rock": "Little Rock", "College of Charleston": "Charleston", "Connecticut": "UConn",
         "Detroit": "Detroit Mercy", "Dixie St.": "Utah Tech", "Fort Wayne": "Purdue Fort Wayne", "IPFW": "Purdue Fort Wayne", "IUPUI": "IU Indianapolis",
         "Grambling St.": "Grambling", "Houston Baptist": "Houston Christian", "Illinois Chicago": "UIC", "LIU Brooklyn": "Long Island University",
         "LIU": "Long Island University", "Louisiana Lafayette": "Louisiana", "Louisiana Monroe": "UL Monroe", "McNeese St.": "McNeese", "Miami FL": "Miami",
         "Miami OH": "Miami (OH)", "Mississippi": "Ole Miss", "Nebraska Omaha": "Omaha", "Nicholls St.": "Nicholls", "North Carolina St.": "NC State",
         "Southeast Missouri St.": "Southeast Missouri State", "Tennessee Martin": "UT Martin", "Texas A&M Corpus Chris": "Texas A&M-Corpus Christi",
         "Texas A&M Corpus Christi": "Texas A&M-Corpus Christi", "UMKC": "Kansas City", "Cal St. Bakersfield": "Cal State Bakersfield",
         "Cal St. Fullerton": "Cal State Fullerton", "Cal St. Northridge": "Cal State Northridge", "Gardner Webb": "Gardner-Webb", "Loyola MD": "Loyola Maryland",
         "Bethune Cookman": "Bethune-Cookman", "Arkansas Pine Bluff": "Arkansas-Pine Bluff", "Texas A&M Commerce": "East Texas A&M",
         "Southeastern Louisiana": "SE Louisiana", "Penn": "Pennsylvania", "USC Upstate": "South Carolina Upstate", "SIU Edwardsville": "SIUE",
         "Seattle": "Seattle U", "UCSB": "UC Santa Barbara", "N.C. State": "NC State", "Cal Baptist": "California Baptist", "Sam Houston St.": "Sam Houston"}


def _get(url, headers=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "cbb-predictor", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def name_map(names, espn_teams):
    """Map external team names to ESPN ids: NET-style matcher first, then aliases."""
    cand = {}
    for t in espn_teams:
        for k in ("location", "displayName", "shortDisplayName", "abbreviation"):
            if t.get(k):
                cand.setdefault(net.norm(t[k]), int(t["id"]))
    m = net.match(pd.DataFrame({"School": sorted(set(names))}), espn_teams)
    out = {r["School"]: tid for tid, r in m.items()}
    for s in names:
        if s not in out:
            a = ALIAS.get(s, s)
            if net.norm(a) in cand:
                out[s] = cand[net.norm(a)]
    return out


def cbbdata_key():
    user, pw = os.environ.get("CBD_USER", "").strip(), os.environ.get("CBD_PW", "").strip()
    if user and pw:
        try:
            req = urllib.request.Request("https://www.cbbdata.com/api/auth/login", data=json.dumps({"username": user, "password": pw}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "cbb-predictor"})
            js = json.loads(urllib.request.urlopen(req, timeout=60).read())
            k = js[0] if isinstance(js, list) else (list(js.values())[0] if isinstance(js, dict) else js)
            if k:
                return str(k)
        except Exception:
            pass
    return os.environ.get("CBBDATA_API_KEY", "").strip()


def torvik_current(season, espn_teams):
    """{espn_id: adj_o minus adj_d} from Torvik's most recent daily ratings this season, or {} if unavailable."""
    key = cbbdata_key()
    if not key:
        return {}, "no cbbdata credentials"
    try:
        raw = _get("https://www.cbbdata.com/api/torvik/ratings/archive?" + urllib.parse.urlencode({"year": season, "key": key}), timeout=300)
        df = pd.read_parquet(io.BytesIO(raw))
    except Exception as e:
        return {}, f"cbbdata request failed ({type(e).__name__})"
    if df.empty:
        return {}, "no Torvik ratings published yet this season"
    df["date"] = pd.to_datetime(df.date)
    df = df.sort_values("date").groupby("team").tail(1)
    ids = name_map(df.team.tolist(), espn_teams)
    out = {ids[t]: float(o - d) for t, o, d in zip(df.team, df.adj_o, df.adj_d) if t in ids}
    return out, f"Torvik ratings as of {df.date.max().date()}, {len(out)} teams"


PROVIDER_ORDER = ["Draft Kings", "DraftKings", "ESPN BET", "Bovada", "consensus", "teamrankings"]


def cbbd_lines(season, start, end, espn_teams):
    """[(home_id, away_id, et_date, home_spread)] from CollegeBasketballData.com for a date window."""
    key = os.environ.get("CBBD_API_KEY", "").strip()
    if not key:
        return []
    q = {"season": season, "startDateRange": start, "endDateRange": end}
    try:
        js = json.loads(_get("https://api.collegebasketballdata.com/lines?" + urllib.parse.urlencode(q), {"Authorization": f"Bearer {key}"}))
    except Exception:
        return []
    names = [g.get("homeTeam") for g in js] + [g.get("awayTeam") for g in js]
    ids = name_map([n for n in names if n], espn_teams)
    out = []
    for g in js:
        lines = {ln.get("provider"): ln.get("spread") for ln in (g.get("lines") or []) if ln.get("spread") is not None}
        sp = next((lines[p] for p in PROVIDER_ORDER if p in lines), None)
        h, a = ids.get(g.get("homeTeam")), ids.get(g.get("awayTeam"))
        if sp is None or h is None or a is None:
            continue
        et = pd.Timestamp(g.get("startDate")).tz_convert("America/New_York").strftime("%Y-%m-%d")
        out.append((h, a, et, float(sp)))
    return out


def cbbd_finals(season, start, end, espn_teams):
    """Backup scores and possessions from CollegeBasketballData.com /games/teams for a date window.
    Returns [dict(home_id, away_id, date, home_pts, away_pts, poss, neutral, conf_game)]."""
    key = os.environ.get("CBBD_API_KEY", "").strip()
    if not key:
        return []
    q = {"season": season, "startDateRange": start, "endDateRange": end}
    try:
        js = json.loads(_get("https://api.collegebasketballdata.com/games/teams?" + urllib.parse.urlencode(q), {"Authorization": f"Bearer {key}"}))
    except Exception:
        return []
    ids = name_map([r.get("team") for r in js if r.get("team")] + [r.get("opponent") for r in js if r.get("opponent")], espn_teams)

    def poss(s):
        try:
            return s["fieldGoals"]["attempted"] - s["rebounds"]["offensive"] + s["turnovers"]["total"] + 0.44 * s["freeThrows"]["attempted"]
        except (KeyError, TypeError):
            return None
    out = []
    for r in js:
        if not r.get("isHome"):
            continue
        ts, os_ = r.get("teamStats") or {}, r.get("opponentStats") or {}
        hp, ap = (ts.get("points") or {}).get("total"), (os_.get("points") or {}).get("total")
        p1, p2 = poss(ts), poss(os_)
        h, a = ids.get(r.get("team")), ids.get(r.get("opponent"))
        if None in (hp, ap, p1, p2, h, a):
            continue
        et = pd.Timestamp(r.get("startDate")).tz_convert("America/New_York").strftime("%Y-%m-%d")
        out.append(dict(home_id=h, away_id=a, date=et, home_pts=float(hp), away_pts=float(ap), poss=(p1 + p2) / 2,
                        neutral=bool(r.get("neutralSite")), conf_game=bool(r.get("conferenceGame")), notes=r.get("notes") or ""))
    return out
