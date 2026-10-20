"""NCAA tournament projection and Monte Carlo simulation.

Before Selection Sunday: a projected field (auto bids = most likely conference champion,
at-larges by NET rank when available, else model rating), seeded on an S-curve. This is a model
projection, not a forecast of committee decisions. After Selection Sunday: the real field from ESPN.
"""
import numpy as np, pandas as pd
from scipy.stats import norm

ROUND_NAMES = ["R64", "R32", "S16", "E8", "F4", "Final", "Champ"]
SEED_ORDER = [1, 16, 8, 9, 5, 12, 4, 13, 6, 11, 3, 14, 7, 10, 2, 15]


def win_prob_matrix(R, meta, ids, p):
    em = np.array([R.at[t, "em"] if t in R.index else -15.0 for t in ids])
    tt = np.array([R.at[t, "t"] if t in R.index else 0.0 for t in ids])
    poss = meta["tau"] + tt[:, None] + tt[None, :]
    margin = poss / 100 * (em[:, None] - em[None, :]) + meta.get("hn", 0.0) * 0  # neutral, no designated home
    sig = p["sigma0"] * np.sqrt(poss / 68.0)
    return norm.cdf(margin / sig), em, poss, sig


def project_field(R, conf_of, net_rank=None, n_teams=68):
    """conf_of: {team_id: conference_name}. Returns DataFrame team_id, seed, region, bid."""
    r = R[R.index.isin(conf_of.keys())].copy()
    r["conf"] = r.index.map(conf_of)
    order_key = (lambda t: net_rank.get(t, 999)) if net_rank else (lambda t: -r.at[t, "em"])
    autos = r.sort_values("em", ascending=False).groupby("conf").head(1).index.tolist()
    rest = sorted([t for t in r.index if t not in autos], key=order_key)
    at_large = rest[: n_teams - len(autos)]
    field = sorted(autos + at_large, key=order_key)
    # First Four: last 4 at-larges and 4 lowest-ranked auto bids share seed lines
    seeds, line = {}, []
    al_set = set(at_large)
    last4 = [t for t in field if t in al_set][-4:]
    low_auto = [t for t in field if t not in al_set][-4:]
    main = [t for t in field if t not in last4 and t not in low_auto]
    # 60 teams on S-curve occupy seeds 1-15 plus partial; place First Four pairs into lines
    ranked = main[:]
    slots = []
    for s in range(1, 17):
        for k in range(4):
            slots.append(s)
    out = []
    ff_auto_pairs = [low_auto[0:2], low_auto[2:4]]  # two 16-seed play-ins
    ff_al_pairs = [last4[0:2], last4[2:4]]          # two at-large play-ins (seed 11 by convention)
    # assign straight S-curve for main teams, skipping two 11 slots and two 16 slots for play-in winners
    reserved = {11: 2, 16: 2}
    regions = ["East", "West", "South", "Midwest"]
    it = iter(ranked)
    pos = 0
    for s in range(1, 17):
        regs = regions if s % 2 == 1 else regions[::-1]
        for reg_i, reg in enumerate(regs):
            if reserved.get(s, 0) > 0 and reg_i >= 4 - reserved[s]:
                pair = (ff_al_pairs if s == 11 else ff_auto_pairs).pop()
                for t in pair:
                    out.append(dict(team_id=t, seed=s, region=reg, first_four=True))
                continue
            t = next(it, None)
            if t is None:
                continue
            out.append(dict(team_id=t, seed=s, region=reg, first_four=False))
    f = pd.DataFrame(out)
    f["bid"] = np.where(f.team_id.isin(autos), "auto", "at-large")
    return f


def simulate(field, R, meta, p, n_sims=20000, team_sd=2.0, f4_pairs=(("East", "West"), ("South", "Midwest")), seed=7):
    """field: DataFrame team_id, seed, region (two rows share a slot when a First Four game exists).
    Returns per-team round probabilities and the model's most likely bracket."""
    rng = np.random.default_rng(seed)
    ids = field.team_id.tolist()
    ix = {t: i for i, t in enumerate(ids)}
    P0, em, poss, sig = win_prob_matrix(R, meta, ids, p)
    n = len(ids)
    adv = np.zeros((n, 7))
    regions = [r for pair in f4_pairs for r in pair]
    # slot list per region in bracket order; a slot is a list of 1 or 2 team indices
    reg_slots = {}
    for reg in regions:
        fr = field[field.region == reg]
        reg_slots[reg] = [[ix[t] for t in fr[fr.seed == s].team_id] for s in SEED_ORDER]
    S = n_sims
    emS = em[None, :] + rng.normal(0, team_sd, (S, n))
    rows = np.arange(S)

    def game(a, b):  # a, b: int arrays (S,)
        m = poss[a, b] / 100 * (emS[rows, a] - emS[rows, b])
        win = rng.random(S) < norm.cdf(m / sig[a, b])
        return np.where(win, a, b)

    def credit(w, r):
        np.add.at(adv[:, r], w, 1)

    champs = {}
    for reg in regions:
        alive = []
        for slot in reg_slots[reg]:
            if len(slot) == 2:
                alive.append(game(np.full(S, slot[0]), np.full(S, slot[1])))
            elif len(slot) == 1:
                alive.append(np.full(S, slot[0]))
        for w in alive:
            credit(w, 0)
        rnd = 1
        while len(alive) > 1:
            alive = [game(alive[i], alive[i + 1]) for i in range(0, len(alive), 2)]
            for w in alive:
                credit(w, rnd)
            rnd += 1
        champs[reg] = alive[0]
    finals = [game(champs[a], champs[b]) for a, b in f4_pairs]
    for w in finals:
        credit(w, 5)
    credit(game(finals[0], finals[1]), 6)
    probs = pd.DataFrame(adv / n_sims, columns=ROUND_NAMES)
    probs.insert(0, "team_id", ids)
    probs = probs.merge(field, on="team_id")
    # most likely bracket: pick the favorite game by game using point (non-shocked) probabilities
    picks = []
    champs = {}
    for reg in regions:
        alive = []
        for slot in reg_slots[reg]:
            if len(slot) == 2:
                a, b = slot; w = a if P0[a, b] >= 0.5 else b
                picks.append(dict(round="First Four", region=reg, a=ids[a], b=ids[b], winner=ids[w], p=float(max(P0[a, b], P0[b, a]))))
                alive.append(w)
            elif slot:
                alive.append(slot[0])
        rnd = 0
        while len(alive) > 1:
            nxt = []
            for i in range(0, len(alive), 2):
                a, b = alive[i], alive[i + 1]; w = a if P0[a, b] >= 0.5 else b
                picks.append(dict(round=ROUND_NAMES[rnd], region=reg, a=ids[a], b=ids[b], winner=ids[w], p=float(max(P0[a, b], P0[b, a]))))
                nxt.append(w)
            alive = nxt; rnd += 1
        champs[reg] = alive[0]
    fin = []
    for x, y in f4_pairs:
        a, b = champs[x], champs[y]; w = a if P0[a, b] >= 0.5 else b
        picks.append(dict(round="F4", region=f"{x}/{y}", a=ids[a], b=ids[b], winner=ids[w], p=float(max(P0[a, b], P0[b, a]))))
        fin.append(w)
    a, b = fin; w = a if P0[a, b] >= 0.5 else b
    picks.append(dict(round="Final", region="", a=ids[a], b=ids[b], winner=ids[w], p=float(max(P0[a, b], P0[b, a]))))
    return probs.sort_values("Champ", ascending=False), pd.DataFrame(picks)
