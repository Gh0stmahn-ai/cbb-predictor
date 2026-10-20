"""Production pipeline. Runs on GitHub Actions.

  python -m pipeline.run daily            # every day: ingest finals, capture lines, refit, lock, grade, publish
  python -m pipeline.run daily --asof 2026-02-16 --season 2026   # dry run on a past date

Monday runs also freeze a weekly snapshot (never edited afterwards).
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd
from scipy.stats import norm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from model.ratings import fit, predict, DEFAULTS, make_prior
from model.prior import roster_features, build_prior
from model.bracket import project_field, simulate
from model import live

STATE = os.path.join(ROOT, "state")
OUT = os.path.join(ROOT, "docs", "data")
CFG = json.load(open(os.path.join(ROOT, "config.json")))


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
    new = []
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
            new.append(dict(game_id=x["game_id"], season=season, date=pd.Timestamp(x["date"]), home_id=x["home_id"],
                            away_id=x["away_id"], home_name=x["home_name"], away_name=x["away_name"],
                            home_pts=x["home_pts"], away_pts=x["away_pts"], poss=bx["poss"], neutral=x["neutral"],
                            conf_game=x["conf_game"], notes=x["notes"], season_type=x["season_type"]))
            have.add(x["game_id"])
        day += timedelta(days=1)
    if new:
        g = pd.concat([g, pd.DataFrame(new)], ignore_index=True)
        g.to_csv(p_(f"games_{season}.csv"), index=False)
    print(f"ingest: {len(new)} new games, {len(g)} total")
    g["date"] = pd.to_datetime(g["date"])
    g["neutral"] = g.neutral.astype(bool)
    return g


def capture_lines(season, now, games_ahead):
    path = p_(f"lines_{season}.csv")
    old = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    rows = []
    for x in games_ahead:
        o = x.get("odds") or {}
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
    lines = capture_lines(season, now, ahead)
    up = pd.DataFrame(ahead)
    ledger_path = p_(f"ledger_{season}.csv")
    ledger = pd.read_csv(ledger_path) if os.path.exists(ledger_path) else pd.DataFrame()
    ledger = grade(ledger, games)
    w, sig_b, n_fit = blend_weight(ledger)
    if len(up):
        m, tot, ph, poss = predict(R, meta, up.home_id.values, up.away_id.values, up.neutral.values, DEFAULTS)
        up["model_margin"], up["model_total"], up["model_p"] = m, tot, ph
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
    publish(season, now, R, meta, teams_df, up, ledger, w, sig_b, n_fit, games)


def game_row(r):
    return dict(id=int(r.game_id), date=r.date, start=r.start, h=int(r.home_id), a=int(r.away_id),
                hn=r.home_name, an=r.away_name, n=bool(r.neutral), note=r.notes or "",
                mm=r1(r.model_margin), mt=r1(r.model_total), mp=r1(r.model_p, 3),
                sp=r1(r.home_spread), tl=r1(r.total_line), bm=r1(r.blend_margin), bp=r1(r.blend_p, 3))


def metrics(df, pcol, mcol):
    d = df.dropna(subset=[pcol, "home_pts"])
    if not len(d):
        return None
    y = (d.home_pts > d.away_pts).astype(float); q = d[pcol].clip(1e-4, 1 - 1e-4)
    act = d.home_pts - d.away_pts
    return dict(n=int(len(d)), logloss=r1(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)), 4),
                brier=r1(np.mean((q - y) ** 2), 4), acc=r1(np.mean((q > .5) == (y == 1)), 4),
                mae=r1(np.mean(np.abs(act - d[mcol])), 2))


def publish(season, now, R, meta, teams_df, up, ledger, w, sig_b, n_fit, games):
    tinfo = teams_df.to_dict(orient="index")
    write_json(os.path.join(OUT, "teams.json"), {str(k): dict(n=v["name"], f=v["full"], ab=v["abbr"], c=v["color"], l=v["logo"]) for k, v in tinfo.items()})
    rr = R.sort_values("em", ascending=False)
    write_json(os.path.join(OUT, "ratings.json"), dict(updated=now.isoformat(), season=season, mu=r1(meta["mu"], 2), tau=r1(meta["tau"], 2), home=r1(meta["h"] * 2 * meta["tau"] / 100, 2),
               teams=[dict(id=int(t), em=r1(x.em, 2), o=r1(meta["mu"] + x.o, 1), d=r1(meta["mu"] - x.d, 1), t=r1(meta["tau"] + x.t, 1), g=int(x.games)) for t, x in rr.iterrows()]))
    if len(up):
        write_json(os.path.join(OUT, "upcoming.json"), dict(updated=now.isoformat(), season=season, blend_weight=r1(w, 3), blend_sigma=r1(sig_b, 2), blend_fit_games=n_fit,
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
        write_json(os.path.join(OUT, "accuracy.json"), acc)
        cols = ["game_id", "date", "home_id", "away_id", "home_name", "away_name", "neutral", "model_margin", "model_p",
                "home_spread", "blend_p", "official_p", "home_pts", "away_pts"]
        write_json(os.path.join(OUT, "ledger.json"), dict(updated=now.isoformat(), season=season,
                   games=[[int(r.game_id), r.date, int(r.home_id), int(r.away_id), r.home_name, r.away_name, bool(r.neutral),
                           r1(r.model_margin), r1(r.model_p, 3), r1(r.home_spread), r1(r.blend_p, 3), r1(r.official_p, 3),
                           r1(r.home_pts, 0), r1(r.away_pts, 0)] for r in L[cols].sort_values("date", ascending=False).itertuples()]))
    # bracket
    try:
        bracket(season, now, R, meta, teams_df)
    except Exception as e:
        print("bracket failed:", e)
    weeks = sorted(f[:-5] for f in os.listdir(os.path.join(OUT, "weeks"))) if os.path.isdir(os.path.join(OUT, "weeks")) else []
    write_json(os.path.join(OUT, "meta.json"), dict(updated=now.isoformat(), season=season, games_ingested=int(len(games)), weeks=weeks,
               params={k: v for k, v in DEFAULTS.items()}, backtest=json.load(open(os.path.join(ROOT, "model", "backtest_summary.json")))))


def bracket(season, now, R, meta, teams_df):
    real = None
    sel = CFG.get("selection_sunday", {}).get(str(season))
    if sel and pd.Timestamp(now.date()) > pd.Timestamp(sel):
        d0 = pd.Timestamp(sel)
        real = live.tournament_field([(d0 + timedelta(days=k)).strftime("%Y%m%d") for k in range(2, 6)])
        if len(real) < 64:
            real = None
    if real is not None:
        field, kind = real, "official"
    else:
        conf = live.conferences()
        field, kind = project_field(R, conf), "projected"
    pairs = tuple(tuple(x) for x in CFG.get("final_four_pairs", {}).get(str(season), [["East", "West"], ["South", "Midwest"]]))
    probs, picks = simulate(field, R, meta, DEFAULTS, n_sims=CFG["bracket"]["n_sims"], team_sd=CFG["bracket"]["team_sd"], f4_pairs=pairs)
    write_json(os.path.join(OUT, "bracket.json"), dict(updated=now.isoformat(), season=season, kind=kind, final_four_pairs=pairs,
               teams=[dict(id=int(r.team_id), seed=int(r.seed), region=r.region, **({"bid": r.bid} if "bid" in probs else {}),
                           **{c: r1(getattr(r, c), 4) for c in ["R64", "R32", "S16", "E8", "F4", "Final", "Champ"]}) for r in probs.itertuples()],
               picks=[dict(round=r["round"], region=r["region"], a=int(r["a"]), b=int(r["b"]), w=int(r["winner"]), p=r1(r["p"], 3)) for r in picks.to_dict("records")]))
    print(f"bracket: {kind} field simulated")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily"])
    ap.add_argument("--asof"); ap.add_argument("--season"); ap.add_argument("--weekly", action="store_true")
    a = ap.parse_args()
    daily(a.asof, a.season, True if a.weekly else None)
