"""Production pipeline. Runs on GitHub Actions.

  python -m pipeline.run daily            # every day: ingest finals, capture lines, refit, lock, grade, publish
  python -m pipeline.run daily --asof 2026-02-16 --season 2026   # dry run on a past date

Monday runs also freeze a weekly snapshot (never edited afterwards).
"""
import argparse, io, json, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd
from scipy.stats import norm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from model.ratings import fit, predict, DEFAULTS, make_prior
from model.prior import roster_features, build_prior
from model.bracket import project_field, simulate, conf_tourney_odds, field_odds
from model import net as netmod
from model import elo as elomod
from model import external
from model.context import km, HIGH_ALT, _st
STAGE2 = json.load(open(os.path.join(ROOT, "model", "stage2.json")))
DEFAULTS["sigma0"] = STAGE2["sigma0"]
SIGMA_SCALE = 1.0
from model import live

STATE = os.path.join(ROOT, "state")
OUT = os.path.join(ROOT, "docs", "data")
CFG = json.load(open(os.path.join(ROOT, "config.json")))
WARN = []  # problems shown as a banner on the site


def p_(name):
    return os.path.join(STATE, name)


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else float(x)
    if hasattr(x, "isoformat"):
        return None if pd.isna(x) else x.isoformat()
    try:
        return None if pd.isna(x) else str(x)
    except (TypeError, ValueError):
        return str(x)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(_clean(obj), f, separators=(",", ":"), allow_nan=False)


def r1(x, k=1):
    return None if x is None or pd.isna(x) else round(float(x), k)


# ---------------- ingestion ----------------
def load_games(season):
    path = p_(f"games_{season}.csv")
    if os.path.exists(path):
        g = pd.read_csv(path, parse_dates=["date"])
        return g
    return pd.DataFrame(columns=["game_id", "season", "date", "home_id", "away_id", "home_name", "away_name", "home_pts",
                                 "away_pts", "poss", "neutral", "conf_game", "notes", "season_type"])


def ingest(season, asof, d1):
    g = load_games(season)
    have = set(g.game_id.astype(int)) if len(g) else set()
    start = (g.date.max() - timedelta(days=2)) if len(g) else pd.Timestamp(CFG["season_start"][str(season)]) - timedelta(days=1)
    new, new_players = [], []
    day = pd.Timestamp(start)
    while day < asof:
        for x in live.scoreboard(day.strftime("%Y%m%d")):
            if not x["completed"] or x["game_id"] in have or x["season"] != season:
                continue
            if x["home_id"] not in d1 or x["away_id"] not in d1:
                continue
            bx = live.boxscore(x["game_id"])
            if bx is None or not (40 < bx["poss"] < 110):
                continue
            for pl in bx.get("players", []):
                new_players.append(dict(game_id=x["game_id"], date=x["date"], **pl))
            new.append(dict(game_id=x["game_id"], season=season, date=pd.Timestamp(x["date"]), home_id=x["home_id"],
                            away_id=x["away_id"], home_name=x["home_name"], away_name=x["away_name"],
                            home_pts=x["home_pts"], away_pts=x["away_pts"], poss=bx["poss"], neutral=x["neutral"],
                            conf_game=x["conf_game"], notes=x["notes"], season_type=x["season_type"]))
            have.add(x["game_id"])
        day += timedelta(days=1)
    if new:
        g = pd.concat([g, pd.DataFrame(new)], ignore_index=True)
        g.to_csv(p_(f"games_{season}.csv"), index=False)
    if new_players:
        pp = p_(f"players_{season}.csv")
        old = pd.read_csv(pp) if os.path.exists(pp) else pd.DataFrame()
        pd.concat([old, pd.DataFrame(new_players)], ignore_index=True).drop_duplicates(["game_id", "athlete_id"]).to_csv(pp, index=False)
    print(f"ingest: {len(new)} new games, {len(g)} total")
    g["date"] = pd.to_datetime(g["date"])
    g["neutral"] = g.neutral.astype(bool)
    return g


def capture_lines(season, now, games_ahead, espn_teams=None):
    path = p_(f"lines_{season}.csv")
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    rows = []
    # backup source: CollegeBasketballData.com, used only where ESPN shows no line
    backup = {}
    if espn_teams and games_ahead:
        dates = sorted(x["date"] for x in games_ahead)
        for h, a, d, sp in external.cbbd_lines(season, dates[0], (pd.Timestamp(dates[-1]) + timedelta(days=1)).strftime("%Y-%m-%d"), espn_teams):
            backup[(h, a, d)] = sp
    for x in games_ahead:
        o = dict(x.get("odds") or {})
        if o.get("home_spread") is None and (x["home_id"], x["away_id"], x["date"]) in backup:
            o = dict(home_spread=backup[(x["home_id"], x["away_id"], x["date"])], provider="CollegeBasketballData", details=None)
        if x["state"] != "pre" or o.get("home_spread") is None:
            continue
        rows.append(dict(game_id=x["game_id"], captured_at=now.isoformat(), home_spread=o.get("home_spread"),
                         total=o.get("total"), home_ml=o.get("home_ml"), away_ml=o.get("away_ml"),
                         provider=o.get("provider"), details=o.get("details")))
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(path, index=False)
    print(f"lines: captured {len(rows)} pregame lines")
    lines = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    return lines.sort_values("captured_at").drop_duplicates("game_id", keep="last").set_index("game_id") if len(lines) else pd.DataFrame()


