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

/* ---------------- This week: team tiles ---------------- */
const short = c => (c || "").replace(/ Conference$/, "").replace(/^Conference /, "");
async function home() {
  const [r, up] = await Promise.all([load("ratings"), load("upcoming")]);
  if (!r) { view.innerHTML = `<h1>This week</h1><p class="muted">Ratings appear after the first update.</p>`; return; }
  OUTS = (up && up.out) || {};
  const byTeam = {};
  ((up && up.games) || []).forEach(g => { (byTeam[g.h] ||= []).push(g); (byTeam[g.a] ||= []).push(g); });
  const confs = [...new Set(r.teams.map(t => short(t.conf)).filter(Boolean))].sort();
  const n = (up && up.games.length) || 0;
  view.innerHTML = `<h1>This week</h1>
    <p class="lede">Every Division I team in rating order, with its record, projected finish, and win chances for each game in the next seven days${n ? ` (${n} games)` : ""}. Rank movement is since last Monday.</p>
    <div class="controls"><input type="search" id="q" placeholder="Find a team" aria-label="Find a team">
      <select id="cf" aria-label="Conference"><option value="">All conferences</option>${confs.map(c => `<option>${esc(c)}</option>`).join("")}</select>
      <button class="chip" id="t25" aria-pressed="false">Top 25</button>
      <button class="chip" id="play" aria-pressed="false">Playing this week</button></div>
    <div id="tiles" class="tiles"></div>`;
  const draw = () => {
    const q = $("#q").value.trim().toLowerCase(), cf = $("#cf").value;
    const top = $("#t25").getAttribute("aria-pressed") === "true", pl = $("#play").getAttribute("aria-pressed") === "true";
    const list = r.teams.filter(t => (!q || T(t.id).n.toLowerCase().includes(q) || (T(t.id).f || "").toLowerCase().includes(q))
      && (!cf || short(t.conf) === cf) && (!top || t.rank <= 25) && (!pl || byTeam[t.id]));
    $("#tiles").innerHTML = list.map(t => tile(t, byTeam[t.id] || [])).join("") || `<p class="muted">No teams match.</p>`;
  };
  $("#q").addEventListener("input", draw); $("#cf").addEventListener("change", draw);
  ["#t25", "#play"].forEach(s => $(s).addEventListener("click", e => { const b = e.currentTarget; b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true"); draw(); }));
  draw();
}
function tile(t, games) {
  const tm = T(t.id), col = "#" + (tm.c || "6b7a99");
  const chg = t.chg == null || t.chg === 0 ? "" : `<span class="chg ${t.chg > 0 ? "up" : "down"}">${t.chg > 0 ? "▲" : "▼"}${Math.abs(t.chg)}</span>`;
  const last = t.last ? `<div class="tl-last"><span class="pill ${t.last.w ? "win" : "loss"}">${t.last.w ? "W" : "L"} ${esc(t.last.s)}</span> ${t.last.h === "away" ? "at" : "vs"} ${esc(T(t.last.o).n)} <span class="muted">${fmtShort(t.last.d)}</span></div>` : "";
  const gs = games.slice().sort((x, y) => (x.start || "").localeCompare(y.start || "")).map(g => {
    const home = g.h === t.id, opp = home ? g.a : g.h;
    const p = g.bp ?? g.mp, pw = home ? p : 1 - p, m = (g.bm ?? g.mm) * (home ? 1 : -1);
    return `<li><span class="tl-day">${fmtShort(g.date)}</span><span class="tl-opp">${g.n ? "vs" : home ? "vs" : "at"} ${esc(T(opp).n)}</span>
      <span class="tl-p ${pw >= .5 ? "fav" : "dog"}">${pct(pw)}</span><span class="tl-m">${m >= 0 ? "Wins by " + m.toFixed(1) : "Loses by " + (-m).toFixed(1)}</span>
      <span class="tl-bar"><i style="width:${(pw * 100).toFixed(0)}%;background:${col}"></i></span></li>`;
  }).join("");
  const outs = OUTS[t.id] ? `<div class="tl-out">Missed last game: ${OUTS[t.id].map(p => esc(p.name)).join(", ")} (about ${(OUTS[t.id].reduce((s, p) => s + (p.gs || 0), 0) * ABS_COEF).toFixed(1)} pts, included)</div>` : "";
  return `<article class="tile" style="--tc:${col}">
    <header><span class="tl-rank">${t.rank}</span>${chg}${tm.l ? `<img src="${esc(tm.l)}" alt="" width="34" height="34" loading="lazy">` : ""}
      <div><h3>${esc(tm.n)}</h3><span class="muted">${esc(short(t.conf))}</span></div></header>
    <dl class="tl-stats"><div><dt>Record</dt><dd>${esc(t.rec)}</dd></div><div><dt>Conf</dt><dd>${esc(t.crec)}</dd></div>
      <div><dt>Projected</dt><dd>${esc(t.proj || "-")}</dd></div><div><dt>NET</dt><dd>${t.net ?? "-"}</dd></div></dl>
    <dl class="tl-stats"><div><dt>Net rating</dt><dd><b>${sgn(t.em)}</b></dd></div><div><dt>Offense</dt><dd>${t.o.toFixed(1)}</dd></div>
      <div><dt>Defense</dt><dd>${t.d.toFixed(1)}</dd></div><div><dt>Pace</dt><dd>${t.t.toFixed(1)}</dd></div></dl>
    ${last}${outs}
    ${gs ? `<ul class="tl-games">${gs}</ul>` : `<p class="muted tl-none">No games in the next seven days.</p>`}
  </article>`;
}
const fmtShort = d => d ? new Date(d + "T12:00:00").toLocaleDateString(undefined, {weekday: "short", month: "numeric", day: "numeric"}) : "";
let OUTS = {};
const ABS_COEF = 0.1266;
function outNote(id) {
  const o = OUTS[id]; if (!o || !o.length) return "";
  const pts = o.reduce((s, p) => s + (p.gs || 0), 0) * ABS_COEF;
  return `<div class="meta out">${esc(T(id).n)} missed its last game without ${o.map(p => `${esc(p.name)} (${p.mpg} min a game)`).join(" and ")}. If still out, that costs about ${pts.toFixed(1)} points, already included in the prediction.</div>`;
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
    ${outNote(g.a)}${outNote(g.h)}
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
    <h2>Closing line value</h2>
    ${o.clv || acc.clv ? (() => { const c = acc.clv; return c && c.n ? `<p>In ${c.n} games where the model disagreed with the opening betting line by a point or more, the line moved before tip in ${c.moved}. It moved toward the model's number ${pct(c.toward, 1)} of the time, by ${c.avg_pts >= 0 ? "+" : ""}${c.avg_pts} points on average. Above 50% means the market tends to come around to the model, a sign of real skill that shows up long before win-loss records settle.</p>` : `<p class="muted">Appears once opening and closing lines have been captured for games where the model disagreed with the market.</p>`; })() : `<p class="muted">Appears once opening and closing lines have been captured for games where the model disagreed with the market.</p>`}
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

/* ---------------- Schedule ---------------- */
async function schedule() {
  const s = await load("schedule");
  if (!s) { view.innerHTML = `<h1>Schedule</h1><p class="muted">The season schedule appears after the next update.</p>`; return; }
  const G = s.games.map(x => ({id: x[0], date: x[1], start: x[2], h: x[3], a: x[4], n: x[5], conf: x[6], note: x[7], hp: x[8], ap: x[9], pm: x[10], pp: x[11], lk: x[12]}));
  const dates = [...new Set(G.map(g => g.date))].sort();
  const today = new Date().toLocaleDateString("en-CA", {timeZone: "America/New_York"});
  let cur = dates.find(d => d >= today) || dates[dates.length - 1];
  const graded = G.filter(g => g.lk && g.hp != null);
  const right = graded.filter(g => (g.pp >= .5) === (g.hp > g.ap)).length;
  const ll = graded.length ? graded.reduce((s, g) => { const y = g.hp > g.ap ? 1 : 0, p = Math.min(Math.max(g.pp, 1e-4), 1 - 1e-4); return s - (y * Math.log(p) + (1 - y) * Math.log(1 - p)); }, 0) / graded.length : null;
  const played = G.filter(g => g.hp != null).length;
  view.innerHTML = `<h1>Schedule</h1>
    <p class="lede">All ${G.length.toLocaleString()} Division I games from opening night to Selection Sunday. Future games carry the model's current prediction, which updates as results come in. Each game's prediction is locked at tip and graded against the final score, and those results feed back into the model.</p>
    <div class="scoreboard"><div><b>${played.toLocaleString()}</b><span>games played</span></div><div><b>${(G.length - played).toLocaleString()}</b><span>games left</span></div>
      <div><b>${graded.length ? pct(right / graded.length, 1) : "-"}</b><span>winners picked, locked predictions (${graded.length})</span></div>
      <div><b>${ll != null ? ll.toFixed(3) : "-"}</b><span>log loss, locked predictions</span></div></div>
    <div class="controls"><button class="chip" id="prev" aria-label="Previous day">‹ Prev</button>
      <input type="date" id="dp" min="${dates[0]}" max="${dates[dates.length - 1]}" aria-label="Date">
      <button class="chip" id="next" aria-label="Next day">Next ›</button>
      <input type="search" id="sq" placeholder="Or a team's full schedule" aria-label="Team schedule"></div>
    <div id="sl"></div>`;
  const row = g => {
    const fav = g.pp == null ? null : g.pp >= .5 ? g.h : g.a, pf = g.pp == null ? null : Math.max(g.pp, 1 - g.pp);
    let res = "";
    if (g.hp != null) {
      const hw = g.hp > g.ap;
      res = `<b>${T(g.a).n} ${g.ap}, ${T(g.h).n} ${g.hp}</b> ` + (g.lk ? `<span class="pill ${((g.pp >= .5) === hw) ? "win" : "loss"}">${((g.pp >= .5) === hw) ? "Right" : "Wrong"}</span>` : `<span class="muted">no pre-game prediction recorded</span>`);
    }
    const pred = fav == null ? `<span class="muted">No prediction</span>` : `${esc(T(fav).n)} ${pct(pf)} <span class="muted">by ${Math.abs(g.pm).toFixed(1)}${g.lk ? ", locked at tip" : g.hp == null ? ", current" : ""}</span>`;
    return `<div class="game"><div class="match">${teamCell(g.a)}<span class="at">${g.n ? "vs" : "at"}</span>${teamCell(g.h)}</div>
      <div class="nums">${g.hp == null || g.lk ? pred : ""}</div>
      ${g.hp == null && g.pp != null ? splitBar(g.a, g.h, g.pp) : ""}
      <div class="meta">${g.start && g.hp == null ? fmtTime(g.start) + ", " : ""}${g.conf ? "Conference game" : "Non-conference"}${g.n ? ", neutral site" : ""}${g.note ? ", " + esc(g.note) : ""}</div>
      ${res ? `<div class="meta">${res}</div>` : ""}</div>`;
  };
  const drawDay = () => {
    $("#dp").value = cur;
    const day = G.filter(g => g.date === cur);
    $("#sl").innerHTML = `<h3>${fmtDay(cur)} <span class="muted">(${day.length} games)</span></h3>` + (day.map(row).join("") || `<p class="muted">No Division I games this day.</p>`);
  };
  const drawTeam = q => {
    const ids = Object.keys(TEAMS).filter(k => (TEAMS[k].n + " " + (TEAMS[k].f || "")).toLowerCase().includes(q)).map(Number);
    if (!ids.length) { $("#sl").innerHTML = `<p class="muted">No team matches.</p>`; return; }
    const id = ids[0], gs = G.filter(g => g.h === id || g.a === id);
    const done = gs.filter(g => g.hp != null), w = done.filter(g => (g.h === id) === (g.hp > g.ap)).length;
    const expW = gs.filter(g => g.hp == null && g.pp != null).reduce((s, g) => s + (g.h === id ? g.pp : 1 - g.pp), 0);
    $("#sl").innerHTML = `<h3>${teamCell(id)} full schedule</h3><p class="muted">${w}-${done.length - w} so far against Division I; the model expects about ${expW.toFixed(1)} more wins in ${gs.length - done.length} remaining scheduled games.</p>` +
      gs.map(g => `<h3 class="sd">${fmtDay(g.date)}</h3>` + row(g)).join("");
  };
  const step = k => { const i = dates.indexOf(cur); cur = dates[Math.min(Math.max(i + k, 0), dates.length - 1)]; drawDay(); };
  $("#prev").addEventListener("click", () => step(-1)); $("#next").addEventListener("click", () => step(1));
  $("#dp").addEventListener("change", e => { cur = dates.find(d => d >= e.target.value) || cur; drawDay(); });
  $("#sq").addEventListener("input", e => { const q = e.target.value.trim().toLowerCase(); q.length >= 3 ? drawTeam(q) : drawDay(); });
  drawDay();
}

/* ---------------- Ratings ---------------- */
function ratingsTable(list, r, compact) {
  return `<div class="tablewrap"><table class="rt"><thead><tr><th>#</th><th class="l" data-k="name">Team</th><th data-k="net">NET</th><th class="l">Quad 1</th><th data-k="em">Net rating</th><th data-k="o">Offense</th><th data-k="d">Defense</th><th data-k="t">Pace</th>${compact ? "" : `<th data-k="g">Games</th>`}</tr></thead><tbody>
    ${list.map((t, i) => `<tr><td>${t.rank ?? i + 1}</td><td class="l">${teamCell(t.id)}</td><td>${t.net ?? "-"}</td><td class="l">${esc(t.q1 || "-")}</td><td><b>${sgn(t.em)}</b></td><td>${t.o.toFixed(1)}</td><td>${t.d.toFixed(1)}</td><td>${t.t.toFixed(1)}</td>${compact ? "" : `<td>${t.g}</td>`}</tr>`).join("")}</tbody></table></div>`;
}
async function ratings() {
  const r = await load("ratings");
  if (!r) { view.innerHTML = `<h1>Ratings</h1><p class="muted">Ratings will appear after the first pipeline run.</p>`; return; }
  r.teams.forEach((t, i) => t.rank = i + 1);
  view.innerHTML = `<h1>Ratings</h1>
    <p class="lede">Points per 100 possessions against an average Division I team on a neutral floor. Net rating is offense minus defense; a +10 team beats an average team by about 7 points in a 68 possession game. Defense is points allowed, so lower is better.</p>
    <p class="muted">${r.net_through ? `NET is the NCAA's official ranking through games of ${esc(r.net_through)}; Quad 1 is the record in the committee's top tier of games. ` : "The NCAA's official NET ranking appears here once it is published in early December. "}League average ${r.mu} points per 100 possessions, average pace ${r.tau} possessions, home court worth ${r.home} points.</p>
    <div class="controls"><input type="search" id="rq" placeholder="Find a team" aria-label="Find a team in ratings"></div><div id="rtab"></div>`;
  let key = "em", dir = -1;
  const draw = () => {
    const q = $("#rq").value.trim().toLowerCase();
    const list = r.teams.filter(t => !q || T(t.id).n.toLowerCase().includes(q)).sort((a, b) => key === "name" ? T(a.id).n.localeCompare(T(b.id).n) * -dir : key === "net" ? ((a.net ?? 999) - (b.net ?? 999)) * -dir : (a[key] - b[key]) * dir);
    $("#rtab").innerHTML = ratingsTable(list, r, false);
    document.querySelectorAll(".rt th[data-k]").forEach(th => th.addEventListener("click", () => { const k = th.dataset.k; dir = key === k ? -dir : (k === "d" || k === "net" ? 1 : -1); key = k; draw(); }));
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
      `<div class="notice"><b>Projected field.</b> Until Selection Sunday, every conference tournament is simulated thousands of times and the most likely champion takes each automatic bid. At-large spots and seeds follow ${b.seeding === "NET" ? "the NCAA's official NET ranking, the committee's main sorting tool" : "model ratings until the NCAA publishes its NET ranking in early December"}. Game odds always come from the model. This is a projection, not a guess at the committee's exact choices.</div>`}
    <p class="muted">Highlighted picks are the favorite in each game; the percentage is that team's chance to win that game if the matchup happens. The odds table shows each team's chance to reach every round across all simulations.</p>
    <h2>Final Four and title</h2>
    <div class="tablewrap"><table><tbody>${ff.filter(p => p.round !== "First Four").map(p => `<tr><td class="l">${p.round === "F4" ? "National semifinal" : "Championship"}${p.region ? " (" + esc(p.region) + ")" : ""}</td><td class="l">${teamCell(p.a)} vs ${teamCell(p.b)}</td><td>${esc(T(p.w).n)} ${pct(p.p)}</td></tr>`).join("")}</tbody></table></div>
    ${ff.some(p => p.round === "First Four") ? `<h3>First Four</h3><div class="tablewrap"><table><tbody>${ff.filter(p => p.round === "First Four").map(p => `<tr><td class="l">${esc(p.region)}</td><td class="l">${teamCell(p.a)} vs ${teamCell(p.b)}</td><td>${esc(T(p.w).n)} ${pct(p.p)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <h2>Regions</h2><div class="regions">${regions.map(regionHTML).join("")}</div>
    ${b.field_odds && b.field_odds.length ? `<h2>Chance to make the field</h2>
    <p class="muted">Automatic bid is the chance to win the conference tournament. Make the field adds the chance of an at-large spot in the simulated outcomes. Teams sorted by chance to make it.</p>
    <div class="tablewrap"><table><thead><tr><th class="l">Team</th><th class="l">Conference</th><th>${b.seeding === "NET" ? "NET" : "Rating rank"}</th><th>Automatic bid</th><th>Make the field</th></tr></thead><tbody>
      ${b.field_odds.map((t, i) => `<tr><td class="l">${teamCell(t.id)}</td><td class="l">${esc(t.conf)}</td><td>${t.net ?? "-"}</td><td>${pct(t.auto, 1)}</td><td><b>${t.make >= .995 ? ">99%" : pct(t.make, 1)}</b></td></tr>`).join("")}
    </tbody></table></div>` : ""}
    <h2>Chance to reach each round</h2>
    <div class="tablewrap"><table><thead><tr><th>Seed</th><th class="l">Team</th><th class="l">Region</th><th>Second round</th><th>Sweet 16</th><th>Elite Eight</th><th>Final Four</th><th>Title game</th><th>Champion</th></tr></thead><tbody>
      ${champ.map(t => `<tr><td>${t.seed}</td><td class="l">${teamCell(t.id)}</td><td class="l">${esc(t.region)}</td>${["R32","S16","E8","F4","Final","Champ"].map(c => `<td>${t[c] < .001 ? "<0.1%" : pct(t[c], 1)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>`;
}

/* ---------------- Method ---------------- */
const TEX = (s, d) => `<span class="${d ? "eq" : ""}" data-tex="${esc(s)}" data-d="${d ? 1 : 0}">${esc(s)}</span>`;
async function renderTex() {
  for (let k = 0; k < 40 && !window.katex; k++) await new Promise(r => setTimeout(r, 100));
  if (!window.katex) return;
  document.querySelectorAll("[data-tex]").forEach(el => { try { katex.render(el.dataset.tex, el, {displayMode: el.dataset.d === "1", throwOnError: false}); } catch {} });
}
const ref = (label, url) => `<a href="${url}" target="_blank" rel="noopener">${label}</a>`;
async function method() {
  const [meta, r] = await Promise.all([load("meta"), load("ratings")]);
  const bt = meta?.backtest, P = meta?.params || {};
  const s = m => m ? `<tr><td>${m.n.toLocaleString()}</td><td>${pct(m.accuracy, 1)}</td><td>${m.logloss.toFixed(3)}</td><td>${m.brier.toFixed(3)}</td><td>${m.margin_mae.toFixed(2)}</td><td>${m.margin_rmse.toFixed(2)}</td><td>${m.total_mae.toFixed(2)}</td></tr>` : "";
  const head = `<thead><tr><th>Games</th><th>Winners</th><th>Log loss</th><th>Brier</th><th>Margin MAE</th><th>Margin RMSE</th><th>Total MAE</th></tr></thead>`;
  view.innerHTML = `<div class="method"><h1>How the model works</h1>
  <p class="lede">A tempo-free, opponent-adjusted rating model built from the published research on college basketball prediction, tuned on 2014 to 2020 and tested on 2022 to 2026, seasons it never saw. Every choice below cites the work it comes from, and every setting was kept only if it improved out-of-sample accuracy.</p>

  <h2>Live settings right now</h2>
  <dl class="kv"><dt>League average efficiency</dt><dd>${r?.mu ?? "-"} points per 100 possessions</dd>
  <dt>League average pace</dt><dd>${r?.tau ?? "-"} possessions per game</dd>
  <dt>Home court</dt><dd>${r?.home ?? "-"} points (fitted from this season's games)</dd>
  <dt>Margin spread, σ₀</dt><dd>${r?.sigma ?? "-"} points${r?.sigma_games ? ` (recalibrated on ${r.sigma_games} graded games)` : " (backtest value until 100 games are graded)"}</dd>
  <dt>Preseason prior weight</dt><dd>${P.prior_games ?? 2.5} games</dd></dl>

  <h2>1. Count possessions, not points</h2>
  <p>Teams play at very different speeds, so raw scores mix quality with pace. Following Dean Oliver's <i>Basketball on Paper</i> (2004) and the standard set out by Kubatko, Oliver, Pelton and Rosenbaum (2007), each game is converted to possessions from the box score, averaged across both teams:</p>
  ${TEX(String.raw`\text{Poss}=\tfrac12\Big[(\text{FGA}-\text{ORB}+\text{TO}+0.44\,\text{FTA})_A+(\text{FGA}-\text{ORB}+\text{TO}+0.44\,\text{FTA})_B\Big]`, 1)}
  <p>Efficiency is then points per 100 possessions. Pomeroy notes the free throw coefficient moves possession counts by only about 1% between common choices, so the standard 0.44 is used.</p>

  <h2>2. Adjust every game for opponent and venue</h2>
  <p>Each team game is one observation: the offense's points per 100 possessions. It is modeled as the league average on that date, plus the offense's strength, minus the opposing defense's strength, plus home court:</p>
  ${TEX(String.raw`y_g = 100\,\frac{\text{Pts}_g}{\text{Poss}_g} = \mu(t_g) + o_i - d_j + h\,\ell_g + \varepsilon_g,\qquad \ell_g\in\{+1,0,-1\}`, 1)}
  <p>All teams are solved at once with a regularized least squares fit, the approach Kenneth Massey introduced for sports ratings (1997), in the additive offense and defense form Ken Pomeroy adopted in 2016:</p>
  ${TEX(String.raw`\min_{o,d,h}\;\sum_g w_g\,(y_g-\hat y_g)^2+\lambda\sum_i\big(o_i-o_i^{\text{prior}}\big)^2+\lambda\sum_j\big(d_j-d_j^{\text{prior}}\big)^2,\qquad \lambda = k\,\bar w`, 1)}
  <p>The penalty pulls each team toward its preseason prior with the weight of <b>k = ${P.prior_games ?? 2.5} games</b>, so the prior's share falls automatically as real games arrive, roughly <i>k / (k + games played)</i>. Net rating is <i>o + d</i>: points per 100 possessions better than an average team on a neutral floor.</p>
  <p class="cite">Tested: weighting recent games more (half-lives from 20 to 120 days) made predictions worse on the tuning seasons, so every game counts equally, consistent with Pomeroy reducing recency weight in 2016.</p>

  <h2>3. Home court, fitted rather than assumed</h2>
  <p>Pomeroy found an average home edge of 3.75 points in 2002 to 2009 and later measured only 2.78 points in conference games, and attributes the long decline to easier travel and slower pace. Single-venue estimates are mostly noise: a team measured at 7.6 points regressed to 4.0 in later years. So this model fits one league-wide home term from the current season's games, plus a small separate term for the team listed as home at a neutral site. A 2021 Samford University analysis found home teams won almost 5 points less often in the nearly fanless 2020 to 2021 season, so that season is treated as an outlier in testing.</p>

  <h2>4. Pace</h2>
  ${TEX(String.raw`\text{Poss}_g = \tau(t_g) + \tau_i + \tau_j + \eta_g`, 1)}
  <p>Pace is fitted the same way. Adding a within-season trend for both pace and efficiency removed a 2 point overestimate of totals late in the season, when games slow down.</p>

  <h2>5. The preseason prior</h2>
  <p>Without a prior, early ratings are biased and swing wildly; Pomeroy showed early-season deviations predict later moves when no prior is used. Each team starts from last season's rating carried over at ${P.carry ?? 0.92}, then adjusted for roster change. The roster adjustment is a regression fitted on 2013 to 2026, predicting a team's season-end rating from:</p>
  ${TEX(String.raw`\text{EM}_s=\beta_0+\beta_1\text{EM}_{s-1}+\beta_2\,\text{EM}_{s-1}\!\cdot\!\text{RM}+\beta_3\text{RM}+\beta_4\text{TV}+\beta_5\text{TM}+\beta_6\text{EM}_{s-2}`, 1)}
  <p>RM is the share of last season's minutes that return, TV is the value of incoming transfers (each transfer's minute share times his old team's rating), and TM is incoming transfer minutes. Half of this roster signal is blended into the carryover prior, the mix that tested best. On the 2022 to 2026 holdout the roster model cut preseason rating error by 11% and improved November log loss from 0.483 to 0.481. Hickman and Metz (2024) found betting markets misprice roster turnover and coaching changes only for the first few weeks of a season, which is exactly the window this prior targets.</p>

  <h2>6. From ratings to a game projection</h2>
  ${TEX(String.raw`\widehat{\text{Poss}}=\tau+\tau_A+\tau_B`, 1)}
  ${TEX(String.raw`\widehat{\text{Pts}}_A=\frac{\widehat{\text{Poss}}}{100}\big(\mu+o_A-d_B+h\ell\big),\qquad \widehat{\text{Pts}}_B=\frac{\widehat{\text{Poss}}}{100}\big(\mu+o_B-d_A-h\ell\big)`, 1)}
  ${TEX(String.raw`\hat M=\widehat{\text{Pts}}_A-\widehat{\text{Pts}}_B,\qquad \hat T=\widehat{\text{Pts}}_A+\widehat{\text{Pts}}_B`, 1)}

  <h2>7. From a margin to a win probability</h2>
  <p>Hal Stern (1991) showed game margins scatter around the expected margin in a near-normal curve, and Pomeroy switched KenPom to margin-based probabilities in 2016. Faster games have more possessions and so more randomness, so the spread grows with pace:</p>
  ${TEX(String.raw`P(A\text{ wins})=\Phi\!\left(\frac{\hat M}{\sigma}\right),\qquad \sigma=\sigma_0\sqrt{\widehat{\text{Poss}}/68}`, 1)}
  <p>σ₀ = ${meta?.stage2?.sigma0 ?? 11.1} points was fitted on 2014 to 2026 together with the second stage below. Pomeroy's data show real margins have too few 1 point games, so the curve is slightly less exact for spreads near zero than for win chances.</p>
  <p><b>Feedback from real results.</b> As this season's games are graded, σ₀ is re-estimated from the model's actual misses and blended with the backtest value, so the probabilities stay calibrated to how this season is really playing:</p>
  ${TEX(String.raw`\sigma_0^{\text{live}}=\sqrt{\frac{n\cdot\text{MSE}_{\text{season}}+k\,\sigma_0^2}{n+k}},\qquad k=1000\text{ games}`, 1)}

  <h2>8. Using the betting market</h2>
  <p>The closing point spread is the strongest public forecast in the research: Harville (1980) and Stern (1991) used it as the benchmark, Gandar and coauthors (1998) showed line moves from open to close make it more accurate, and Lopez and Matthews (2015) won the 2014 Kaggle March Madness contest by combining a spread-based model (31%) with an efficiency model (69%). Lines are captured before tip three times a day. Where one exists, the posted pick blends the two:</p>
  ${TEX(String.raw`M_{\text{blend}}=w\,\hat M+(1-w)(-\text{spread}),\qquad w=\frac{\sum x\,(a-m)}{\sum x^2},\;x=\hat M-m`, 1)}
  <p>Here <i>a</i> is the actual margin and <i>m</i> the market's. ${meta?.stage2?.market_blend ? `The weight was fitted on ${meta.stage2.market_blend.holdout.n.toLocaleString()}+ past games with sportsbook lines from CollegeBasketballData.com: the model earns w = ${meta.stage2.market_blend.model_weight}, and on the untouched 2022 to 2025 games the blend scored ${meta.stage2.market_blend.holdout.logloss_blend} log loss against ${meta.stage2.market_blend.holdout.logloss_line} for the line alone, so the model adds information the market misses.` : "The weight starts at w = 0.3."} Once about 1,500 of this season's graded games have a line, the weight is refitted from them. When ESPN shows no line, CollegeBasketballData.com is used as a backup source. The Results page grades the model, the market and the blend separately.</p>

  <h2>9. Season, conference tournaments and the bracket</h2>
  <p>Every remaining scheduled game is simulated 2,000 times with the probabilities above, giving each team's projected record and conference record. Projected conference standings seed every conference tournament, which is then simulated 5,000 times for automatic bid odds. At-large spots and seeds follow the NCAA's NET ranking (the committee's main sorting tool, adopted in 2018) once it is published, and model ratings before then. The bracket is simulated 50,000 times. In each simulation every team's strength gets one random shift that lasts the whole tournament:</p>
  ${TEX(String.raw`\text{EM}_i^{(s)}=\text{EM}_i+u_i^{(s)},\qquad u_i^{(s)}\sim\mathcal N(0,\,1.0^2)`, 1)}
  <p>That shift size was chosen by scoring round-by-round odds against 12 real tournaments (2014 to 2026). Kvam and Sokol (2006) found most of what predicts tournament success is already visible by mid-February, and the testing here agreed: refitting daily during March and using a separate tournament σ both added nothing.</p>

  <h2>10. What was tested and left out</h2>
  <p>The literature consistently finds that adjusted efficiency carries most of the signal. Lopez and Matthews found adjusted efficiencies beat raw ones and that interaction terms made predictions worse. Pomeroy (2015) showed offense controls about 83% of three point percentage, so opponent three point shooting is treated as luck and not used. Dean Oliver's Four Factors are a breakdown of efficiency rather than extra information, so they are not separate inputs. Two ideas were tested here and rejected: capping blowout margins (every cap from 15 to 25 points made predictions worse on the tuning seasons) and days of rest (no gain on the holdout, and the fitted effect had the wrong sign).</p>

  <h2>11. Second stage: Elo, travel, altitude and missing players</h2>
  <p>The rating model's margin is combined with four more signals in a final regression. It was fitted on 2014 to 2020 and scored on the untouched 2022 to 2026 seasons; once it proved out, the weights below were refitted on all seasons for live use:</p>
  ${TEX(String.raw`M=\beta_0\hat M_{\text{ratings}}+\beta_1\hat M_{\text{Elo}}+\beta_2\,\Delta\text{km}+\beta_3\,\text{Alt}+\beta_4\,\Delta\text{GS}_{\text{missing}}`, 1)}
  ${meta?.stage2 ? `<dl class="kv"><dt>Rating model weight</dt><dd>${meta.stage2.coef.model}</dd>
    <dt>Elo weight</dt><dd>${meta.stage2.coef.elo_margin}</dd>
    <dt>Per 1,000 km of extra visitor travel</dt><dd>${meta.stage2.coef.travel_diff} points</dd>
    <dt>Home at altitude, lowland visitor</dt><dd>${meta.stage2.coef.alt_adv} points</dd>
    <dt>Per point of missing game score</dt><dd>${meta.stage2.coef.abs_diff} points</dd></dl>` : ""}
  <p><b>Elo.</b> A second, independent rating that moves game by game with a margin-of-victory multiplier, the other major family of rating systems in the literature. Lopez and Matthews won their Kaggle contest by blending different forecasts, and the blend here lands at about 86% ratings and 14% Elo.</p>
  <p><b>Travel and altitude.</b> Pomeroy links the long decline of home court to easier travel and notes altitude teams keep larger home edges. Each team's home city comes from its home games and venues are placed with a US city coordinate table. The visitor's extra distance and a high-altitude home floor (Wyoming, Air Force, Colorado, New Mexico, Utah, Nevada, Montana State, Idaho State, Northern Arizona, Southern Utah) each add a fitted amount.</p>
  <p><b>Bart Torvik's ratings.</b> Torvik's T-Rank is one of the leading public tempo-free systems. His day-by-day archive (via cbbdata) made a fair test possible, using only the ratings published the day before each game. Added as a third model, it was the largest single improvement found: ${meta?.stage2?.with_torvik ? `holdout log loss ${meta.stage2.with_torvik.holdout.logloss_without} to ${meta.stage2.with_torvik.holdout.logloss_with} on ${meta.stage2.with_torvik.holdout.n.toLocaleString()} games (95% interval for the gain ${meta.stage2.with_torvik.holdout.ci[0]} to ${meta.stage2.with_torvik.holdout.ci[1]})` : "a significant holdout gain"}. With it, the live weights are about half this site's ratings, a third Torvik and a tenth Elo. If Torvik's ratings are unavailable on a given day, the model falls back to the weights above. ${meta?.torvik_note ? `Latest pull: ${esc(meta.torvik_note)}.` : ""}</p>
  <p><b>Missing players.</b> Each player's value is his season average Hollinger game score. When a regular (20+ minutes a team game) missed his team's previous game, the same signal available before tip, his value counts against the team.</p>
  ${meta?.stage2 ? `<p>On the untouched holdout the second stage cut log loss from ${meta.stage2.holdout.logloss_base} to ${meta.stage2.holdout.logloss_new} (95% bootstrap interval for the gain: ${meta.stage2.holdout.ci[0]} to ${meta.stage2.holdout.ci[1]}, so the improvement is not noise), raised winners picked from ${pct(meta.stage2.holdout.acc_base, 1)} to ${pct(meta.stage2.holdout.acc_new, 1)}, and improved NCAA tournament log loss from ${meta.stage2.holdout.tourney_base} to ${meta.stage2.holdout.tourney_new}. The backtest table below is the rating model alone, before this stage.</p>` : ""}

  <h2>12. Tested on the past</h2>
  <p>The model was replayed week by week, refitting every Monday with only the games played before it, exactly as it runs live. Settings were chosen using 2014 to 2020 only; 2022 to 2026 is an untouched test.</p>
  ${bt ? `<div class="tablewrap"><table>${head}<tbody>
    <tr><td colspan="7" class="l"><b>Untouched test seasons, 2022 to 2026</b></td></tr>${s(bt.holdout)}
    <tr><td colspan="7" class="l"><b>NCAA tournament games, 2014 to 2026</b></td></tr>${s(bt.ncaa_tournament)}
    <tr><td colspan="7" class="l"><b>All seasons, 2014 to 2026</b></td></tr>${s(bt.all)}</tbody></table></div>
  <p class="muted">For comparison, Lopez and Matthews' winning Kaggle efficiency model scored 0.487 log loss on late-season games from 2003 to 2013 and 0.529 on the 2014 tournament.</p>
  <h3>Calibration</h3><p class="muted">When the model says a team wins 75% of the time, how often does it? Gray bar is the predicted chance, orange mark is what happened.</p>
  <div class="cal">${(bt.calibration || []).map(c => `<span>${pct(c.pred)}</span><div class="track"><i style="width:${c.pred*100}%"></i><em style="left:calc(${c.act*100}% - 1px)"></em></div><span>${pct(c.act)} won</span>`).join("")}</div>` : ""}

  <h2>Limits</h2>
  <p>No model can promise a perfect bracket. Even with exactly correct probabilities, the chance of calling all 63 tournament games is far below one in a million, and Lopez and Matthews showed that even true probabilities win a large contest only about one time in eight. The goal is honest, well calibrated odds for every game and round.</p>

  <h2>References</h2>
  <ol class="refs">
    <li>Oliver, D. (2004). <i>Basketball on Paper</i>. Brassey's.</li>
    <li>Kubatko, J., Oliver, D., Pelton, K., Rosenbaum, D. (2007). ${ref("A starting point for analyzing basketball statistics", "https://doi.org/10.2202/1559-0410.1070")}. <i>Journal of Quantitative Analysis in Sports</i> 3(1).</li>
    <li>Massey, K. (1997). ${ref("Statistical models applied to the rating of sports teams", "https://masseyratings.com/theory/massey97.pdf")}. Bluefield College.</li>
    <li>Pomeroy, K. (2016). ${ref("Ratings methodology update", "https://kenpom.com/blog/ratings-methodology-update/")}; ${ref("How to measure home court advantage", "https://kenpom.com/blog/how-to-measure-home-court-advantage/")}; ${ref("Preseason ratings: why weight", "https://kenpom.com/blog/preseason-ratings-why-weight/")}; ${ref("Offense vs. defense: 3-point percentage", "https://kenpom.com/blog/offense-vs-defense-3point-percentage/")}; ${ref("The missing 1-point games", "https://kenpom.com/blog/?p=268")}.</li>
    <li>Stern, H. (1991). On the probability of winning a football game. <i>The American Statistician</i> 45, 179 to 183.</li>
    <li>Harville, D. (1980). Predictions for NFL games via linear-model methodology. <i>Journal of the American Statistical Association</i> 75, 516 to 524.</li>
    <li>Kvam, P., Sokol, J. (2006). A logistic regression/Markov chain model for NCAA basketball. <i>Naval Research Logistics</i> 53, 788 to 803. ${ref("LRMC", "https://www2.isye.gatech.edu/~jsokol/lrmc/about_lrmc.html")}</li>
    <li>Lopez, M., Matthews, G. (2015). ${ref("Building an NCAA men's basketball predictive model and quantifying its success", "https://arxiv.org/abs/1412.0248")}. <i>Journal of Quantitative Analysis in Sports</i> 11(1).</li>
    <li>Gandar, J., Dare, W., Brown, C., Zuber, R. (1998). Informed traders and price variations in the betting market for professional basketball games. <i>Journal of Finance</i> 53(1).</li>
    <li>Hickman, D., Metz, N. (2024). ${ref("How quickly do markets incorporate new information? Evidence from the NCAA men's basketball betting market", "https://verso.uidaho.edu/esploro/outputs/journalArticle/How-quickly-do-markets-incorporate-new/996683436201851")}. <i>Applied Economics</i>.</li>
    <li>NCAA (2020). ${ref("The NET, explained", "https://www.ncaa.com/news/basketball-men/article/2020-05-12/net-explained-ncaa-adopts-new-college-basketball-ranking-replace-rpi")}.</li>
    <li>Samford University Center for Sports Analytics (2021). ${ref("Did minimal crowds in 2020 to 2021 impact home team performance?", "https://www.samford.edu/sports-analytics/fans/2021/Did-Minimal-Crowds-in-2020-2021-Impact-College-Basketball-Home-Team-Performance")}</li>
  </ol>
  <p class="muted">Data: ESPN public endpoints, the sportsdataverse archive, and NCAA.com.</p></div>`;
  renderTex();
}

const routes = {"": home, schedule, results, ratings, bracket, method};
async function route() {
  const r = location.hash.replace(/^#\/?/, "");
  setNav(r in routes ? r : "");
  TEAMS = (await load("teams")) || {};
  const meta = await load("meta");
  let warn = "";
  if (meta) {
    const ageH = (Date.now() - new Date(meta.updated)) / 36e5;
    const w = (meta.warnings || []).slice();
    if (ageH > 30) w.unshift(`The last successful update was ${Math.floor(ageH)} hours ago, so predictions and results may be out of date. Updates normally run three times a day.`);
    if (w.length) warn = `<div class="notice warn" role="status">${w.map(esc).join("<br>")}</div>`;
  }
  if (meta) $("#updated").textContent = `Updated ${new Date(meta.updated).toLocaleString()}, season ${meta.season - 1} to ${String(meta.season).slice(2)}, ${meta.games_ingested} games in the model`;
  try { await (routes[r] || home)(); if (warn) view.insertAdjacentHTML("afterbegin", warn); } catch (e) { view.innerHTML = `<p>This page failed to load: ${esc(e.message)}. Try refreshing.</p>`; }
  window.scrollTo(0, 0);
}
addEventListener("hashchange", route);
route();
