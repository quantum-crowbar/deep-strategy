// One renderer for live matches and replays (D04). Plain JS for now: the TypeScript
// port (D12) waits until a TS compiler is set up on this machine.
"use strict";
const $ = (id) => document.getElementById(id);
const canvas = $("c"), ctx = canvas.getContext("2d");
ctx.imageSmoothingEnabled = false;

const S = {
  header: null, frames: [], notes: {}, cur: 0, mode: "none", playing: true, tps: 20,
  followLive: true, ws: null, selected: null, skin: { kinds: {} }, images: {}, result: null, last: 0,
  t: 0, maps: [], dir: {},
};
const COLORS = ["#e0b43a", "#7e8aa8"];

// ---------------------------------------------------------------- skin
async function loadSkin(name) {
  S.skin = { kinds: {} };
  S.images = {};
  if (name === "placeholder") return;
  try {
    const m = await (await fetch(`skins/${name}.json`)).json();
    S.skin = m;
    for (const [kind, spec] of Object.entries(m.kinds || {})) {
      for (const p of m.players || ["p0", "p1"]) {
        const url = "skins/" + spec.image.replace("{p}", p);
        const img = new Image();
        img.onload = () => { img.ok = true; };       // draw() falls back per sprite until loaded
        img.src = url;
        S.images[`${kind}/${p}`] = img;
      }
    }
  } catch (e) { S.skin = { kinds: {} }; }
}

// ---------------------------------------------------------------- loading
function reset(mode) {
  if (S.ws) { S.ws.close(); S.ws = null; }
  Object.assign(S, { frames: [], notes: {}, cur: 0, t: 0, maps: [], dir: {}, mode, result: null, selected: null, followLive: true });
  $("log").textContent = "";
  $("scrub").disabled = mode === "none";
}

function setHeader(h) {
  S.header = h;
  const names = h.specs.map((s) => s.name);
  $("title").textContent = `${names[0]} vs ${names[1]} (seed ${h.seed})`;
  S.tile = canvas.width / h.map.size;
  S.mine = {};
}

function watchLive() {
  reset("live");
  const ws = new WebSocket(`ws://${location.host}/ws`);
  S.ws = ws;
  $("status").textContent = "connecting…";
  ws.onopen = () => { $("status").textContent = "live"; };
  ws.onclose = () => { if (S.mode === "live") $("status").textContent = "disconnected (the match may have ended)"; };
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.type === "hello") { setHeader(d); S.tps = d.tps; syncSpeed(); }
    else if (d.type === "frame") {
      S.frames.push(d);
      if (d.notes) for (const k in d.notes) S.notes[k] = d.notes[k];
      S.playing = !d.paused;
      $("play").textContent = d.paused ? "Play" : "Pause";
    } else if (d.type === "end") {
      S.result = d.result;
      $("status").textContent = `finished: ${resultText(d.result)} — saved as replays/${d.replay}`;
      refreshReplayList();
    }
  };
}

async function openReplay(name) {
  reset("replay");
  const r = await (await fetch(`/replays/${name}`)).json();
  setHeader(r.header);
  S.frames = r.frames;
  S.notes = r.notes;
  S.result = r.result;
  S.playing = true;
  $("status").textContent = `replay: ${resultText(r.result)}`;
}

function resultText(r) {
  if (!r) return "";
  const n = S.header.specs.map((s) => s.name);
  const w = r.winner === -1 ? "draw" : `${n[r.winner]} wins`;
  return `${w} at tick ${r.ticks} (${r.reason})`;
}

async function refreshReplayList() {
  try {
    const names = await (await fetch("/replays.json")).json();
    const sel = $("replays");
    const keep = sel.value;
    sel.innerHTML = '<option value="">replays…</option>' + names.map((n) => `<option>${n}</option>`).join("");
    sel.value = keep;
  } catch (e) { /* static hosting: no list */ }
}

// ---------------------------------------------------------------- drawing
function entsOf(f) { return f ? f.e : []; }