# ---------------- prior ----------------
def season_prior(season, teams):
    path = p_(f"prior_{season}.csv")
    if os.path.exists(path):
        return pd.read_csv(path, index_col=0)
    finals = {s: pd.read_csv(p_(f"finals_{s}.csv"), index_col=0) for s in (season - 1, season - 2) if os.path.exists(p_(f"finals_{s}.csv"))}
    if season - 1 not in finals:
        return None
    base = make_prior(finals[season - 1], DEFAULTS, teams)
    try:
        pm = pd.read_csv(p_(f"player_minutes_{season - 1}.csv")).assign(season=season - 1)
        ros = []
        for t in teams:
            try:
                d = live._get(f"{live.SITE}/teams/{t}/roster")
                ros += [dict(team_id=t, athlete_id=int(a["id"])) for a in d.get("athletes", [])]
            except Exception:
                pass
        roster = pd.DataFrame(ros)
        f = roster_features(pm, finals, season, roster=roster)
        pc = json.load(open(os.path.join(ROOT, "model", "prior_coefs.json")))
        rp = build_prior(f, pc["coefs"], pc["new_team_em"], DEFAULTS["tempo_carry"])
        em_c = base.o + base.d
        adj = DEFAULTS["roster_blend"] * ((rp.o + rp.d).reindex(base.index).fillna(em_c) - em_c)
        base = base.assign(o=base.o + adj / 2, d=base.d + adj / 2)
        print(f"prior: roster prior built from {len(roster)} roster entries")
    except Exception as e:
        print("prior: roster prior unavailable, using carryover only:", e)
    base.to_csv(path)
    return base


# ---------------- blending ----------------
def blend_weight(ledger):
    """Weight on model margin vs market spread. Fixed until enough graded games with lines, then fit."""
    w0 = CFG["blend"]["model_weight_default"]
    d = ledger.dropna(subset=["home_spread", "home_pts", "model_margin"]) if len(ledger) else ledger
    if len(d) < CFG["blend"]["min_games_to_fit"]:
        return w0, CFG["blend"]["sigma_default"], len(d)
    act = d.home_pts - d.away_pts
    mkt = -d.home_spread
    x = d.model_margin - mkt
    w = float(np.clip(np.sum(x * (act - mkt)) / np.sum(x * x), 0, 1))
    sig = float(np.sqrt(np.mean((act - (w * d.model_margin + (1 - w) * mkt)) ** 2)))
    return w, sig, len(d)


def grade(ledger, games):
    if not len(ledger):
        return ledger
    res = games.drop_duplicates("game_id").set_index("game_id")
    ledger = ledger.copy()
    ledger["home_pts"] = ledger.game_id.map(res.home_pts)
    ledger["away_pts"] = ledger.game_id.map(res.away_pts)
    return ledger


def availability(season):
    """Key players (20+ minutes per team game) who did not play in their team's most recent game."""
    pp = p_(f"players_{season}.csv")
    if not os.path.exists(pp):
        return {}
    pl = pd.read_csv(pp)
    if pl.empty:
        return {}
    out = {}
    for tid, d in pl.groupby("team_id"):
        games = d.drop_duplicates("game_id").sort_values("date")
        if len(games) < 3:
            continue
        mpg = d.groupby(["athlete_id", "name"]).minutes.sum() / len(games)
        key = mpg[mpg >= 20]
        last = games.game_id.iloc[-1]
        played = set(d[(d.game_id == last) & (d.minutes > 0)].athlete_id)
        gs = d.groupby("athlete_id").gs.sum() / len(games) if "gs" in d else pd.Series(dtype=float)
        miss = [dict(name=n, mpg=round(float(v), 1), gs=round(float(gs.get(a, 0.0)), 1)) for (a, n), v in key.items() if a not in played]
        if miss:
            out[int(tid)] = miss
    return out


def net_ranks(season, espn_teams):
    """Official NET for the current season, or {} before it is published."""
    try:
        t, through = netmod.fetch()
        if not through or pd.Timestamp(through) < pd.Timestamp(f"{season - 1}-11-01"):
            return {}, None
        m = netmod.match(t, espn_teams)
        return {tid: dict(rank=int(r["Rank"]), q1=str(r.get("Quad 1", "")), rec=str(r.get("Record", ""))) for tid, r in m.items()}, through
    except Exception as e:
        WARN.append(f"Official NET rankings could not be loaded ({type(e).__name__}); the bracket uses model ratings instead.")
        return {}, None


