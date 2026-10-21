"""Fetch external data with the repo's secret API keys (run on GitHub Actions only).

Saves compact copies under state/ext/ plus a probe report, so the backtest can run anywhere
without ever handling the keys. Secrets used: CBBD_API_KEY, CBD_USER, CBD_PW, CBBDATA_API_KEY.
"""
import io, json, os, sys, time, traceback, urllib.parse, urllib.request
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "state", "ext")
os.makedirs(OUT, exist_ok=True)
PROBE = {"cbbd": {}, "cbbdata": {}}
SEASONS = list(range(int(os.environ.get("EXT_FIRST", 2014)), int(os.environ.get("EXT_LAST", 2026)) + 1))


def http(url, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "cbb-predictor", **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------- CollegeBasketballData.com: betting lines ----------------
def cbbd():
    key = os.environ.get("CBBD_API_KEY", "").strip()
    if not key:
        PROBE["cbbd"]["error"] = "CBBD_API_KEY not set"; return
    H = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    base = "https://api.collegebasketballdata.com/lines"
    rows, calls = [], 0
    for s in SEASONS:
        # season runs roughly Nov 1 (year s-1) to Apr 15 (year s); split into windows to stay under the per-call cap
        bounds = pd.date_range(f"{s-1}-10-25", f"{s}-04-20", periods=6)
        for a, b in zip(bounds[:-1], bounds[1:]):
            q = {"season": s, "startDateRange": a.strftime("%Y-%m-%d"), "endDateRange": b.strftime("%Y-%m-%d")}
            try:
                js = json.loads(http(base + "?" + urllib.parse.urlencode(q), H)); calls += 1
            except Exception as e:
                PROBE["cbbd"].setdefault("errors", []).append(f"{s} {a.date()}: {type(e).__name__} {e}")
                continue
            if calls == 1:
                PROBE["cbbd"]["sample"] = js[:2]
            for g in js:
                for ln in g.get("lines") or [{}]:
                    rows.append(dict(season=s, date=str(g.get("startDate") or g.get("start_date") or "")[:19],
                                     home=g.get("homeTeam"), away=g.get("awayTeam"),
                                     home_score=g.get("homeScore"), away_score=g.get("awayScore"),
                                     provider=ln.get("provider"), spread=ln.get("spread"), spread_open=ln.get("spreadOpen"),
                                     total=ln.get("overUnder"), total_open=ln.get("overUnderOpen"),
                                     home_ml=ln.get("homeMoneyline"), away_ml=ln.get("awayMoneyline")))
            time.sleep(0.5)
    PROBE["cbbd"]["calls"] = calls
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(os.path.join(OUT, "lines_cbbd.csv.gz"), index=False, compression="gzip")
        PROBE["cbbd"]["rows"] = len(df)
        PROBE["cbbd"]["with_spread"] = int(df.spread.notna().sum())
        PROBE["cbbd"]["providers"] = df.provider.value_counts().head(10).to_dict()


# ---------------- cbbdata: Torvik day-by-day ratings ----------------
def cbbdata():
    key = os.environ.get("CBBDATA_API_KEY", "").strip()
    user, pw = os.environ.get("CBD_USER", "").strip(), os.environ.get("CBD_PW", "").strip()
    if user and pw:
        try:
            js = json.loads(http("https://www.cbbdata.com/api/auth/login",
                                 {"Content-Type": "application/json"}, json.dumps({"username": user, "password": pw}).encode()))
            fresh = js[0] if isinstance(js, list) else (list(js.values())[0] if isinstance(js, dict) else js)
            if fresh:
                key = str(fresh); PROBE["cbbdata"]["login"] = "ok"
        except Exception as e:
            PROBE["cbbdata"]["login"] = f"failed: {type(e).__name__}"
    if not key:
        PROBE["cbbdata"]["error"] = "no key"; return
    frames = []
    for s in SEASONS:
        url = "https://www.cbbdata.com/api/torvik/ratings/archive?" + urllib.parse.urlencode({"year": s, "key": key})
        try:
            raw = http(url, timeout=300)
            df = pd.read_parquet(io.BytesIO(raw))
        except Exception as e:
            PROBE["cbbdata"].setdefault("errors", []).append(f"{s}: {type(e).__name__} {str(e)[:120]}")
            continue
        if not frames:
            PROBE["cbbdata"]["columns"] = list(df.columns)
            PROBE["cbbdata"]["sample"] = df.head(2).astype(str).to_dict(orient="records")
        keep = [c for c in df.columns if c in ("team", "conf", "year", "date", "adj_o", "adj_d", "adj_tempo", "barthag", "rank", "adj_t")]
        frames.append(df[keep])
        time.sleep(1)
    if frames:
        out = pd.concat(frames, ignore_index=True)
        for c in out.select_dtypes("float64").columns:
            out[c] = out[c].astype("float32")
        out.to_parquet(os.path.join(OUT, "torvik_archive.parquet"), index=False)
        PROBE["cbbdata"]["rows"] = len(out)


if __name__ == "__main__":
    for name, fn in (("cbbd", cbbd), ("cbbdata", cbbdata)):
        try:
            fn()
        except Exception:
            PROBE[name]["crash"] = traceback.format_exc()[-800:]
    json.dump(PROBE, open(os.path.join(OUT, "probe.json"), "w"), indent=1, default=str)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("sample",)} for k, v in PROBE.items()}, indent=1, default=str)[:3000])
