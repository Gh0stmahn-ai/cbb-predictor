"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const cache = {};
async function load(name) {
  if (cache[name] !== undefined) return cache[name];
  try {
    const r = await fetch(`data/${name}.json?v=${Date.now() >> 16}`);
    cache[name] = r.ok ? await r.json() : null;
  } catch { cache[name] = null; }
  return cache[name];
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const pct = (p, d = 0) => p == null ? "-" : (100 * p).toFixed(d) + "%";
const sgn = x => x == null ? "-" : (x > 0 ? "+" : "") + x.toFixed(1);
const fmtDay = d => new Date(d + "T12:00:00").toLocaleDateString(undefined, {weekday: "long", month: "short", day: "numeric"});
const fmtTime = s => s ? new Date(s).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"}) : "";
let TEAMS = {};
const T = id => TEAMS[id] || {n: String(id), c: "6b7a99"};
function teamCell(id) {
  const t = T(id);
  return `<span class="team">${t.l ? `<img src="${esc(t.l)}" alt="" loading="lazy" width="20" height="20">` : ""}${esc(t.n)}</span>`;
}
function hex(c) { const n = parseInt((c || "6b7a99").slice(0, 6), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
function close(a, b) { const x = hex(a), y = hex(b); return Math.hypot(x[0]-y[0], x[1]-y[1], x[2]-y[2]) < 90; }
function splitBar(aId, hId, pHome) {
  const a = T(aId), h = T(hId);
  let ca = "#" + (a.c || "6b7a99"), ch = "#" + (h.c || "1b2a4a");
  if (close(a.c, h.c)) ca = "#8a94a8";
  const pa = 1 - pHome;
  return `<div class="split" role="img" aria-label="${esc(a.n)} ${pct(pa)}, ${esc(h.n)} ${pct(pHome)}">
    <span class="lab" style="width:${(pa*100).toFixed(1)}%;background:${ca}">${pa >= .14 ? esc(a.ab || a.n) + " " + pct(pa) : ""}</span>
    <span class="lab" style="width:${(pHome*100).toFixed(1)}%;background:${ch};text-align:right">${pHome >= .14 ? esc(h.ab || h.n) + " " + pct(pHome) : ""}</span></div>`;
}
function setNav(r) { document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", a.dataset.r === r)); }

/* ---------------- This week ---------------- */
async function home() {
  const up = await load("upcoming");
  if (!up || !up.games.length) {
    view.innerHTML = `<h1>This week</h1><p class="lede">No games on the schedule for the next seven days. The season opens in early November; predictions appear here as soon as games are on the calendar.</p>${await preseasonTop()}`;
    return;
  }
  const lined = up.games.filter(g => g.sp != null).length;
  view.innerHTML = `<h1>This week</h1>
    <p class="lede">Projected margin, total, and win chance for every Division I game in the next seven days. Where a betting line exists, the posted pick blends the model with the market; otherwise it is the model alone.</p>
    <div class="controls"><input type="search" id="q" placeholder="Find a team" aria-label="Find a team">
    <button class="chip" id="onlyLined" aria-pressed="false">Games with a line (${lined})</button></div>
    <div id="slate"></div>`;
  const draw = () => {
    const q = $("#q").value.trim().toLowerCase(), ol = $("#onlyLined").getAttribute("aria-pressed") === "true";
    const gs = up.games.filter(g => (!q || (g.hn + " " + g.an).toLowerCase().includes(q)) && (!ol || g.sp != null));
    const days = {};
    gs.forEach(g => (days[g.date] ||= []).push(g));
    $("#slate").innerHTML = Object.keys(days).sort().map(d => `<section class="day"><h3>${fmtDay(d)} <span class="muted">(${days[d].length})</span></h3>${days[d].map(gameRow).join("")}</section>`).join("") || `<p class="muted">No games match that search.</p>`;
  };
  $("#q").addEventListener("input", draw);
  $("#onlyLined").addEventListener("click", e => { const b = e.currentTarget; b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true"); draw(); });
  draw();
}
function gameRow(g) {
  const p = g.bp ?? g.mp, m = g.bm ?? g.mm;
  const hs = (g.mt + g.mm) / 2, as = (g.mt - g.mm) / 2;
  const fav = m >= 0 ? T(g.h).n : T(g.a).n;
  const line = g.sp == null ? `<span class="muted">No line yet</span>` : `Line ${esc(T(g.h).ab || T(g.h).n)} ${sgn(g.sp)}`;
  return `<div class="game">
    <div class="match">${teamCell(g.a)}<span class="at">${g.n ? "vs" : "at"}</span>${teamCell(g.h)}</div>
    <div class="nums"><b>${esc(fav)} by ${Math.abs(m).toFixed(1)}</b><br>${as.toFixed(0)} to ${hs.toFixed(0)}, total ${g.mt?.toFixed(1)}</div>
    ${splitBar(g.a, g.h, p)}
    <div class="meta">${fmtTime(g.start)}${g.n ? ", neutral site" : ""}${g.note ? ", " + esc(g.note) : ""}, ${line}${g.bp != null ? `, model alone ${pct(g.mp)} for ${esc(T(g.h).n)}` : ""}</div>
  </div>`;
}
async function preseasonTop() {
  const r = await load("ratings");
  if (!r) return "";
  return `<h2>Preseason top 25</h2><p class="muted">Starting ratings from last season, returning minutes, and incoming transfers. Full list on the Ratings page.</p>${ratingsTable(r.teams.slice(0, 25), r, true)}`;
}

/* ---------------- Results ---------------- */
async function results() {
  const [acc, led, meta] = await Promise.all([load("accuracy"), load("ledger"), load("meta")]);
  const weeks = (meta && meta.weeks) || [];
  if (!acc || !led) {
    view.innerHTML = `<h1>Results</h1><p class="lede">Graded predictions show up here after the first games are played. Every prediction is locked at tip and never edited, so this page is the full record.</p>${weekPicker(weeks)}<div id="wk"></div>`;
    bindWeek(weeks); return;
  }
  const o = acc.overall, of = o.official || {};
  const row = (name, m, note) => m ? `<tr><td class="l">${name}</td><td>${m.n}</td><td>${pct(m.acc, 1)}</td><td>${m.logloss.toFixed(3)}</td><td>${m.brier.toFixed(3)}</td><td>${m.mae.toFixed(2)}</td><td class="l muted">${note}</td></tr>` : "";
  view.innerHTML = `<h1>Results</h1>
    <p class="lede">Every prediction is locked at tip and graded against the final score. Nothing is edited after the fact.</p>
    <div class="scoreboard"><div><b>${of.n ?? 0}</b><span>games graded</span></div><div><b>${pct(of.acc, 1)}</b><span>winners picked</span></div>
      <div><b>${of.logloss?.toFixed(3) ?? "-"}</b><span>log loss (lower is better)</span></div><div><b>${of.mae?.toFixed(1) ?? "-"}</b><span>avg margin miss, points</span></div></div>
    <h2>Model against the market</h2>
    <div class="tablewrap"><table><thead><tr><th class="l">Forecast</th><th>Games</th><th>Winners</th><th>Log loss</th><th>Brier</th><th>Margin miss</th><th class="l"></th></tr></thead><tbody>
      ${row("Posted pick", o.official, "Blend where a line exists, model otherwise")}
      ${row("Model alone", o.model, "All games")}
      ${row("Model alone", o.model_on_lined, "Games with a line")}
      ${row("Betting line", o.market, "Same games, line converted to a probability")}
      ${row("Blend", o.blend, "Same games")}
    </tbody></table></div>
    <h2>By week</h2>
    <div class="tablewrap"><table><thead><tr><th class="l">Week of</th><th>Games</th><th>Winners</th><th>Log loss</th><th>Margin miss</th><th class="l">Winners, model alone</th></tr></thead><tbody>
      ${acc.weeks.slice().reverse().map(w => w.official ? `<tr><td class="l">${w.week_of}</td><td>${w.official.n}</td><td>${pct(w.official.acc, 1)}</td><td>${w.official.logloss.toFixed(3)}</td><td>${w.official.mae.toFixed(2)}</td><td class="l">${pct(w.model?.acc, 1)}</td></tr>` : "").join("")}
    </tbody></table></div>
    <h2>Monday snapshots</h2>
    <p class="muted">Each Monday the full week of predictions is frozen as published. Pick a week to see how that snapshot did.</p>
    ${weekPicker(weeks)}<div id="wk"></div>
    <h2>Every graded game</h2>
    <div class="controls"><input type="search" id="lq" placeholder="Find a team" aria-label="Find a team in results"></div><div id="lg"></div>`;
  bindWeek(weeks);
  const draw = () => {
    const q = $("#lq").value.trim().toLowerCase();
    const rows = led.games.filter(g => g[12] != null && (!q || (g[4] + " " + g[5]).toLowerCase().includes(q))).slice(0, 400);
    $("#lg").innerHTML = `<div class="tablewrap"><table><thead><tr><th class="l">Date</th><th class="l">Game</th><th>Pick</th><th>Final</th><th class="l">Result</th></tr></thead><tbody>${rows.map(g => {
      const ph = g[11], hw = g[12] > g[13], right = (ph >= .5) === hw;
      const pickName = ph >= .5 ? g[4] : g[5];
      return `<tr><td class="l">${g[1]}</td><td class="l">${esc(g[5])} ${g[6] ? "vs" : "at"} ${esc(g[4])}</td><td>${esc(pickName)} ${pct(Math.max(ph, 1 - ph))}</td><td>${g[13]}-${g[12]}</td><td class="l"><span class="pill ${right ? "win" : "loss"}">${right ? "Right" : "Wrong"}</span></td></tr>`;
    }).join("")}</tbody></table></div>`;
  };
  $("#lq").addEventListener("input", draw); draw();
}
function weekPicker(weeks) {
  if (!weeks.length) return `<p class="muted">The first Monday snapshot is saved on opening week.</p>`;
  return `<div class="controls"><select id="wsel" aria-label="Choose a week">${weeks.slice().reverse().map(w => `<option>${w}</option>`).join("")}</select></div>`;
}
function bindWeek(weeks) {
  const s = $("#wsel"); if (!s) return;
  const go = async () => {
    const r = await fetch(`data/weeks/${s.value}.json`); const w = r.ok ? await r.json() : null;
    const led = await load("ledger");
    const res = {}; (led?.games || []).forEach(g => { if (g[12] != null) res[g[0]] = [g[12], g[13]]; });
    if (!w) { $("#wk").innerHTML = `<p class="muted">That snapshot could not be loaded.</p>`; return; }
    let n = 0, right = 0;
    const rows = w.games.map(g => {
      const f = res[g.id]; const p = g.bp ?? g.mp;
      let out = `<span class="muted">Not played yet</span>`;
      if (f) { n++; const ok = (p >= .5) === (f[0] > f[1]); if (ok) right++; out = `${f[1]}-${f[0]} <span class="pill ${ok ? "win" : "loss"}">${ok ? "Right" : "Wrong"}</span>`; }
      return `<tr><td class="l">${g.date}</td><td class="l">${esc(g.an)} ${g.n ? "vs" : "at"} ${esc(g.hn)}</td><td>${esc(p >= .5 ? g.hn : g.an)} ${pct(Math.max(p, 1 - p))}</td><td class="l">${out}</td></tr>`;
    }).join("");
    $("#wk").innerHTML = `<p>Frozen ${new Date(w.frozen_at).toLocaleString()}. ${n ? `${right} of ${n} winners right (${pct(right / n, 1)}).` : ""}</p><div class="tablewrap"><table><thead><tr><th class="l">Date</th><th class="l">Game</th><th>Pick</th><th class="l">Final</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  };
  s.addEventListener("change", go); go();
}

/* ---------------- Ratings ---------------- */
function ratingsTable(list, r, compact) {
  return `<div class="tablewrap"><table class="rt"><thead><tr><th>#</th><th class="l" data-k="name">Team</th><th data-k="em">Net rating</th><th data-k="o">Offense</th><th data-k="d">Defense</th><th data-k="t">Pace</th>${compact ? "" : `<th data-k="g">Games</th>`}</tr></thead><tbody>
    ${list.map((t, i) => `<tr><td>${t.rank ?? i + 1}</td><td class="l">${teamCell(t.id)}</td><td><b>${sgn(t.em)}</b></td><td>${t.o.toFixed(1)}</td><td>${t.d.toFixed(1)}</td><td>${t.t.toFixed(1)}</td>${compact ? "" : `<td>${t.g}</td>`}</tr>`).join("")}</tbody></table></div>`;
}
async function ratings() {
  const r = await load("ratings");
  if (!r) { view.innerHTML = `<h1>Ratings</h1><p class="muted">Ratings will appear after the first pipeline run.</p>`; return; }
  r.teams.forEach((t, i) => t.rank = i + 1);
  view.innerHTML = `<h1>Ratings</h1>
    <p class="lede">Points per 100 possessions against an average Division I team on a neutral floor. Net rating is offense minus defense; a +10 team beats an average team by about 7 points in a 68 possession game. Defense is points allowed, so lower is better.</p>
    <p class="muted">League average ${r.mu} points per 100 possessions, average pace ${r.tau} possessions, home court worth ${r.home} points.</p>
    <div class="controls"><input type="search" id="rq" placeholder="Find a team" aria-label="Find a team in ratings"></div><div id="rtab"></div>`;
  let key = "em", dir = -1;
  const draw = () => {
    const q = $("#rq").value.trim().toLowerCase();
    const list = r.teams.filter(t => !q || T(t.id).n.toLowerCase().includes(q)).sort((a, b) => key === "name" ? T(a.id).n.localeCompare(T(b.id).n) * -dir : (a[key] - b[key]) * dir);
    $("#rtab").innerHTML = ratingsTable(list, r, false);
    document.querySelectorAll(".rt th[data-k]").forEach(th => th.addEventListener("click", () => { const k = th.dataset.k; dir = key === k ? -dir : (k === "d" ? 1 : -1); key = k; draw(); }));
  };
  $("#rq").addEventListener("input", draw); draw();
}

/* ---------------- Bracket ---------------- */
const RNDS = ["R64", "R32", "S16", "E8"];
async function bracket() {
  const b = await load("bracket");
  if (!b) { view.innerHTML = `<h1>Bracket</h1><p class="muted">The bracket appears after the first pipeline run.</p>`; return; }
  const champ = b.teams.slice().sort((x, y) => y.Champ - x.Champ);
  const regions = [...new Set(b.final_four_pairs.flat())];
  const regionHTML = reg => {
    const cols = RNDS.map(rd => {
      const ps = b.picks.filter(p => p.region === reg && p.round === rd);
      return `<div class="rcol"><div class="rhead">${({R64:"First round",R32:"Second round",S16:"Sweet 16",E8:"Elite Eight"})[rd]}</div>${ps.map(p => {
        const tA = b.teams.find(t => t.id === p.a), tB = b.teams.find(t => t.id === p.b);
        const s = (t, id) => `<div class="slot ${p.w === id ? "pick" : ""}"><span>${t ? t.seed : ""} ${esc(T(id).n)}</span>${p.w === id ? `<small>${pct(p.p)}</small>` : ""}</div>`;
        return `<div>${s(tA, p.a)}${s(tB, p.b)}</div>`;
      }).join("")}</div>`;
    }).join("");
    return `<section class="region"><h3>${reg}</h3><div class="rgrid">${cols}</div></section>`;
  };
  const ff = b.picks.filter(p => p.round === "F4" || p.round === "Final" || p.round === "First Four");
  view.innerHTML = `<h1>Bracket</h1>
    ${b.kind === "official" ? `<p class="lede">The official field, simulated 50,000 times with the latest ratings.</p>` :
      `<div class="notice"><b>Projected field.</b> Until Selection Sunday this bracket is built from current ratings: the top rated team in each conference takes the automatic bid and the best remaining teams fill the at-large spots, seeded in order. It shows who the model rates highest, not a guess at the committee's choices. It firms up as the season goes on.</div>`}
    <p class="muted">Highlighted picks are the favorite in each game; the percentage is that team's chance to win that game if the matchup happens. The odds table shows each team's chance to reach every round across all simulations.</p>
    <h2>Final Four and title</h2>
    <div class="tablewrap"><table><tbody>${ff.filter(p => p.round !== "First Four").map(p => `<tr><td class="l">${p.round === "F4" ? "National semifinal" : "Championship"}${p.region ? " (" + esc(p.region) + ")" : ""}</td><td class="l">${teamCell(p.a)} vs ${teamCell(p.b)}</td><td>${esc(T(p.w).n)} ${pct(p.p)}</td></tr>`).join("")}</tbody></table></div>
    ${ff.some(p => p.round === "First Four") ? `<h3>First Four</h3><div class="tablewrap"><table><tbody>${ff.filter(p => p.round === "First Four").map(p => `<tr><td class="l">${esc(p.region)}</td><td class="l">${teamCell(p.a)} vs ${teamCell(p.b)}</td><td>${esc(T(p.w).n)} ${pct(p.p)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <h2>Regions</h2><div class="regions">${regions.map(regionHTML).join("")}</div>
    <h2>Chance to reach each round</h2>
    <div class="tablewrap"><table><thead><tr><th>Seed</th><th class="l">Team</th><th class="l">Region</th><th>Second round</th><th>Sweet 16</th><th>Elite Eight</th><th>Final Four</th><th>Title game</th><th>Champion</th></tr></thead><tbody>
      ${champ.map(t => `<tr><td>${t.seed}</td><td class="l">${teamCell(t.id)}</td><td class="l">${esc(t.region)}</td>${["R32","S16","E8","F4","Final","Champ"].map(c => `<td>${t[c] < .001 ? "<0.1%" : pct(t[c], 1)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>`;
}

/* ---------------- Method ---------------- */
async function method() {
  const meta = await load("meta");
  const bt = meta?.backtest;
  const s = m => m ? `<tr><td>${m.n.toLocaleString()}</td><td>${pct(m.accuracy, 1)}</td><td>${m.logloss.toFixed(3)}</td><td>${m.brier.toFixed(3)}</td><td>${m.margin_mae.toFixed(2)}</td><td>${m.margin_rmse.toFixed(2)}</td><td>${m.total_mae.toFixed(2)}</td></tr>` : "";
  const head = `<thead><tr><th>Games</th><th>Winners</th><th>Log loss</th><th>Brier</th><th>Margin MAE</th><th>Margin RMSE</th><th>Total MAE</th></tr></thead>`;
  view.innerHTML = `<h1>How it works</h1>
    <p class="lede">A tempo-free rating model in the tradition of Dean Oliver and Ken Pomeroy, tuned on past seasons and tested on seasons it never saw.</p>
    <h2>The model</h2>
    <p>Every Division I game is converted to points per 100 possessions. A regularized regression then solves for each team's offense, defense, and pace at once, so a team's numbers already account for who it played and where. Home court is fitted from the data each season, and teams listed as home at neutral sites get their own smaller term.</p>
    <p>Before a season starts, each team begins from last season's rating, adjusted for the share of minutes that return and for transfers coming in from other programs. That starting point carries the weight of about two and a half games and fades as real results arrive.</p>
    <p>A game's projected margin is the gap in net ratings scaled by expected possessions. The margin becomes a win chance through a normal curve whose spread grows with pace, fitted to past results.</p>
    <h2>Betting lines</h2>
    <p>Lines are captured from public sources twice a day before tip. Where one exists, the posted pick blends the model with the line, because the closing line is the strongest single public forecast in the research. The blend starts at 30% model and 70% market and is refitted from this site's own graded games once about 1,500 have a line. The Results page grades the model, the line, and the blend separately.</p>
    <h2>Tested on the past</h2>
    <p>The model was replayed week by week, refitting every Monday with only the games played before it, exactly as it runs live. Settings were chosen using 2014 through 2020 only; 2022 through 2026 is an untouched test.</p>
    ${bt ? `<div class="tablewrap"><table>${head}<tbody>
      <tr><td colspan="7" class="l"><b>Untouched test seasons, 2022 to 2026</b></td></tr>${s(bt.holdout)}
      <tr><td colspan="7" class="l"><b>NCAA tournament games, 2014 to 2026</b></td></tr>${s(bt.ncaa_tournament)}
      <tr><td colspan="7" class="l"><b>All seasons, 2014 to 2026</b></td></tr>${s(bt.all)}
    </tbody></table></div>
    <h3>Calibration</h3><p class="muted">When the model says a team wins 75% of the time, how often does it? Gray bar is the predicted chance, orange mark is what happened.</p>
    <div class="cal">${(bt.calibration || []).map(c => `<span>${pct(c.pred)}</span><div class="track"><i style="width:${c.pred*100}%"></i><em style="left:calc(${c.act*100}% - 1px)"></em></div><span>${pct(c.act)} won</span>`).join("")}</div>` : ""}
    <h2>Limits</h2>
    <p>No model can promise a perfect bracket. Even with exactly correct probabilities, the chance of calling all 63 tournament games is far below one in a million. The goal here is honest, well calibrated odds for every game and every round.</p>
    <p class="muted">The model does not yet see injuries or suspensions, and the projected bracket before Selection Sunday ranks teams by rating, not by the committee's résumé criteria. Data: ESPN public endpoints and the sportsdataverse archive.</p>`;
}

const routes = {"": home, results, ratings, bracket, method};
async function route() {
  const r = location.hash.replace(/^#\/?/, "");
  setNav(r in routes ? r : "");
  TEAMS = (await load("teams")) || {};
  const meta = await load("meta");
  if (meta) $("#updated").textContent = `Updated ${new Date(meta.updated).toLocaleString()}, season ${meta.season - 1} to ${String(meta.season).slice(2)}, ${meta.games_ingested} games in the model`;
  try { await (routes[r] || home)(); } catch (e) { view.innerHTML = `<p>This page failed to load: ${esc(e.message)}. Try refreshing.</p>`; }
  window.scrollTo(0, 0);
}
addEventListener("hashchange", route);
route();