def conf_records(games):
    cg = games[games.conf_game.astype(bool)]
    if cg.empty:
        return {}
    w = pd.concat([cg[cg.home_pts > cg.away_pts].home_id, cg[cg.away_pts > cg.home_pts].away_id]).value_counts()
    n = pd.concat([cg.home_id, cg.away_id]).value_counts()
    return (w.reindex(n.index).fillna(0) / n).to_dict()


SCHED_COLS = ["game_id", "date", "start", "home_id", "away_id", "home_name", "away_name", "neutral", "conf_game",
              "notes", "season_type", "state", "home_pts", "away_pts", "venue_city", "venue_state"]
_GEO = None
CITIES_URL = "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"


def geo():
    global _GEO
    if _GEO is None:
        path = os.path.join(ROOT, "model", "data", "us_cities.csv.gz")
        if not os.path.exists(path):
            import urllib.request
            os.makedirs(os.path.dirname(path), exist_ok=True)
            raw = pd.read_csv(io.BytesIO(urllib.request.urlopen(CITIES_URL, timeout=60).read()))
            raw[["STATE_CODE", "CITY", "LATITUDE", "LONGITUDE"]].round(4).to_csv(path, index=False, compression="gzip")
        c = pd.read_csv(path)
        c["key"] = c.CITY.str.lower().str.strip() + "|" + c.STATE_CODE
        _GEO = c.drop_duplicates("key").set_index("key")[["LATITUDE", "LONGITUDE"]]
    return _GEO


def loc(city, state):
    k = f"{str(city or '').lower().strip()}|{_st(state)}"
    g = geo()
    return (g.at[k, "LATITUDE"], g.at[k, "LONGITUDE"]) if k in g.index else (np.nan, np.nan)


def team_homes(sched):
    """Home city per team: most common venue of its non-neutral home games."""
    h = sched[~sched.neutral.astype(bool)].dropna(subset=["venue_city"]) if "venue_city" in sched else sched.iloc[0:0]
    out = {}
    for t, d in h.groupby("home_id"):
        c, st = d.groupby(["venue_city", "venue_state"]).size().idxmax()
        lat, lon = loc(c, st)
        out[int(t)] = dict(lat=lat, lon=lon, alt=(str(c), _st(st)) in HIGH_ALT)
    return out


def stage2(df, R_elo, homes, absent, torvik=None):
    """Final margin: research-tested second stage (model/stage2.json) on top of the rating model.
    Inputs per game: base model margin, Elo margin, extra travel by the visitor, altitude, missing-player value."""
    c = STAGE2["coef"]
    em = elomod.margin(R_elo, df.home_id.values, df.away_id.values, df.neutral.astype(bool).values)
    travel, alt = [], []
    for r in df.itertuples():
        vlat, vlon = loc(getattr(r, "venue_city", None), getattr(r, "venue_state", None))
        hh, aa = homes.get(int(r.home_id), {}), homes.get(int(r.away_id), {})
        ka = km(aa.get("lat", np.nan), aa.get("lon", np.nan), vlat, vlon)
        kh = km(hh.get("lat", np.nan), hh.get("lon", np.nan), vlat, vlon) if r.neutral else 0.0
        t = (ka - kh) / 1000
        travel.append(0.0 if np.isnan(t) else float(t))
        venue_alt = (str(getattr(r, "venue_city", "")), _st(getattr(r, "venue_state", ""))) in HIGH_ALT
        alt.append(1.0 if (not r.neutral and venue_alt and not aa.get("alt", False)) else 0.0)
    ab = np.array([absent.get(int(h), 0.0) - absent.get(int(a), 0.0) for h, a in zip(df.home_id, df.away_id)])
    feats = dict(model=df.base_margin.values, elo_margin=em, travel_diff=np.array(travel), alt_adv=np.array(alt), abs_diff=ab)
    final = sum(c[k] * v for k, v in feats.items())
    sigma = np.full(len(df), STAGE2["sigma0"])
    torvik = torvik or {}
    wt = STAGE2.get("with_torvik")
    if torvik and wt:
        poss = df["poss_hat"].values if "poss_hat" in df else np.full(len(df), 68.0)
        th = np.array([torvik.get(int(h), np.nan) for h in df.home_id]); ta = np.array([torvik.get(int(a), np.nan) for a in df.away_id])
        has = ~np.isnan(th) & ~np.isnan(ta)
        tm = np.where(has, (th - ta) * poss / 100, 0.0)
        ct = wt["coef"]
        with_t = sum(ct[k] * v for k, v in feats.items()) + ct["torvik_margin"] * tm
        final = np.where(has, with_t, final)
        sigma = np.where(has, wt["sigma0"], sigma)
    return final, em, sigma


