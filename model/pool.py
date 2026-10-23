"""Pool strategy: pick the bracket most likely to WIN a pool, not just the most likely bracket.

Simulate the tournament many times with the model, simulate opponents' brackets from public pick rates,
and score candidate brackets with standard ESPN scoring (10, 20, 40, 80, 160, 320).
"""
import numpy as np, pandas as pd
from scipy.stats import norm
from model.bracket import win_prob_matrix, SEED_ORDER

PTS = np.array([10, 20, 40, 80, 160, 320], dtype=float)  # points for picking a winner in rounds 1..6


def _structure(field, f4_pairs):
    ids = field.team_id.tolist()
    ix = {t: i for i, t in enumerate(ids)}
    regions = [r for pair in f4_pairs for r in pair]
    slots = {reg: [[ix[t] for t in field[(field.region == reg) & (field.seed == s)].team_id] for s in SEED_ORDER] for reg in regions}
    return ids, ix, regions, slots


def play(slots, regions, f4_pairs, decide):
    """Run one bracket. decide(a, b, level) -> winner index (vectorized arrays allowed).
    Returns list of (level, winner) where level 1..6 = winner of round 1..6 game."""
    out = []
    champs = {}
    for reg in regions:
        alive = []
        for slot in slots[reg]:
            alive.append(decide(slot[0], slot[1], 0) if len(slot) == 2 else slot[0])
        lvl = 1
        while len(alive) > 1:
            nxt = []
            for i in range(0, len(alive), 2):
                w = decide(alive[i], alive[i + 1], lvl); nxt.append(w); out.append((lvl, w))
            alive = nxt; lvl += 1
        champs[reg] = alive[0]
    fin = []
    for a, b in f4_pairs:
        w = decide(champs[a], champs[b], 5); fin.append(w); out.append((5, w))
    w = decide(fin[0], fin[1], 6); out.append((6, w))
    return out


def reach_matrix(results, n_teams, n):
    """results: list of (level, winner_array[n]) -> bool array n x teams x 6 (won a game at level k)."""
    R = np.zeros((n, n_teams, 6), dtype=bool)
    rows = np.arange(n)
    for lvl, w in results:
        if lvl >= 1:
            R[rows, w, lvl - 1] = True
    return R


def strategy(field, Rt, meta, p, f4_pairs, public=None, n_sims=3000, n_opp=4000, pool_sizes=(10, 25, 100, 500),
             gamma=1.5, team_sd=1.0, seed=17):
    ids, ix, regions, slots = _structure(field, f4_pairs)
    T = len(ids)
    P, em, poss, sig = win_prob_matrix(Rt, meta, ids, p)
    rng = np.random.default_rng(seed)
    # 1) tournament outcomes from the model (team strength shock shared within each simulation)
    emS = em[None, :] + rng.normal(0, team_sd, (n_sims, T))
    rows = np.arange(n_sims)

    def model_decide(a, b, lvl):
        a = np.full(n_sims, a) if np.isscalar(a) else a; b = np.full(n_sims, b) if np.isscalar(b) else b
        m = poss[a, b] / 100 * (emS[rows, a] - emS[rows, b])
        return np.where(rng.random(n_sims) < norm.cdf(m / sig[a, b]), a, b)
    actual = reach_matrix(play(slots, regions, f4_pairs, model_decide), T, n_sims)
    model_reach = actual.mean(0)  # T x 6
    # 2) public pick rates: real numbers if supplied, else a chalkier proxy of the model's own odds
    if public:
        pub = np.array([[public.get(str(t), [0] * 6)[k] for k in range(6)] for t in ids], dtype=float)
        source = "public"
    else:
        pub = np.zeros_like(model_reach)
        for k in range(6):
            x = model_reach[:, k] ** gamma
            pub[:, k] = x / max(x.sum(), 1e-12) * (32 / 2 ** k)
        source = "proxy"
    pub = np.clip(pub, 1e-6, None)
    # 3) opponent brackets sampled game by game from public rates
    orow = np.arange(n_opp)

    def pub_decide(a, b, lvl):
        a = np.full(n_opp, a) if np.isscalar(a) else a; b = np.full(n_opp, b) if np.isscalar(b) else b
        k = max(lvl, 1) - 1 if lvl > 0 else 0
        pa = pub[a, k]; pb = pub[b, k]
        return np.where(rng.random(n_opp) < pa / (pa + pb), a, b)
    opp = reach_matrix(play(slots, regions, f4_pairs, pub_decide), T, n_opp)
    W = (actual.reshape(n_sims, -1) * np.tile(PTS, T)[None, :]).astype(np.float32)  # n_sims x (T*6), weighted
    opp_scores = opp.reshape(n_opp, -1).astype(np.float32) @ W.T  # n_opp x n_sims

    # 4) candidate brackets: model favorites, with each plausible champion forced through
    def favorites_with(champ=None):
        def d(a, b, lvl):
            if champ is not None and (a == champ or b == champ):
                return champ
            return a if P[a, b] >= 0.5 else b
        res = play(slots, regions, f4_pairs, d)
        return reach_matrix([(l, np.array([w])) for l, w in res], T, 1)[0]
    order = np.argsort(-model_reach[:, 5])
    cands = {"model favorites": favorites_with(None)}
    for c in order[:10]:
        cands[f"champ:{ids[c]}"] = favorites_with(c)
    results = {}
    for name, br in cands.items():
        my = actual.reshape(n_sims, -1).astype(np.float32) @ (br.reshape(-1) * np.tile(PTS, T)).astype(np.float32)
        winp = {}
        for K in pool_sizes:
            # sample K-1 opponents per simulation; win = beat them all (ties split)
            idx = rng.integers(0, n_opp, (n_sims, K - 1))
            best = opp_scores[idx, rows[:, None]].max(1)
            winp[K] = float(np.mean((my > best) + 0.5 * (my == best)))
        results[name] = dict(bracket=br, win=winp, exp=float(my.mean()))
    out = dict(source=source, pool_sizes=list(pool_sizes), model_reach={str(ids[i]): model_reach[i].round(4).tolist() for i in range(T)},
               public_reach={str(ids[i]): pub[i].round(4).tolist() for i in range(T)}, best={}, chalk={})
    chalk = results["model favorites"]
    for K in pool_sizes:
        name = max(results, key=lambda n: results[n]["win"][K])
        r = results[name]; br = r["bracket"]
        levels = {str(ids[t]): int(br[t].sum()) for t in range(T) if br[t].any()}
        champ = ids[int(np.argmax(br[:, 5]))]
        out["best"][str(K)] = dict(champion=int(champ), levels=levels, win=round(r["win"][K], 4), expected=round(r["exp"], 1),
                                   final_four=[int(ids[t]) for t in np.where(br[:, 3])[0]])
        out["chalk"][str(K)] = dict(win=round(chalk["win"][K], 4), expected=round(chalk["exp"], 1))
    out["champion_value"] = [dict(id=int(ids[t]), model=round(float(model_reach[t, 5]), 4), public=round(float(pub[t, 5]), 4))
                             for t in order[:16]]
    return out