function draw() {
  const h = S.header;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!h) { ctx.fillStyle = "#9aa"; ctx.font = "16px sans-serif"; ctx.fillText("Choose 'Watch live' or open a replay.", 20, 40); return; }
  const T = S.tile, n = h.map.size;
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const rock = h.map.terrain[y][x] === "#";
    ctx.fillStyle = rock ? "#4a4f57" : ((x + y) % 2 ? "#3b5a3a" : "#3f6040");
    ctx.fillRect(x * T, y * T, T, T);
    if (rock) { ctx.fillStyle = "#62676f"; ctx.fillRect(x * T + 3, y * T + 3, T - 6, T - 6); }
  }
  const idx = Math.min(S.frames.length - 1, Math.floor(S.t));
  S.cur = Math.max(0, idx);
  const f = S.frames[S.cur], prev = S.frames[S.cur - 1];
  if (!f) return;
  const pos = {};                      // id -> displayed [x, y] in tile units, glided between tiles
  for (const e of entsOf(f)) pos[e[0]] = displayPos(e, S.cur, S.t);
  const ents = entsOf(f).slice().sort((a, b) => pos[a[0]][1] - pos[b[0]][1] || a[0] - b[0]);
  for (const e of ents) drawEntity(e, pos[e[0]]);
  ctx.lineWidth = 2;
  const prevById = {};
  for (const e of entsOf(prev)) prevById[e[0]] = e;
  const at = (id) => pos[id] || (prevById[id] ? [prevById[id][3], prevById[id][4]] : null);
  for (const [a, t] of f.a || []) {
    const A = at(a), B = at(t);
    const owner = (byRow(S.cur, a) || prevById[a] || [0, 0])[1];
    if (!A || !B) continue;
    ctx.strokeStyle = COLORS[owner] || "#fff";
    ctx.beginPath(); ctx.moveTo((A[0] + .5) * T, (A[1] + .5) * T); ctx.lineTo((B[0] + .5) * T, (B[1] + .5) * T); ctx.stroke();
  }
  if (S.selected != null && pos[S.selected]) {
    const p = pos[S.selected];
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
    ctx.strokeRect(p[0] * T - 2, p[1] * T - 2, T + 4, T + 4);
  }
  updatePanels(f);
}

// Entities teleport one tile per move in the sim. To animate, find the entity's most recent move
// (within the last 8 frames) and glide from the old tile to the new one over `speed` ticks, which
// is exactly how long the sim makes it wait before the next move, so motion looks continuous.
function byRow(i, id) {
  let m = S.maps[i];
  if (!m) { m = S.maps[i] = new Map(); for (const e of entsOf(S.frames[i])) m.set(e[0], e); }
  return m.get(id);
}

function displayPos(e, i, t) {
  const [id, , ki, x, y] = e;
  const speed = (S.header.speed || {})[S.header.kinds[ki]] || 4;
  for (let j = i, n = 0; j > 0 && n < 8; j--, n++) {
    const before = byRow(j - 1, id);
    if (!before) break;                                   // just spawned: no glide
    if (before[3] !== x || before[4] !== y) {
      const k = Math.min(1, Math.max(0, (t - j) / speed));
      if (x !== before[3]) S.dir[id] = Math.sign(x - before[3]);
      return [before[3] + (x - before[3]) * k, before[4] + (y - before[4]) * k];
    }
  }
  return [x, y];
}

function drawEntity(e, p) {
  const [id, owner, ki, , , hp] = e;
  const [x, y] = p;
  const kind = S.header.kinds[ki], T = S.tile, max = S.header.maxhp[kind] || hp;
  const cx = (x + .5) * T, cy = (y + .5) * T;
  const spec = (S.skin.kinds || {})[kind];
  const img = spec && owner >= 0 ? S.images[`${kind}/p${owner}`] : null;
  if (kind === "mine") {
    ctx.fillStyle = "#c9a227"; ctx.beginPath(); ctx.arc(cx, cy, T * .42, 0, 7); ctx.fill();
    ctx.fillStyle = "#fff8"; ctx.font = `${T * .4}px sans-serif`; ctx.textAlign = "center";
    ctx.fillText(String(hp), cx, cy + T * .15);
    return;
  }
  if (img && img.ok) {
    const frame = spec.frames > 1 ? Math.floor(performance.now() / 110 + id) % spec.frames : 0;   // idle loop on a wall clock, not sim ticks
    const w = spec.w * T, h = w * spec.fh / spec.fw;
    const flip = S.dir[id] < 0;
    ctx.save();
    ctx.translate(cx, 0);
    if (flip) ctx.scale(-1, 1);
    ctx.drawImage(img, frame * spec.fw, 0, spec.fw, spec.fh, -w / 2, cy - h / 2 + (spec.yoff || 0) * T + T * .2, w, h);
    ctx.restore();
    if (spec.label) { ctx.fillStyle = "#fff"; ctx.font = `bold ${T * .38}px sans-serif`; ctx.textAlign = "center"; ctx.fillText(spec.label, cx, cy + T * .75); }
  } else {
    ctx.fillStyle = COLORS[owner] || "#aaa";
    const isB = kind === "hall" || kind === "barracks";
    if (isB) { const s = kind === "hall" ? 1.6 : 1.2; ctx.fillRect(cx - s * T / 2, cy - s * T / 2, s * T, s * T);
      ctx.fillStyle = "#000a"; ctx.font = `${T * .5}px sans-serif`; ctx.textAlign = "center"; ctx.fillText(kind === "hall" ? "H" : "B", cx, cy + T * .17); }
    else {
      ctx.beginPath(); ctx.arc(cx, cy, T * (kind === "worker" ? .26 : .4), 0, 7); ctx.fill();
      ctx.fillStyle = "#000b"; ctx.font = `bold ${T * .45}px sans-serif`; ctx.textAlign = "center";
      ctx.fillText(kind[0].toUpperCase(), cx, cy + T * .16);
    }
  }
  if (hp < max) {
    const w = T * (kind === "hall" ? 1.6 : 1), x0 = cx - w / 2, yb = cy - T * (kind === "hall" ? 1.15 : .75);
    ctx.fillStyle = "#000a"; ctx.fillRect(x0, yb, w, 4);
    ctx.fillStyle = hp / max > .5 ? "#4c4" : hp / max > .25 ? "#ca3" : "#d44"; ctx.fillRect(x0, yb, w * Math.max(0, hp / max), 4);
  }
}