def build_schedule(season, today, d1, ahead):
    """Full Division I schedule, opening night through Selection Sunday.
    Fully refetched on Mondays (or when missing); otherwise the next 7 days are refreshed from `ahead`."""
    path, metap = p_(f"schedule_{season}.csv"), p_(f"schedule_{season}.json")
    info = json.load(open(metap)) if os.path.exists(metap) else {}
    stale = (not os.path.exists(path)) or (today.weekday() == 0 and info.get("fetched") != today.date().isoformat()) \
        or "venue_city" not in pd.read_csv(path, nrows=1).columns
    sched = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=SCHED_COLS)
    if stale:
        start = pd.Timestamp(CFG["season_start"][str(season)])
        end = pd.Timestamp(CFG["selection_sunday"][str(season)])
        rows, day = [], start
        while day <= end:
            try:
                rows += [x for x in live.scoreboard(day.strftime("%Y%m%d")) if x["home_id"] in d1 and x["away_id"] in d1]
            except Exception as e:
                WARN.append(f"Part of the season schedule could not be refreshed ({day.date()}).")
            day += timedelta(days=1)
        if rows:
            sched = pd.DataFrame(rows).reindex(columns=SCHED_COLS)
            json.dump(dict(fetched=today.date().isoformat(), games=len(sched)), open(metap, "w"))
            print(f"schedule: {len(sched)} games fetched")
    if ahead:
        a = pd.DataFrame(ahead).reindex(columns=SCHED_COLS)
        sched = pd.concat([sched[~sched.game_id.isin(a.game_id)], a], ignore_index=True)
    sched = sched.drop_duplicates("game_id", keep="last").sort_values(["date", "start"]).reset_index(drop=True)
    sched.to_csv(path, index=False)
    return sched


def records(games):
    """Division I record and conference record from completed games."""
    rec = {}
    for r in games.itertuples():
        hw = r.home_pts > r.away_pts
        for t, w in ((r.home_id, hw), (r.away_id, not hw)):
            d = rec.setdefault(int(t), [0, 0, 0, 0])
            d[0 if w else 1] += 1
            if r.conf_game:
                d[2 if w else 3] += 1
    return rec


def season_sim(sched, games, R, meta, p, n_sims=2000, seed=3):
    """Monte Carlo of every unplayed scheduled game. Returns per team projected W, L, conf W, conf L."""
    done = set(games.game_id.astype(int))
    rem = sched[~sched.game_id.astype(int).isin(done) & sched.home_id.isin(R.index) & sched.away_id.isin(R.index)]
    rem = rem[~rem.notes.fillna("").str.upper().str.contains("CHAMPIONSHIP|NIT|CBI|CROWN")]
    rec = records(games)
    out = {t: dict(w=v[0], l=v[1], cw=v[2], cl=v[3]) for t, v in rec.items()}
    if len(rem):
        _, _, ph, _ = predict(R, meta, rem.home_id.values, rem.away_id.values, rem.neutral.astype(bool).values, p)
        rng = np.random.default_rng(seed)
        hw = rng.random((n_sims, len(rem))) < ph[None, :]
        exp_h = hw.mean(0)
        conf = rem.conf_game.astype(bool).values
        for i, (h, a) in enumerate(zip(rem.home_id.values, rem.away_id.values)):
            for t, w in ((int(h), exp_h[i]), (int(a), 1 - exp_h[i])):
                d = out.setdefault(t, dict(w=0, l=0, cw=0, cl=0))
                d["w"] += w; d["l"] += 1 - w
                if conf[i]:
                    d["cw"] += w; d["cl"] += 1 - w
    return out


def live_sigma(ledger):
    """Feedback: recalibrate the margin spread from this season's graded games, shrunk toward the backtest value."""
    s0, k = DEFAULTS["sigma0"], CFG.get("sigma_prior_games", 1000)
    if not len(ledger) or "home_pts" not in ledger:
        return s0, 0
    d = ledger.dropna(subset=["home_pts", "model_margin"])
    if len(d) < 100:
        return s0, len(d)
    mse = float(np.mean((d.home_pts - d.away_pts - d.model_margin) ** 2))
    return float(np.sqrt((len(d) * mse + k * s0 ** 2) / (len(d) + k))), len(d)


def rank_history(season, today, R, is_monday):
    path = p_(f"rank_history_{season}.csv")
    h = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame(columns=["date", "team_id", "rank", "em"])
    ranks = pd.Series(np.arange(1, len(R) + 1), index=R.sort_values("em", ascending=False).index)
    prev = h[pd.to_datetime(h.date) <= today - timedelta(days=6)]
    prev = prev[prev.date == prev.date.max()].set_index("team_id")["rank"] if len(prev) else pd.Series(dtype=float)
    if is_monday and (h.empty or h.date.max() != today.date().isoformat()):
        h = pd.concat([h, pd.DataFrame(dict(date=today.date().isoformat(), team_id=ranks.index, rank=ranks.values, em=R.em.reindex(ranks.index).round(2).values))])
        h.to_csv(path, index=False)
    return ranks, prev


# ---------------- main daily run ----------------
def daily(asof=None, season=None, weekly=None):
    now = datetime.now(timezone.utc) if asof is None else datetime.fromisoformat(asof).replace(hour=14, tzinfo=timezone.utc)
    today = pd.Timestamp(now.date())
    teams_df = live.d1_teams()
    d1 = set(teams_df.index)
    if season is None:
        sb = live.scoreboard(today.strftime("%Y%m%d"))
        season = sb[0]["season"] if sb else int(CFG["current_season"])
    season = int(season)
    games = ingest(season, today, d1)
    teams = set(teams_df.index)
    prior = season_prior(season, teams)
    R, meta = fit(games, today, prior, dict(DEFAULTS, preseason_meta=CFG.get("preseason_meta")))
    R = R[R.index.isin(d1)]

    # upcoming slate: today through the next 6 days
    ahead = []
    for k in range(7):
        ahead += [x for x in live.scoreboard((today + timedelta(days=k)).strftime("%Y%m%d")) if x["home_id"] in d1 and x["away_id"] in d1]
    lines = capture_lines(season, now, ahead, [dict(id=t, location=r.location, displayName=r.full, shortDisplayName=r["name"], abbreviation=r.abbr) for t, r in teams_df.iterrows()])
    up = pd.DataFrame(ahead)
    # context for the second stage: schedule venues, team home cities, Elo, missing players
    sched = build_schedule(season, today, d1, ahead)
    homes = team_homes(sched)
    avail = availability(season)
    absent = {t: sum(x.get("gs", 0.0) for x in v) for t, v in avail.items()}
    ep = p_(f"elo_start_{season}.json")
    R_elo = {int(k): v for k, v in json.load(open(ep)).items()} if os.path.exists(ep) else {}
    R_elo = elomod.update(R_elo, games) if len(games) else R_elo
    ledger_path = p_(f"ledger_{season}.csv")
    ledger = pd.read_csv(ledger_path) if os.path.exists(ledger_path) else pd.DataFrame()
    ledger = grade(ledger, games)
    # feedback: recalibrate the win-probability spread from this season's graded games
    sig_live, n_sig = live_sigma(ledger)
    global SIGMA_SCALE
    SIGMA_SCALE = sig_live / STAGE2["sigma0"]
    DEFAULTS["sigma0"] = sig_live
    torvik, tv_note = external.torvik_current(season, [dict(id=t, location=r.location, displayName=r.full, shortDisplayName=r["name"], abbreviation=r.abbr) for t, r in teams_df.iterrows()])
    print("torvik:", tv_note)
    w, sig_b, n_fit = blend_weight(ledger)
    if len(up):
        m, tot, ph, poss = predict(R, meta, up.home_id.values, up.away_id.values, up.neutral.values, DEFAULTS)
        up["base_margin"] = m; up["poss_hat"] = poss
        m, em, sg = stage2(up, R_elo, homes, absent, torvik)
        ph = norm.cdf(m / (sg * SIGMA_SCALE * np.sqrt(poss / 68.0)))
        up["model_margin"], up["model_total"], up["model_p"], up["elo_margin"] = m, tot, ph, em
        up["home_spread"] = up.game_id.map(lines.home_spread) if len(lines) else np.nan
        up["total_line"] = up.game_id.map(lines.total) if len(lines) else np.nan
        mkt = -up.home_spread
        up["blend_margin"] = np.where(mkt.notna(), w * up.model_margin + (1 - w) * mkt, np.nan)
        up["blend_p"] = np.where(mkt.notna(), norm.cdf(up.blend_margin / sig_b), np.nan)
        up["pick_p"] = up.blend_p.fillna(up.model_p)
        # lock: games starting today are (re)locked each run until tip; the last pre-tip run wins
        start = pd.to_datetime(up.start, utc=True)
        lock = up[(up.state == "pre") & (pd.to_datetime(up.date).dt.date == today.date()) & (start > pd.Timestamp(now))].copy()
        lock["locked_at"] = now.isoformat()
        keep = ["game_id", "date", "start", "home_id", "away_id", "home_name", "away_name", "neutral", "notes",
                "model_margin", "model_total", "model_p", "home_spread", "total_line", "blend_margin", "blend_p", "locked_at"]
        if len(lock):
            ledger = pd.concat([ledger[~ledger.game_id.isin(lock.game_id)] if len(ledger) else ledger, lock[keep]], ignore_index=True)
    ledger = grade(ledger, games)
    if len(ledger):
        ledger.to_csv(ledger_path, index=False)

    # Monday frozen snapshot
    is_monday = today.weekday() == 0 if weekly is None else weekly
    week_of = (today - timedelta(days=today.weekday())).date().isoformat()
    week_path = os.path.join(OUT, "weeks", f"{week_of}.json")
    if is_monday and len(up) and not os.path.exists(week_path):
        wk = up[pd.to_datetime(up.date) < today + timedelta(days=7)]
        write_json(week_path, dict(week_of=week_of, frozen_at=now.isoformat(), season=season,
                                   games=[game_row(r) for r in wk.itertuples()]))
        print("weekly snapshot frozen:", week_path)
    # full season: simulate every remaining game
    proj = season_sim(sched, games, R, meta, DEFAULTS)
    is_mon = today.weekday() == 0 if weekly is None else weekly
    ranks, prev_ranks = rank_history(season, today, R, is_mon)
    extras = dict(sched=sched, proj=proj, ranks=ranks, prev_ranks=prev_ranks, sigma=(sig_live, n_sig), R_elo=R_elo, homes=homes, absent=absent,
                  torvik=torvik, torvik_note=tv_note)
    net, net_through = net_ranks(season, [dict(id=t, location=r.location, displayName=r.full, shortDisplayName=r["name"], abbreviation=r.abbr) for t, r in teams_df.iterrows()])
    # line capture health: games starting later today should have lines by the afternoon runs
    if len(up) and now.hour >= 20:
        todays = up[(pd.to_datetime(up.date).dt.date == today.date()) & (up.state == "pre")]
        if len(todays) >= 5 and todays.home_spread.notna().sum() == 0:
            WARN.append(f"No betting lines were found for today's {len(todays)} games, so posted picks are model-only until lines return.")
    publish(season, now, R, meta, teams_df, up, ledger, w, sig_b, n_fit, games, net, avail, net_through, extras)