function updatePanels(f) {
  const h = S.header, names = h.specs.map((s) => s.name);
  const counts = [0, 1].map((p) => {
    const c = {};
    for (const e of f.e) if (e[1] === p) { const k = h.kinds[e[2]]; c[k] = (c[k] || 0) + 1; }
    return c;
  });
  $("players").innerHTML = [0, 1].map((p) =>
    `<div class="p${p}"><b>${names[p]}</b> — gold ${f.g[p]}<br><span class="dim">` +
    Object.entries(counts[p]).map(([k, v]) => `${k} ${v}`).join(", ") + `</span></div>`).join("");
  $("tick").textContent = `tick ${f.t}`;
  const sc = $("scrub");
  sc.max = Math.max(0, S.frames.length - 1);
  if (document.activeElement !== sc) sc.value = S.cur;
  // agent notes: latest per player at or before this tick
  const lines = [];
  for (let t = f.t; t > Math.max(0, f.t - 400) && lines.length < 10; t--) {
    const n = S.notes[t];
    if (n) for (let p = 0; p < 2; p++) if (n[p]) lines.push(`t${t} ${names[p]}: ${n[p]}`);
  }
  $("log").textContent = lines.join("\n") || "(no notes yet)";
  if (S.selected != null) {
    const e = f.e.find((e) => e[0] === S.selected);
    $("sel").textContent = e ? `#${e[0]} ${h.kinds[e[2]]} (${e[1] >= 0 ? names[e[1]] : "neutral"})  hp ${e[5]}/${h.maxhp[h.kinds[e[2]]]}  tile ${e[3]},${e[4]}` : `#${S.selected} is gone`;
  }
}

// ---------------------------------------------------------------- loop & controls
function frameLoop(now) {
  const dt = Math.min(0.25, (now - (S.last || now)) / 1000);
  S.last = now;
  const latest = S.frames.length - 1;
  if (latest >= 0) {
    if (S.mode === "replay" && S.playing) S.t = Math.min(latest, S.t + dt * S.tps);
    else if (S.mode === "live" && S.followLive) {
      S.t = Math.min(latest, S.t + dt * S.tps);
      if (latest - S.t > Math.max(3, S.tps * 0.5)) S.t = latest - 1;     // far behind (dropped frames): jump up
    }
  }
  draw();
  requestAnimationFrame(frameLoop);
}

function send(o) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(o)); }
function syncSpeed() { const sel = $("speed"); if (![...sel.options].some((o) => +o.value === S.tps)) sel.add(new Option(S.tps, S.tps)); sel.value = S.tps; }

$("play").onclick = () => {
  if (S.mode === "live") send({ cmd: S.playing ? "pause" : "play" });
  else { S.playing = !S.playing; $("play").textContent = S.playing ? "Pause" : "Play"; }
};
$("step").onclick = () => {
  if (S.mode === "live") { send({ cmd: "pause" }); send({ cmd: "step" }); }
  else { S.playing = false; $("play").textContent = "Play"; S.t = Math.min(S.frames.length - 1, Math.floor(S.t) + 1); }
};
$("speed").onchange = (e) => { S.tps = +e.target.value; if (S.mode === "live") send({ cmd: "speed", v: S.tps }); };
$("scrub").oninput = (e) => { S.t = +e.target.value; S.followLive = S.t >= S.frames.length - 2; };
$("live").onclick = watchLive;
$("load").onclick = () => { const v = $("replays").value; if (v) openReplay(v); };
$("skin").onchange = (e) => loadSkin(e.target.value);
canvas.onclick = (ev) => {
  if (!S.header) return;
  const r = canvas.getBoundingClientRect(), T = S.tile * r.width / canvas.width;
  const tx = (ev.clientX - r.left) / T, ty = (ev.clientY - r.top) / T;
  const f = S.frames[S.cur];
  let best = null;
  for (const e of entsOf(f)) { const q = displayPos(e, S.cur, S.t); const d = Math.hypot(q[0] + .5 - tx, q[1] + .5 - ty); if (d < 1.2 && (!best || d < best[0])) best = [d, e[0]]; }
  S.selected = best ? best[1] : null;
  if (!best) $("sel").textContent = "click a unit";
};

(async () => {
  await loadSkin("tinyswords");
  await refreshReplayList();
  const q = new URLSearchParams(location.search);
  if (q.get("replay")) openReplay(q.get("replay"));
  else if (q.get("live") !== null || location.protocol.startsWith("http")) watchLive();
  requestAnimationFrame(frameLoop);
})();