def game_row(r):
    return dict(id=int(r.game_id), date=r.date, start=r.start, h=int(r.home_id), a=int(r.away_id),
                hn=r.home_name, an=r.away_name, n=bool(r.neutral), note=r.notes or "",
                mm=r1(r.model_margin), mt=r1(r.model_total), mp=r1(r.model_p, 3),
                sp=r1(r.home_spread), tl=r1(r.total_line), bm=r1(r.blend_margin), bp=r1(r.blend_p, 3))


def clv(season, ledger):
    """Closing line value: when the model disagreed with the opening line, did the line move toward the model by tip?"""
    path = p_(f"lines_{season}.csv")
    if not os.path.exists(path) or not len(ledger):
        return None
    L = pd.read_csv(path).dropna(subset=["home_spread"]).sort_values("captured_at")
    if L.empty:
        return None
    op = L.groupby("game_id").home_spread.first(); cl = L.groupby("game_id").home_spread.last()
    d = ledger.drop_duplicates("game_id", keep="last").set_index("game_id")[["model_margin"]].join(op.rename("open")).join(cl.rename("close")).dropna()
    d["dis"] = d.model_margin - (-d.open)
    d = d[d.dis.abs() >= 1]
    if d.empty:
        return dict(n=0)
    mv = ((-d.close) - (-d.open)) * np.sign(d.dis)
    moved = mv[mv != 0]
    return dict(n=int(len(d)), moved=int(len(moved)), toward=r1((moved > 0).mean(), 4) if len(moved) else None,
                avg_pts=r1(mv.mean(), 2))


def metrics(df, pcol, mcol):
    d = df.dropna(subset=[pcol, "home_pts"])
    if not len(d):
        return None
    y = (d.home_pts > d.away_pts).astype(float); q = d[pcol].clip(1e-4, 1 - 1e-4)
    act = d.home_pts - d.away_pts
    return dict(n=int(len(d)), logloss=r1(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)), 4),
                brier=r1(np.mean((q - y) ** 2), 4), acc=r1(np.mean((q > .5) == (y == 1)), 4),
                mae=r1(np.mean(np.abs(act - d[mcol])), 2))


def publish(season, now, R, meta, teams_df, up, ledger, w, sig_b, n_fit, games, net=None, avail=None, net_through=None, extras=None):
    extras = extras or {}
    tinfo = teams_df.to_dict(orient="index")
    write_json(os.path.join(OUT, "teams.json"), {str(k): dict(n=v["name"], f=v["full"], ab=v["abbr"], c=v["color"], l=v["logo"]) for k, v in tinfo.items()})
    rr = R.sort_values("em", ascending=False)
    try:
        conf_of = live.conferences()
    except Exception:
        conf_of = {}
    rec = records(games)
    proj = extras.get("proj", {})
    prev = extras.get("prev_ranks", pd.Series(dtype=float))
    last = {}
    for r in games.sort_values("date").itertuples():
        for t, o, me, op, home in ((r.home_id, r.away_id, r.home_pts, r.away_pts, True), (r.away_id, r.home_id, r.away_pts, r.home_pts, False)):
            last[int(t)] = dict(o=int(o), s=f"{int(me)}-{int(op)}", w=bool(me > op), h=("vs" if r.neutral else ("home" if home else "away")), d=str(r.date)[:10])
    tiles = []
    for i, (t, x) in enumerate(rr.iterrows(), start=1):
        t = int(t); rc = rec.get(t, [0, 0, 0, 0]); pj = proj.get(t)
        tiles.append(dict(id=t, rank=i, chg=(int(prev[t] - i) if t in prev.index else None),
                          conf=conf_of.get(t), net=(net or {}).get(t, {}).get("rank"), q1=(net or {}).get(t, {}).get("q1"),
                          em=r1(x.em, 2), o=r1(meta["mu"] + x.o, 1), d=r1(meta["mu"] - x.d, 1), t=r1(meta["tau"] + x.t, 1), g=int(x.games),
                          rec=f"{rc[0]}-{rc[1]}", crec=f"{rc[2]}-{rc[3]}",
                          proj=(f"{pj['w']:.0f}-{pj['l']:.0f}" if pj else None), pcrec=(f"{pj['cw']:.0f}-{pj['cl']:.0f}" if pj else None),
                          last=last.get(t)))
    write_json(os.path.join(OUT, "ratings.json"), dict(updated=now.isoformat(), season=season, mu=r1(meta["mu"], 2), tau=r1(meta["tau"], 2),
               home=r1(meta["h"] * 2 * meta["tau"] / 100, 2), net_through=net_through,
               sigma=r1(extras.get("sigma", (DEFAULTS["sigma0"], 0))[0], 2), sigma_games=extras.get("sigma", (0, 0))[1], teams=tiles))
    sched = extras.get("sched")
    if sched is not None and len(sched):
        sc = sched.copy()
        res = games.drop_duplicates("game_id").set_index("game_id")
        sc["hp"] = sc.game_id.map(res.home_pts); sc["ap"] = sc.game_id.map(res.away_pts)
        ok = sc.home_id.isin(R.index) & sc.away_id.isin(R.index)
        m, tot, ph, poss = predict(R, meta, sc.home_id.values, sc.away_id.values, sc.neutral.astype(bool).values, DEFAULTS)
        sc["base_margin"] = m; sc["poss_hat"] = poss
        if "R_elo" in extras:
            m, _, sg = stage2(sc, extras["R_elo"], extras["homes"], extras["absent"], extras.get("torvik"))
            ph = norm.cdf(m / (sg * SIGMA_SCALE * np.sqrt(poss / 68.0)))
        sc["pm"], sc["pp"] = np.where(ok, m, np.nan), np.where(ok, ph, np.nan)
        sc["lk"] = False
        if len(ledger):
            L = ledger.drop_duplicates("game_id", keep="last").set_index("game_id")
            lp = L.blend_p.fillna(L.model_p); lm = L.blend_margin.fillna(L.model_margin)
            has = sc.game_id.isin(L.index)
            sc.loc[has, "pp"] = sc.loc[has, "game_id"].map(lp).values
            sc.loc[has, "pm"] = sc.loc[has, "game_id"].map(lm).values
            sc.loc[has, "lk"] = True
        write_json(os.path.join(OUT, "schedule.json"), dict(updated=now.isoformat(), season=season,
                   cols=["id", "date", "start", "h", "a", "n", "conf", "note", "hp", "ap", "pm", "pp", "locked"],
                   games=[[int(r.game_id), r.date, r.start, int(r.home_id), int(r.away_id), bool(r.neutral), bool(r.conf_game),
                           (r.notes if isinstance(r.notes, str) else ""), r1(r.hp, 0), r1(r.ap, 0), r1(r.pm), r1(r.pp, 3), bool(r.lk)]
                          for r in sc.itertuples()]))
    if len(up):
        write_json(os.path.join(OUT, "upcoming.json"), dict(updated=now.isoformat(), season=season, blend_weight=r1(w, 3), blend_sigma=r1(sig_b, 2), blend_fit_games=n_fit, out={str(k): v for k, v in (avail or {}).items()},
                   games=[game_row(r) for r in up.sort_values("start").itertuples()]))
    # ledger + accuracy
    L = ledger.copy() if len(ledger) else pd.DataFrame(columns=["game_id"])
    if len(L):
        L["mkt_margin"] = -L.home_spread
        L["mkt_p"] = norm.cdf(L.mkt_margin / sig_b)
        L["official_p"] = L.blend_p.fillna(L.model_p); L["official_m"] = L.blend_margin.fillna(L.model_margin)
        L["week_of"] = (pd.to_datetime(L.date) - pd.to_timedelta(pd.to_datetime(L.date).dt.weekday, unit="D")).dt.date.astype(str)
        both = L.dropna(subset=["home_spread"])
        acc = dict(updated=now.isoformat(), season=season,
                   overall=dict(official=metrics(L, "official_p", "official_m"), model=metrics(L, "model_p", "model_margin"),
                                model_on_lined=metrics(both, "model_p", "model_margin"), market=metrics(both, "mkt_p", "mkt_margin"),
                                blend=metrics(both, "blend_p", "blend_margin")),
                   weeks=[dict(week_of=k, official=metrics(v, "official_p", "official_m"), model=metrics(v, "model_p", "model_margin"))
                          for k, v in L.groupby("week_of")])
        acc["clv"] = clv(season, ledger)
        write_json(os.path.join(OUT, "accuracy.json"), acc)
        cols = ["game_id", "date", "home_id", "away_id", "home_name", "away_name", "neutral", "model_margin", "model_p",
                "home_spread", "blend_p", "official_p", "home_pts", "away_pts"]
        write_json(os.path.join(OUT, "ledger.json"), dict(updated=now.isoformat(), season=season,
                   games=[[int(r.game_id), r.date, int(r.home_id), int(r.away_id), r.home_name, r.away_name, bool(r.neutral),
                           r1(r.model_margin), r1(r.model_p, 3), r1(r.home_spread), r1(r.blend_p, 3), r1(r.official_p, 3),
                           r1(r.home_pts, 0), r1(r.away_pts, 0)] for r in L[cols].sort_values("date", ascending=False).itertuples()]))
    # bracket
    try:
        bracket(season, now, R, meta, teams_df, games, net or {}, extras.get("proj"))
    except Exception as e:
        print("bracket failed:", e)
        WARN.append(f"The bracket could not be rebuilt in the last update ({type(e).__name__}).")
    weeks = sorted(f[:-5] for f in os.listdir(os.path.join(OUT, "weeks"))) if os.path.isdir(os.path.join(OUT, "weeks")) else []
    write_json(os.path.join(OUT, "meta.json"), dict(updated=now.isoformat(), season=season, games_ingested=int(len(games)), weeks=weeks, warnings=WARN,
               params={k: v for k, v in DEFAULTS.items()}, sigma_live=r1(extras.get("sigma", (0, 0))[0], 2), sigma_games=extras.get("sigma", (0, 0))[1], stage2=STAGE2, torvik_note=extras.get("torvik_note"), backtest=json.load(open(os.path.join(ROOT, "model", "backtest_summary.json")))))


def bracket(season, now, R, meta, teams_df, games, net, proj=None):
    real = None
    sel = CFG.get("selection_sunday", {}).get(str(season))
    if sel and pd.Timestamp(now.date()) > pd.Timestamp(sel):
        d0 = pd.Timestamp(sel)
        real = live.tournament_field([(d0 + timedelta(days=k)).strftime("%Y%m%d") for k in range(2, 6)])
        if len(real) < 64:
            real = None
    odds = []
    if real is not None:
        field, kind = real, "official"
    else:
        conf = {t: c for t, c in live.conferences().items() if t in R.index}
        net_rank = {t: v["rank"] for t, v in net.items()} if net else None
        order_key = (lambda t: net_rank.get(t, 999)) if net_rank else (lambda t: -R.at[t, "em"])
        cpct = ({t: v["cw"] / max(v["cw"] + v["cl"], 1e-9) for t, v in proj.items() if v["cw"] + v["cl"] > 0} if proj else conf_records(games))
        auto_p = conf_tourney_odds(R, meta, DEFAULTS, conf, cpct)
        make_p, _ = field_odds(R, auto_p, conf, order_key)
        best = {}
        for t, c in conf.items():
            if c not in best or auto_p.get(t, 0) > auto_p.get(best[c], 0):
                best[c] = t
        field, kind = project_field(R, conf, net_rank, autos=list(best.values())), "projected"
        odds = [dict(id=int(t), conf=conf[t], auto=r1(auto_p.get(t, 0), 4), make=r1(make_p.get(t, 0), 4),
                     net=net_rank.get(t) if net_rank else None)
                for t in sorted(make_p, key=lambda t: -make_p[t]) if make_p[t] >= 0.005]
    pairs = tuple(tuple(x) for x in CFG.get("final_four_pairs", {}).get(str(season), [["East", "West"], ["South", "Midwest"]]))
    probs, picks = simulate(field, R, meta, DEFAULTS, n_sims=CFG["bracket"]["n_sims"], team_sd=CFG["bracket"]["team_sd"], f4_pairs=pairs)
    write_json(os.path.join(OUT, "bracket.json"), dict(updated=now.isoformat(), season=season, kind=kind, final_four_pairs=pairs,
               seeding="NET" if (net and kind == "projected") else ("official" if kind == "official" else "model rating"),
               field_odds=odds,
               teams=[dict(id=int(r.team_id), seed=int(r.seed), region=r.region, **({"bid": r.bid} if "bid" in probs else {}),
                           **{c: r1(getattr(r, c), 4) for c in ["R64", "R32", "S16", "E8", "F4", "Final", "Champ"]}) for r in probs.itertuples()],
               picks=[dict(round=r["round"], region=r["region"], a=int(r["a"]), b=int(r["b"]), w=int(r["winner"]), p=r1(r["p"], 3)) for r in picks.to_dict("records")]))
    print(f"bracket: {kind} field simulated ({'NET' if net else 'rating'} order)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily"])
    ap.add_argument("--asof"); ap.add_argument("--season"); ap.add_argument("--weekly", action="store_true")
    a = ap.parse_args()
    daily(a.asof, a.season, True if a.weekly else None)
