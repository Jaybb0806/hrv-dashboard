const $ = (id) => document.getElementById(id);
const PHASE_LABEL = {
  idle: "Waiting", baseline: "Baseline", music: "Music playing",
  post: "Post-listening", done: "Session ended",
};
const MODE_LABEL = {
  ppi: "HRV mode · sit still",
  hr: "HR mode · no HRV",
};
const COLOR = { baseline: "var(--pre)", music: "var(--music)", post: "var(--post)" };
const isAdmin = new URLSearchParams(location.search).has("admin");
document.body.classList.toggle("audience", !isAdmin);
let snap = null;

const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
const sign = (v, d = 1) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(d));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mmss = (s) => {
  s = Math.max(0, Math.floor(s));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const niceMax = (v) => {
  if (!v) return 50;
  const step = v > 100 ? 25 : 10;
  return Math.ceil((v * 1.15) / step) * step;
};

function renderHeader(s) {
  $("session").textContent = `${s.session.name}, started ${new Date(s.session.started * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
  $("phase").dataset.phase = s.phase;
  $("phase-label").textContent = PHASE_LABEL[s.phase] || s.phase;
  $("phase-timer").textContent = s.phase === "idle" || s.phase === "done" ? "" : mmss(s.now - s.phase_started);
  $("clock").textContent = new Date(s.now * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  document.querySelectorAll("#admin [data-phase]").forEach((b) => b.classList.toggle("current", b.dataset.phase === s.phase));
  renderTimeline(s);
  $("mode").textContent = MODE_LABEL[s.mode] || "—";
  $("mode").dataset.mode = s.mode;
  document.querySelectorAll("#admin [data-mode]").forEach((b) => b.classList.toggle("current", b.dataset.mode === s.mode));
}

function renderTimeline(s) {
  const acts = ["baseline", "music", "post"];
  const span = {};
  s.phases.forEach(([p, t], i) => {
    if (acts.includes(p)) span[p] = [t, i + 1 < s.phases.length ? s.phases[i + 1][1] : null];
  });
  $("timeline").innerHTML = acts.map((p, i) => {
    const [t0, t1] = span[p] || [];
    const state = s.phase === p ? "now" : t0 != null ? "done" : "next";
    const time = t0 == null ? "" : mmss((t1 ?? s.now) - t0);
    return `<li class="act ${state}" data-phase="${p}"${state === "now" ? ' aria-current="step"' : ""}>
      <span class="act-n">${i + 1}</span><span class="act-name">${PHASE_LABEL[p]}</span><span class="act-time num">${time}</span></li>`;
  }).join("");
}

const TH_PHASE = { baseline: "เริ่มวัดก่อนฟัง", music: "เริ่มฟังเพลง", post: "เริ่มวัดหลังฟัง" };
const hhmm = (t, sec) => new Date(t * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", ...(sec ? { second: "2-digit" } : {}) });
const trendDir = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
const pctText = (v) => { const r = Math.round(v) || 0; return `${r > 0 ? "+" : ""}${r}%`; };
const beatDur = (hr) => (hr ? (60 / hr).toFixed(2) + "s" : "1s");

function setHeart(el, hr, dir) {
  el.classList.toggle("still", !hr);
  el.dataset.dir = dir || "";
  el.style.setProperty("--dur", beatDur(hr));
}

function renderOverview(s) {
  const base = s.group.means.baseline.mean;
  const lastPt = [...s.trend].reverse().find((p) => p[1] != null);
  const now = lastPt ? lastPt[1] : null;
  const pct = now != null && base ? ((now - base) / base) * 100 : null;
  const dir = trendDir(pct);
  $("ov-hrv").dataset.dir = dir;
  $("ov-hrv-val").textContent = fmt(now, 1);
  $("ov-hrv-pct").innerHTML = pct == null
    ? (base ? "รอสัญญาณ" : "รอค่าช่วงก่อนฟัง")
    : `<span class="num"><i class="tri ${pct >= 0 ? "up" : "down"}"></i>${pctText(pct)}</span> จากก่อนฟัง`;

  const { mean: hrMean, n: hrN } = liveHr(s);
  $("ov-hr-val").textContent = fmt(hrMean);
  $("ov-hr-n").textContent = hrN ? `${hrN} คน` : "";
  setHeart($("ov-hr-heart"), hrMean, "");

  let up = 0, down = 0;
  const anyGw = s.people.some((p) => p.gw);
  const cards = s.people.filter((p) => p.gw || !anyGw).map((p) => {
    const lv = p.live, b = p.baseline;
    const ch = lv?.valid && b?.valid ? ((lv.rmssd - b.rmssd) / b.rmssd) * 100 : null;
    if (ch != null) ch >= 0 ? up++ : down++;
    return { p, lv, ch, hr: p.hr ?? lv?.hr };
  });
  $("ov-up").textContent = up + down ? up : "—";
  $("ov-down").textContent = up + down ? down : "—";
  $("ov-cmp-n").textContent = up + down ? `จาก ${up + down} คน` : "";
  renderCards(s, cards);

  $("ov-base-note").innerHTML = base ? `<i class="dash"></i>ค่าเฉลี่ยก่อนฟัง <span class="num">${fmt(base, 1)} ms</span>` : "";
  $("trend-chart").innerHTML = trendChart(s, base);
  $("foot").textContent =
    `HRV ของแต่ละคน = RMSSD 60 วินาทีล่าสุด เทียบกับช่วงก่อนฟังของคนนั้น · ` +
    `ต้องมีคู่จังหวะดี ≥ ${s.config.min_pairs} และสัญญาณดี ≥ ${s.config.min_quality * 100}% · ระบบสาธิต ไม่ใช่การวินิจฉัยทางการแพทย์`;
}

function renderCards(s, cards) {
  const box = $("ov-people");
  if (box.dataset.ids !== cards.map((c) => c.p.id).join()) {
    box.dataset.ids = cards.map((c) => c.p.id).join();
    box.innerHTML = cards.map(({ p }) => `<div class="pc" data-id="${esc(p.id)}" role="button" tabindex="0" title="กดเพื่อเปิดมอนิเตอร์ HR">
      <div class="pc-head"><span class="num pc-id">${esc(p.p)}</span>
        <span class="pc-hr"><span class="num pc-bpm"></span><span class="heart still"><span class="ripple"></span><span class="beat"></span></span></span></div>
      <div class="num pc-val"></div><div class="pc-chg"></div></div>`).join("");
  }
  cards.forEach(({ p, lv, ch, hr }, i) => {
    const el = box.children[i];
    const dir = trendDir(ch);
    el.dataset.dir = dir;
    el.classList.toggle("off", p.status !== "online" && !p.held);
    el.classList.toggle("held", !!p.held);
    el.style.setProperty("--dur", beatDur(hr));
    el.querySelector(".pc-bpm").textContent = hr ? Math.round(hr) : "";
    setHeart(el.querySelector(".heart"), hr, dir);
    el.querySelector(".pc-val").textContent = s.mode === "hr" ? "—" : lv?.valid ? fmt(lv.rmssd, 1) : "—";
    const note = p.status !== "online" && !p.held ? "ออฟไลน์" : s.mode === "hr" ? "โหมด HR" : ch != null ? pctText(ch)
      : lv?.valid ? "รอก่อนฟัง" : "สัญญาณอ่อน";
    el.querySelector(".pc-chg").textContent = note;
    el.classList.toggle("note", ch == null);
  });
}

const _measure = document.createElement("canvas").getContext("2d");
function textW(text, px, weight = 600) {
  _measure.font = `${weight} ${px}px ${getComputedStyle(document.body).fontFamily}`;
  return _measure.measureText(text).width;
}
function tagRows(items, px, maxX = Infinity) {
  const ends = [];
  return items.map((it) => {
    const w = textW(it.text, px) + 24;
    it.flip = it.x + w > maxX;
    const start = it.flip ? it.x - w : it.x;
    let row = ends.findIndex((e) => start >= e);
    if (row < 0) row = ends.length;
    ends[row] = it.flip ? it.x + 8 : it.x + w;
    return row;
  });
}

function trendChart(s, base) {
  const box = $("trend-chart");
  const W = Math.max(400, box.clientWidth || 1000), H = Math.max(240, box.clientHeight || 420), L = 56, R = 16, B = 36;
  const end = s.phase === "done" ? s.phase_started : s.now;
  const first = s.phases.find(([p]) => COLOR[p]);
  if (!first) {
    let o = `<svg class="chart-empty" viewBox="0 0 ${W} ${H}" role="img" aria-label="ยังไม่เริ่มวัด"><g class="grid">`;
    for (let i = 0; i <= 5; i++) { const yy = 40 + (i / 5) * (H - 40 - B); o += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>`; }
    return o + `</g><text class="empty-msg" x="${(L + W - R) / 2}" y="${(40 + H - B) / 2}" text-anchor="middle">กราฟจะเริ่มเมื่อเริ่มวัดช่วงก่อนฟัง</text></svg>`;
  }
  const t0 = first[1], t1 = Math.max(end, t0 + 300);
  const pts = s.trend.filter((p) => p[1] != null && p[0] >= t0 && p[0] <= t1);
  const vals = pts.map((p) => p[1]).concat(base ? [base] : []);
  let lo = vals.length ? Math.min(...vals) : 20, hi = vals.length ? Math.max(...vals) : 50;
  if (hi - lo < 8) { const mid = (hi + lo) / 2; lo = mid - 4; hi = mid + 4; }
  const pad = (hi - lo) * 0.15;
  const step = [1, 2, 5, 10, 20].find((st) => (hi - lo + 2 * pad) / st <= 6) || 25;
  const ymin = Math.max(0, Math.floor((lo - pad) / step) * step), ymax = Math.ceil((hi + pad) / step) * step;
  const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const starts = s.phases.filter(([p, t]) => COLOR[p] && t >= t0);
  const tags = starts.map(([p, t]) => ({ x: x(t), text: `${TH_PHASE[p]} ${hhmm(t, true)}` }));
  const rows = tagRows(tags, 18, W - 4);
  const nRows = Math.max(1, ...rows.map((r) => r + 1)), T = 40 + (nRows - 1) * 32;
  const y = (v) => T + (1 - (v - ymin) / (ymax - ymin)) * (H - T - B);
  let out = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="HRV ของกลุ่มตามเวลา">`;
  s.phases.forEach(([p, t], i) => {
    if (!COLOR[p] || t < t0) return;
    const until = i + 1 < s.phases.length ? s.phases[i + 1][1] : end;
    out += `<rect x="${x(t)}" y="${T}" width="${Math.max(0, x(until) - x(t))}" height="${H - T - B}" fill="${COLOR[p]}" opacity="0.08"/>`;
    const k = starts.findIndex((st) => st[1] === t), row = rows[k] || 0, flip = tags[k]?.flip;
    out += `<line x1="${x(t)}" x2="${x(t)}" y1="${T - 30 - (nRows - 1 - row) * 32}" y2="${H - B}" stroke="${COLOR[p]}" stroke-width="2" stroke-dasharray="6 5"/>`;
    out += `<text class="ph-tag" x="${flip ? x(t) - 8 : x(t) + 8}" y="${T - 12 - (nRows - 1 - row) * 32}" text-anchor="${flip ? "end" : "start"}" style="fill:${COLOR[p]}">${TH_PHASE[p]} ${hhmm(t, true)}</text>`;
  });
  out += `<g class="grid">`;
  for (let v = ymin; v <= ymax + 1e-9; v += step) {
    out += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 10}" y="${y(v) + 5}" text-anchor="end">${Math.round(v)}</text>`;
  }
  out += `</g>`;
  const span = (t1 - t0) / 60, stepMin = span > 60 ? 15 : span > 30 ? 10 : span > 12 ? 5 : 2;
  for (let m = 0; m <= span; m += stepMin) {
    out += `<text class="axis" x="${x(t0 + m * 60)}" y="${H - 13}" text-anchor="middle">${hhmm(t0 + m * 60)}</text>`;
  }
  if (base) out += `<line x1="${L}" x2="${W - R}" y1="${y(base)}" y2="${y(base)}" stroke="var(--pre)" stroke-width="2" stroke-dasharray="8 7"/>`;
  let d = "", pen = false, area = "";
  s.trend.forEach(([t, v]) => {
    if (v == null || t > t1 || t < t0) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`;
    pen = true;
  });
  if (pts.length > 1) area = `M${x(pts[0][0]).toFixed(1)},${y(ymin)}` + pts.map(([t, v]) => `L${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("") + `L${x(pts[pts.length - 1][0]).toFixed(1)},${y(ymin)}Z`;
  out += `<path d="${area}" fill="var(--accent)" opacity="0.10"/>`;
  out += `<path d="${d}" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>`;
  const last = pts[pts.length - 1];
  if (last) {
    const c = base ? (last[1] >= base ? "var(--up)" : "var(--down)") : "var(--accent)";
    out += `<circle class="ping" cx="${x(last[0])}" cy="${y(last[1])}" r="9" fill="none" stroke="${c}" stroke-width="2"/>`;
    out += `<circle cx="${x(last[0])}" cy="${y(last[1])}" r="7" fill="${c}"/>`;
    const right = x(last[0]) < L + 160;
    out += `<text class="now-tag" x="${x(last[0]) + (right ? 14 : -14)}" y="${y(last[1]) - 16}" text-anchor="${right ? "start" : "end"}" style="fill:${c}">${last[1].toFixed(1)} ms</text>`;
  }
  return out + `</svg>`;
}

function renderPeople(s) {
  const after = s.group.post.n ? "post" : "music";
  const rows = s.people.map((p) => {
    const a = p.baseline, b = p[after];
    const ok = a?.valid && b?.valid;
    return { p, a, b, ok, pct: ok ? ((b.rmssd - a.rmssd) / a.rmssd) * 100 : null };
  });
  rows.sort((r1, r2) => (r2.ok - r1.ok) || ((r2.pct ?? 0) - (r1.pct ?? 0)) || r1.p.p.localeCompare(r2.p.p));
  const vmax = niceMax(Math.max(...rows.filter((r) => r.ok).flatMap((r) => [r.a.rmssd, r.b.rmssd]), 0));
  const W = 300, X = (v) => 8 + (v / vmax) * (W - 16);
  $("people-note").textContent = `Comparing "Baseline" with "${PHASE_LABEL[after]}" · scale 0–${vmax} ms`;
  $("dumbbells").style.gridTemplateRows = `repeat(${Math.ceil(rows.length / 2)}, auto)`;
  $("dumbbells").innerHTML = rows.map(({ p, a, b, ok, pct }) => {
    if (!ok) {
      const why = !a ? "Not measured yet" : "Not enough signal";
      return `<div class="db-row na"><span class="code">${esc(p.p)}</span><span>${why}</span><span class="vals">${a?.valid ? fmt(a.rmssd) : "—"} → ${b?.valid ? fmt(b.rmssd) : "—"}</span><span class="pct">—</span></div>`;
    }
    const up = b.rmssd >= a.rmssd, c = up ? "var(--up)" : "var(--down)";
    return `<div class="db-row"><span class="code">${esc(p.p)}</span>
      <svg viewBox="0 0 ${W} 24" preserveAspectRatio="none"><line x1="8" x2="${W - 8}" y1="12" y2="12" stroke="var(--line)"/>
        <line x1="${X(a.rmssd)}" x2="${X(b.rmssd)}" y1="12" y2="12" stroke="${c}" stroke-width="3"/>
        <circle cx="${X(a.rmssd)}" cy="12" r="6" fill="var(--pre)"/><circle cx="${X(b.rmssd)}" cy="12" r="6" fill="${c}"/></svg>
      <span class="vals">${fmt(a.rmssd)} → ${fmt(b.rmssd)}</span><span class="pct ${up ? "up" : "down"}">${sign(pct, 0)}%</span></div>`;
  }).join("");
}

function spark(trend) {
  const vals = trend.filter((v) => v != null);
  if (vals.length < 2) return `<svg viewBox="0 0 100 26"></svg>`;
  const max = Math.max(...vals) * 1.1, min = Math.min(...vals) * 0.9;
  let d = "", pen = false;
  trend.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    const x = (i / (trend.length - 1)) * 100, y = 24 - ((v - min) / (max - min || 1)) * 22;
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  });
  return `<svg viewBox="0 0 100 26" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="var(--music)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function renderLive(s) {
  $("tiles").innerHTML = s.people.map((p) => {
    const lv = p.live, q = lv ? lv.quality : 0;
    const hr = p.hr ?? lv?.hr;
    const rm = s.mode === "hr" ? "off (HR mode)"
      : lv?.valid ? `<b>${fmt(lv.rmssd)}</b> ms` : lv?.beats ? "not enough signal" : "—";
    return `<div class="tile ${p.status} ${q < s.config.min_quality ? "lowq" : ""} ${p.held ? "held" : ""}" data-id="${esc(p.id)}" title="Click for HR monitor">
      <div class="head"><span class="code">${esc(p.p)}</span><span class="st ${p.status}" title="${p.status}"></span></div>
      <div class="hr"><span class="heart">♥</span>${fmt(hr)}<small> bpm</small></div>
      <div class="rm">HRV ${rm}</div>
      <div class="q" title="Signal quality ${Math.round(q * 100)}%"><i style="width:${Math.round(q * 100)}%"></i></div>
      ${spark(p.trend)}</div>`;
  }).join("");
}

function liveHr(s) {
  const hrs = s.people.filter((p) => p.hr).map((p) => p.hr);
  return { mean: hrs.length ? hrs.reduce((a, b) => a + b, 0) / hrs.length : null, n: hrs.length };
}

const HR_HISTORY_S = 600, TREND_S = 120, STRIP_S = 10;
const BEAT_DELAY_S = 6;
const HR_MIN_SPAN = 10;
const SWEEP_GAP_S = 0.25;
const hrHist = { group: [] };
let clockOffset = 0;
const mon = { target: null, beats: [], timer: null, raf: null };
let openFromUrl = new URLSearchParams(location.search).get("monitor");

function recordHr(s) {
  clockOffset = s.now - Date.now() / 1000;
  const push = (id, v) => {
    const h = (hrHist[id] ||= []);
    h.push([s.now, v ?? null]);
    while (h.length && h[0][0] < s.now - HR_HISTORY_S) h.shift();
  };
  s.people.forEach((p) => push(p.id, p.held?.hr != null ? null : p.hr));
  push("group", liveHr(s).mean);
}

function openMonitor(target) {
  mon.target = target;
  mon.beats = [];
  mon.lastEnd = 0;
  mon.lo = mon.hi = null;
  $("monitor").hidden = false;
  fetch(`/api/hr?id=${encodeURIComponent(target)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && mon.target === target) { hrHist[target] = d.hr; renderMonitor(snap); } })
    .catch(() => {});
  clearInterval(mon.timer);
  if (target !== "group") {
    const poll = async () => {
      try {
        const r = await fetch(`/api/beats?id=${encodeURIComponent(target)}`);
        if (r.ok) mon.beats = (await r.json()).beats;
      } catch {}
    };
    poll();
    mon.timer = setInterval(poll, 1000);
  }
  renderMonitor(snap);
  cancelAnimationFrame(mon.raf);
  const frame = () => { drawMonitor(); mon.raf = requestAnimationFrame(frame); };
  frame();
}

function closeMonitor() {
  mon.target = null;
  $("monitor").hidden = true;
  clearInterval(mon.timer);
  cancelAnimationFrame(mon.raf);
}

function renderMonitor(s) {
  if (!mon.target || !s) return;
  const group = mon.target === "group";
  const p = group ? null : s.people.find((x) => x.id === mon.target);
  const hr = group ? liveHr(s).mean : p?.hr;
  const recent = (hrHist[mon.target] || []).filter(([t, v]) => v != null && t > s.now - TREND_S).map(([, v]) => v);
  const stat = (f) => (recent.length ? fmt(f(...recent)) : "—");
  $("mon-title").textContent = group ? "All participants · mean HR" : `${p?.p ?? "?"} · ${p?.id ?? mon.target}`;
  $("mon-sub").textContent = `${MODE_LABEL[s.mode] || ""} · ${PHASE_LABEL[s.phase] || s.phase}`;
  $("mon-hr").textContent = fmt(hr);
  $("monitor").classList.toggle("nosignal", hr == null);
  const rows = [
    ["Min (2 min)", `${stat(Math.min)} bpm`],
    ["Max (2 min)", `${stat(Math.max)} bpm`],
    ["Mean (2 min)", `${recent.length ? fmt(recent.reduce((a, b) => a + b, 0) / recent.length) : "—"} bpm`],
  ];
  if (group) rows.push(["Sensors with HR", `${liveHr(s).n} / ${s.people.length}`]);
  else rows.push(
    ["HRV (RMSSD, 60 s)", s.mode === "hr" ? "off (HR mode)" : p?.live?.valid ? `${fmt(p.live.rmssd)} ms` : "not enough signal"],
    ["Signal quality", s.mode === "hr" ? "— (HR mode)" : p?.live ? `${Math.round(p.live.quality * 100)}%` : "—"],
    ["Battery", p?.bat != null ? `${p.bat}%` : "—"],
    ["Status", p?.status ?? "—"]);
  $("mon-stats").innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("");
  $("mon-beats-label").textContent = group
    ? "ECG-style strip is per person: open a tile on the Live page"
    : s.mode === "hr" ? "ECG-style strip needs HRV (PPI) mode"
    : `ECG-style display · each complex is one real detected beat at its real time, ~${BEAT_DELAY_S} s behind · waveform shape is illustrative, not a recorded ECG · grey = artifact`;
  $("mon-beats").hidden = group || s.mode === "hr";
}

function fitCanvas(c) {
  const r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) {
    c.width = Math.round(r.width * dpr);
    c.height = Math.round(r.height * dpr);
  }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [g, r.width, r.height];
}

function monGrid(g, w, h, step) {
  g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
  g.strokeStyle = "rgba(61,255,138,0.08)"; g.lineWidth = 1;
  g.beginPath();
  for (let x = w; x > 0; x -= step) { g.moveTo(x, 0); g.lineTo(x, h); }
  for (let y = h; y > 0; y -= step) { g.moveTo(0, y); g.lineTo(w, y); }
  g.stroke();
}

function drawMonitor() {
  const now = Date.now() / 1000 + clockOffset;
  const green = getComputedStyle(document.documentElement).getPropertyValue("--mon-green").trim() || "#3dff8a";

  const [g, w, h] = fitCanvas($("mon-trend"));
  monGrid(g, w, h, 40);
  const pts = (hrHist[mon.target] || []).filter(([t]) => t > now - TREND_S - 2);
  const vals = pts.map(([, v]) => v).filter((v) => v != null);
  let tlo = vals.length ? Math.min(...vals) - 1 : 65, thi = vals.length ? Math.max(...vals) + 1 : 75;
  if (thi - tlo < HR_MIN_SPAN) { const mid = (tlo + thi) / 2; tlo = mid - HR_MIN_SPAN / 2; thi = mid + HR_MIN_SPAN / 2; }
  const ease = (cur, target, widening) => (cur == null || widening ? target : cur + (target - cur) * 0.03);
  mon.lo = ease(mon.lo, tlo, tlo < (mon.lo ?? tlo));
  mon.hi = ease(mon.hi, thi, thi > (mon.hi ?? thi));
  const lo = mon.lo, hi = mon.hi;
  const X = (t) => w - ((now - t) / TREND_S) * w;
  const Y = (v) => h - 8 - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (h - 16);
  g.fillStyle = "rgba(61,255,138,0.45)"; g.font = "12px system-ui"; g.textAlign = "left";
  const tick = hi - lo > 60 ? 20 : hi - lo > 30 ? 10 : hi - lo > 14 ? 5 : 2;
  for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick) g.fillText(v, 6, Math.max(14, Y(v) - 3));
  g.strokeStyle = green; g.lineWidth = 2.5; g.shadowColor = green; g.shadowBlur = 8;
  g.beginPath();
  let pen = false;
  pts.forEach(([t, v]) => {
    if (v == null) { pen = false; return; }
    pen ? g.lineTo(X(t), Y(v)) : g.moveTo(X(t), Y(v));
    pen = true;
  });
  g.stroke(); g.shadowBlur = 0;

  const c2 = $("mon-beats");
  if (c2.hidden) return;
  const [b, w2, h2] = fitCanvas(c2);
  monGrid(b, w2, h2, 25);
  const end = now - BEAT_DELAY_S;
  const sweep0 = Math.floor(end / STRIP_S) * STRIP_S;
  const near = mon.beats.filter(([t]) => t > end - STRIP_S - 1 && t < end + 1);
  const base = h2 * 0.72, amp = h2 * 0.66;
  const traceAt = (t, beats) => {
    let v = 0;
    for (const [bt] of beats) { const dt = t - bt; if (dt > -0.3 && dt < 0.45) v += ecg(dt); }
    return Math.max(8, Math.min(h2 - 8, base - v * amp));
  };
  const timeAt = (x) => {
    const t = sweep0 + (x / w2) * STRIP_S;
    if (t <= end) return t;
    return t - end < SWEEP_GAP_S ? null : t - STRIP_S;
  };
  const stroke = (beats, color, glow, onlyNear) => {
    b.strokeStyle = color; b.lineWidth = 2.2; b.shadowColor = color; b.shadowBlur = glow;
    b.beginPath();
    let pen = false;
    for (let x = 0; x <= w2; x++) {
      const t = timeAt(x);
      if (t == null || (onlyNear && !beats.some(([bt]) => t - bt > -0.3 && t - bt < 0.45))) { pen = false; continue; }
      const y = traceAt(t, beats);
      pen ? b.lineTo(x, y) : b.moveTo(x, y);
      pen = true;
    }
    b.stroke();
  };
  stroke(near, green, 8, false);
  stroke(near.filter(([, , ok]) => !ok), "rgba(150,160,170,0.9)", 0, true);
  b.shadowBlur = 0;
  const penX = ((end - sweep0) / STRIP_S) * w2;
  b.fillStyle = "#eafff2"; b.beginPath(); b.arc(penX, traceAt(end, near), 3.5, 0, Math.PI * 2); b.fill();

  if (mon.lastEnd && near.some(([t, , ok]) => ok && t > mon.lastEnd && t <= end)) {
    const heart = document.querySelector(".mon-hr .heart");
    heart.classList.add("beat");
    setTimeout(() => heart.classList.remove("beat"), 160);
  }
  mon.lastEnd = end;
}

function ecg(dt) {
  const g = (c, w) => Math.exp(-(((dt - c) / w) ** 2));
  return 0.12 * g(-0.17, 0.028) - 0.12 * g(-0.028, 0.009) + g(0, 0.011) - 0.25 * g(0.03, 0.011) + 0.3 * g(0.26, 0.05);
}

const SIGNAL = { wifi: [-67, -75], ble: [-70, -85] };
function signal(dbm, [good, ok]) {
  if (dbm == null || dbm === 0) return `<span class="muted">—</span>`;
  const level = dbm >= good ? 3 : dbm >= ok ? 2 : 1;
  const cls = ["", "weak", "ok", "good"][level];
  const bars = [1, 2, 3].map((i) => `<i class="${i <= level ? "on" : ""}"></i>`).join("");
  return `<span class="sig ${cls}" title="${["", "Weak: may drop", "Usable", "Good"][level]}"><span class="bars">${bars}</span>${dbm} dBm</span>`;
}

function renderDevices(s) {
  const ago = (a) => (a == null ? "—" : a < 60 ? `${Math.round(a)} s` : `${Math.round(a / 60)} min`);
  const gstat = (a) => (a < 5 ? "online" : a < 30 ? "stale" : "offline");
  const weakest = {};
  for (const p of s.people) {
    if (p.status !== "online" || !p.gw || p.rssi == null) continue;
    if (!weakest[p.gw] || p.rssi < weakest[p.gw].rssi) weakest[p.gw] = p;
  }
  setTable("gateways", s.gateways.length
    ? `<div class="sensor-table"><table><tr><th>ID</th><th>IP</th><th>WiFi → router</th><th>Weakest Polar</th><th>Sensors</th><th>Last seen</th></tr>${s.gateways.map((g) => {
        const w = weakest[g.id];
        const live = gstat(g.age) !== "offline";
        return `<tr><td>${esc(g.id)}</td><td>${esc(g.ip)}</td><td>${live ? signal(g.wifi, SIGNAL.wifi) : "—"}</td>
          <td>${w ? `${signal(w.rssi, SIGNAL.ble)} <span class="muted">${esc(w.p)}</span>` : "—"}</td>
          <td>${g.sensors}</td><td class="${gstat(g.age)}">${ago(g.age)}</td></tr>`;
      }).join("")}</table></div>
      <p class="muted sig-legend">WiFi: good ≥ ${SIGNAL.wifi[0]}, weak < ${SIGNAL.wifi[1]} dBm · BLE: good ≥ ${SIGNAL.ble[0]}, weak < ${SIGNAL.ble[1]} dBm</p>`
    : `<p class="muted">No gateway has sent data yet</p>`);
  const online = s.people.filter((p) => p.status === "online").length;
  setTable("sensors", `<p class="muted">Online ${online} / ${s.people.length}</p>
    <div class="sensor-table"><table><tr><th>Code</th><th>Sensor ID</th><th>GW</th><th>BLE → ESP32</th><th>Battery</th><th>Quality</th><th>Last seen</th></tr>
    ${s.people.map((p) => `<tr><td>${esc(p.p)}</td><td>${esc(p.id)}</td><td>${esc(p.gw ?? "—")}</td><td>${p.status === "offline" ? "—" : signal(p.rssi, SIGNAL.ble)}</td>
      <td>${p.bat == null ? "—" : p.bat + "%"}</td><td>${p.live ? Math.round(p.live.quality * 100) + "%" : "—"}</td>
      <td class="${p.status}">${ago(p.age)}</td></tr>`).join("")}</table></div>`);
}

const touching = new Set();
function setTable(id, html) {
  const el = $(id);
  if (touching.has(id)) return;
  const left = [...el.querySelectorAll(".sensor-table")].map((t) => t.scrollLeft);
  el.innerHTML = html;
  el.querySelectorAll(".sensor-table").forEach((t, i) => { t.scrollLeft = left[i] || 0; });
}
for (const id of ["gateways", "sensors"]) {
  const el = $(id);
  let idle;
  const hold = () => { clearTimeout(idle); touching.add(id); };
  const release = () => { clearTimeout(idle); idle = setTimeout(() => touching.delete(id), 1500); };
  el.addEventListener("touchstart", hold, { passive: true });
  el.addEventListener("touchend", release, { passive: true });
  el.addEventListener("touchcancel", release, { passive: true });
  el.addEventListener("scroll", () => { hold(); release(); }, { passive: true, capture: true });
}

const ACTS = ["baseline", "music", "post"];
const TH_ACT = { baseline: "ก่อนฟัง", music: "ระหว่างฟังเพลง", post: "หลังฟัง" };
const SAME_PCT = 3;
const RISE_PCT = 5, RISE_HOLD_S = 30;
const meanOf = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
const signPct = (v) => { const r = Math.round(v) || 0; return `${r > 0 ? "+" : ""}${r}%`; };
const dirOfPct = (v) => (v == null ? "" : v >= SAME_PCT ? "up" : v <= -SAME_PCT ? "down" : "same");

function summaryData(s) {
  const span = {};
  s.phases.forEach(([p, t], i) => {
    if (ACTS.includes(p)) span[p] = [t, i + 1 < s.phases.length ? s.phases[i + 1][1] : s.now];
  });
  const after = s.group.post.n ? "post" : s.group.music.n ? "music" : null;
  const cmp = after ? s.group[after] : null;
  const during = after === "post" && s.group.music.n ? s.group.music : null;

  const acts = ACTS.filter((p) => span[p]);
  const cc = s.people.filter((p) => acts.every((a) => p[a]?.valid));
  const useCc = cc.length >= 3;
  const phases = acts.map((a) => ({
    p: a,
    rmssd: useCc ? meanOf(cc.map((x) => x[a].rmssd)) : s.group.means[a].mean,
    hr: meanOf((useCc ? cc : s.people.filter((x) => x[a]?.valid)).map((x) => x[a].hr).filter(Boolean)),
    n: useCc ? cc.length : s.group.means[a].n,
  }));

  const pairs = after ? s.people.filter((p) => p.baseline?.valid && p[after]?.valid && p.baseline.hr && p[after].hr) : [];
  const hrBase = meanOf(pairs.map((p) => p.baseline.hr)), hrAfter = meanOf(pairs.map((p) => p[after].hr));

  const nMax = Math.max(0, ...s.trend.map((p) => p[2] || 0));
  const curve = s.trend.filter(([, v, n]) => v != null && (n || 0) >= Math.max(1, nMax * 0.5));
  const bl = span.baseline ? curve.filter(([t]) => t >= span.baseline[0] + 30 && t <= span.baseline[1]) : [];
  const base = meanOf(bl.map((p) => p[1])) ?? s.group.means.baseline.mean;
  let first = null, peak = null;
  if (span.music && base) {
    const [m0, m1] = span.music;
    let run = null;
    for (const [t, v] of curve) {
      if (t < m0 || t > m1) continue;
      if (!peak || v > peak[1]) peak = [t, v];
      if (first) continue;
      if (v >= base * (1 + RISE_PCT / 100)) {
        run = run || [t, v];
        if (t - run[0] >= RISE_HOLD_S) first = run;
      } else run = null;
    }
  }
  return { span, after, cmp, during, phases, ccN: useCc ? cc.length : null, hrBase, hrAfter, curve, base, first, peak };
}

const sumUnit = () => Math.min(22, Math.max(12, Math.min(innerWidth * 0.0085, innerHeight * 0.015)));

function summaryChart(s, d, W, H) {
  const u = sumUnit(), L = Math.round(u * 3.4), R = Math.round(u * 1.2), B = Math.round(u * 2.4), rowH = Math.round(u * 1.7);
  const t0 = d.span.baseline?.[0] ?? d.curve[0]?.[0] ?? s.session.started;
  const t1 = (d.span.post || d.span.music || d.span.baseline)?.[1] ?? s.now;
  const pts = d.curve.filter(([t]) => t >= t0 && t <= t1);
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const acts = ACTS.filter((p) => d.span[p]);
  const tags = acts.map((p) => ({ x: x(d.span[p][0]), text: `${TH_PHASE[p]} ${hhmm(d.span[p][0], true)}` }));
  const rows = tagRows(tags, Math.max(12, u * 1.2), W - 4);
  const nRows = Math.max(1, ...rows.map((r) => r + 1)), T = Math.round(u * 2.6) + (nRows - 1) * rowH;
  if (pts.length < 2) {
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="ยังไม่มีข้อมูล"><text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="empty">ยังไม่มีข้อมูล HRV ที่ใช้ได้</text></svg>`;
  }
  const vals = pts.map((p) => p[1]).concat(d.base ? [d.base] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 8) { const m = (hi + lo) / 2; lo = m - 4; hi = m + 4; }
  const pad = (hi - lo) * 0.18, step = [1, 2, 5, 10, 20].find((st) => (hi - lo + 2 * pad) / st <= 6) || 25;
  const ymin = Math.max(0, Math.floor((lo - pad) / step) * step), ymax = Math.ceil((hi + pad) / step) * step;
  const y = (v) => T + (1 - (v - ymin) / (ymax - ymin)) * (H - T - B);
  let o = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="HRV ของกลุ่มตลอดรอบ">`;
  acts.forEach((p, i) => {
    const sp = d.span[p], lift = (nRows - 1 - rows[i]) * rowH;
    o += `<rect class="sb" style="--i:${i}" x="${x(sp[0])}" y="${T}" width="${Math.max(0, x(sp[1]) - x(sp[0]))}" height="${H - T - B}" fill="${COLOR[p]}" fill-opacity="0.08"/>`;
    o += `<line x1="${x(sp[0])}" x2="${x(sp[0])}" y1="${T - u * 1.9 - lift}" y2="${H - B}" stroke="${COLOR[p]}" stroke-width="2" stroke-dasharray="6 5"/>`;
    o += `<text class="tag" x="${tags[i].flip ? x(sp[0]) - u * 0.5 : x(sp[0]) + u * 0.5}" y="${T - u * 0.75 - lift}" text-anchor="${tags[i].flip ? "end" : "start"}" style="fill:${COLOR[p]}">${TH_PHASE[p]} ${hhmm(sp[0], true)}</text>`;
  });
  o += `<g class="grid">`;
  for (let v = ymin; v <= ymax + 1e-9; v += step) o += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${L - u * 0.6}" y="${y(v) + u * 0.32}" text-anchor="end">${v}</text>`;
  o += `</g>`;
  const mins = (t1 - t0) / 60, sm = mins > 40 ? 10 : mins > 15 ? 5 : mins > 6 ? 2 : 1;
  for (let m = 0; m <= mins + 1e-9; m += sm) {
    const tx = x(t0 + m * 60), edge = tx > W - u * 2.2;
    o += `<text class="axis" x="${edge ? W : tx}" y="${H - u * 0.85}" text-anchor="${edge ? "end" : "middle"}">${hhmm(t0 + m * 60)}</text>`;
  }
  const poly = pts.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("L");
  if (d.base) {
    const yb = y(d.base).toFixed(1), shape = `M${x(pts[0][0]).toFixed(1)},${yb}L${poly}L${x(pts[pts.length - 1][0]).toFixed(1)},${yb}Z`;
    o += `<defs><clipPath id="sum-above"><rect x="0" y="0" width="${W}" height="${yb}"/></clipPath><clipPath id="sum-below"><rect x="0" y="${yb}" width="${W}" height="${H}"/></clipPath></defs>`;
    o += `<path class="fill-up" clip-path="url(#sum-above)" d="${shape}"/><path class="fill-down" clip-path="url(#sum-below)" d="${shape}"/>`;
    o += `<line x1="${L}" x2="${W - R}" y1="${yb}" y2="${yb}" stroke="var(--pre)" stroke-width="2" stroke-dasharray="8 7"/>`;
  }
  let line = "";
  pts.forEach(([t, v], i) => { line += `${i && t - pts[i - 1][0] <= 15 ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`; });
  o += `<path class="sum-line" d="${line}" pathLength="1"/>`;
  const mark = (pt, cls, label) => {
    if (!pt) return "";
    const [t, v] = pt, cx = x(t), cy = y(v), right = cx > W - u * 20, above = cy - u * 2 > T;
    return `<g class="mk ${cls}"><circle class="mk-ring" cx="${cx}" cy="${cy}" r="12"/><circle cx="${cx}" cy="${cy}" r="8"/>
      <text x="${cx + (right ? -u : u)}" y="${cy + (above ? -u * 1.1 : u * 2)}" text-anchor="${right ? "end" : "start"}">${label}</text></g>`;
  };
  o += mark(d.first, "mk-first", `เริ่มผ่อนคลาย ${hhmm(d.first?.[0] ?? 0, true)}`);
  o += mark(d.peak, "mk-peak", `สูงสุด ${fmt(d.peak?.[1], 1)} ms · ${hhmm(d.peak?.[0] ?? 0, true)}`);
  return o + `</svg>`;
}

function countUp(el, to, dec, delay, fmtFn) {
  if (to == null || !isFinite(to)) { el.textContent = "—"; return; }
  const show = (v) => { el.textContent = fmtFn(v); };
  const t0 = performance.now() + delay, dur = 1400;
  const step = (now) => {
    const k = Math.min(1, Math.max(0, (now - t0) / dur));
    show(Number((to * (1 - Math.pow(1 - k, 3))).toFixed(dec)));
    if (k < 1) requestAnimationFrame(step);
  };
  show(0);
  requestAnimationFrame(step);
  setTimeout(() => show(to), delay + dur + 200);
}

const sum = { open: false };
function openSummary(s = snap) {
  if (!s) return;
  const d = summaryData(s);
  const el = $("summary");
  el.hidden = false;
  sum.open = true;
  el.classList.remove("play");

  const pct = d.cmp ? d.cmp.pct_change : null;
  const dir = dirOfPct(pct);
  $("sum-hero").dataset.dir = dir;
  $("sh-label").textContent = d.after === "post" ? "หลังฟังเพลง" : d.after === "music" ? "ระหว่างฟังเพลง" : "สรุปผล";
  $("sh-verdict").innerHTML = !d.cmp ? "ยังไม่มีข้อมูลพอสำหรับสรุป"
    : dir === "up" ? "HRV ของกลุ่ม<b>สูงขึ้น</b>" : dir === "down" ? "HRV ของกลุ่ม<b>ลดลง</b>" : "HRV ของกลุ่ม<b>ใกล้เคียงก่อนฟัง</b>";
  $("sh-sub").innerHTML = d.cmp
    ? `<span class="num">${fmt(d.cmp.pre_mean, 1)}<i class="arr"></i>${fmt(d.cmp.post_mean, 1)} ms</span> · เทียบคนเดียวกัน ${d.cmp.n} คน`
    : "ต้องวัดช่วงก่อนฟังและช่วงฟังเพลง อย่างน้อยช่วงละ 1 นาที";
  const chip = (label, value, cdir) => `<span class="sh-chip" data-dir="${cdir}"><span>${label}</span><b class="num">${value}</b></span>`;
  const chips = [];
  if (d.during) chips.push(chip("ระหว่างฟังเพลง", signPct(d.during.pct_change), dirOfPct(d.during.pct_change)));
  if (d.hrBase && d.hrAfter) chips.push(chip("HR เฉลี่ย", `${fmt(d.hrBase)}<i class="arr"></i>${fmt(d.hrAfter)} bpm`, ""));
  if (d.cmp) chips.push(chip("ค่ากลางรายคน", signPct(d.cmp.median_pct), dirOfPct(d.cmp.median_pct)));
  $("sh-chips").innerHTML = chips.map((c, i) => c.replace('class="sh-chip"', `class="sh-chip" style="--i:${i}"`)).join("");

  const f = d.cmp && d.cmp.n ? d.cmp.n_increased / d.cmp.n : 0;
  $("sum-ring").dataset.has = d.cmp ? "1" : "";
  $("sum-ring").style.setProperty("--f", (f * 100).toFixed(1));
  $("rg-n").textContent = d.cmp ? ` / ${d.cmp.n} คน` : "";
  $("rg-note").innerHTML = d.cmp ? `ลดลง <b class="num">${d.cmp.n - d.cmp.n_increased}</b> คน` : "ยังไม่มีข้อมูล";

  const pmax = Math.max(1, ...d.phases.map((p) => p.rmssd || 0));
  const baseBar = d.phases.find((p) => p.p === "baseline")?.rmssd;
  $("sp-note").textContent = d.ccN ? `คนเดียวกัน ${d.ccN} คน` : "";
  $("sum-phase-bars").innerHTML = ACTS.map((p, i) => {
    const ph = d.phases.find((x) => x.p === p), sp = d.span[p];
    const pc = ph && baseBar && p !== "baseline" && ph.rmssd != null ? (ph.rmssd / baseBar - 1) * 100 : null;
    const h = ph?.rmssd != null ? (ph.rmssd / pmax) * 100 : 0;
    return `<div class="sp-col" style="--i:${i};--c:${COLOR[p]};--bh:${(h * 0.72).toFixed(1)}%">
      <div class="sp-plot">
        <div class="sp-val num">${ph?.rmssd != null ? `${ph.rmssd.toFixed(1)}<small> ms</small>` : "—"}</div>
        <div class="sp-chg num" data-dir="${dirOfPct(pc)}">${pc == null ? (p === "baseline" && ph ? "จุดเริ่มต้น" : "&nbsp;") : `<i class="tri ${pc >= 0 ? "up" : "down"}"></i>${signPct(pc)}`}</div>
        <div class="sp-bar"></div>
      </div>
      <div class="sp-name">${TH_ACT[p]}</div>
      <div class="sp-time num">${sp ? `${hhmm(sp[0])}–${hhmm(sp[1])}` : "ยังไม่ได้วัด"}${ph?.hr ? `<span class="sp-hr">HR ${fmt(ph.hr)} bpm</span>` : ""}</div>
    </div>`;
  }).join("");

  setTimeout(() => {
    const box = $("sum-plot");
    box.innerHTML = summaryChart(s, d, Math.max(500, box.clientWidth), Math.max(260, box.clientHeight));
    void el.offsetWidth;
    el.classList.add("play");
    countUp($("sh-big"), pct, 0, 500, (v) => signPct(v));
    countUp($("rg-up"), d.cmp ? d.cmp.n_increased : null, 0, 700, (v) => String(Math.round(v)));
    countUp($("rg-pct"), d.cmp ? f * 100 : null, 0, 700, (v) => `${Math.round(v)}%`);
  }, 30);
}
function closeSummary() {
  $("summary").hidden = true;
  sum.open = false;
}
function syncSummary(s) {
  const show = s.summary && !isAdmin, show2 = s.summary2 && !isAdmin;
  if (show && !sum.open) openSummary(s);
  else if (!show && sum.open) closeSummary();
  if (show2 && !sum2.open) openSummary2(s);
  else if (!show2 && sum2.open) closeSummary2();
  const b = $("show-summary");
  b.classList.toggle("current", !!s.summary);
  b.textContent = s.summary ? "Results on audience screen · hide" : "Show results on audience screen";
  const b2 = $("show-summary2");
  b2.classList.toggle("current", !!s.summary2);
  b2.textContent = s.summary2 ? "Individual results on screen · hide" : "Show individual results";
}

const sum2 = { open: false };
function openSummary2(s = snap) {
  if (!s) return;
  const after = s.group.post.n ? "post" : s.group.music.n ? "music" : null;
  const anyGw = s.people.some((p) => p.gw);
  const rows = s.people.filter((p) => p.gw || !anyGw).map((p) => {
    const a = p.baseline, b = after ? p[after] : null, ok = !!(a?.valid && b?.valid);
    return { p, a, b, ok, pct: ok ? (b.rmssd / a.rmssd - 1) * 100 : null };
  });
  const done = rows.filter((r) => r.ok), up = done.filter((r) => r.b.rmssd > r.a.rmssd).length, down = done.length - up;
  const vmax = Math.max(1, ...done.flatMap((r) => [r.a.rmssd, r.b.rmssd]));
  const n = rows.length, cols = n <= 4 ? Math.max(1, n) : n <= 8 ? 4 : n <= 24 ? 6 : 8;
  const el = $("summary2");
  el.style.setProperty("--cols", cols);
  el.style.setProperty("--rows", Math.max(1, Math.ceil(n / cols)));
  $("s2-label").textContent = after === "post" ? "หลังฟังเพลง" : after === "music" ? "ระหว่างฟังเพลง" : "สรุปผล";
  $("s2-note").innerHTML = `เทียบกับช่วงก่อนฟังของแต่ละคน<span class="s2-legend"><i class="sw pre"></i>ก่อนฟัง<i class="sw post"></i>${after === "music" ? "ระหว่างฟัง" : "หลังฟัง"}</span>`;
  $("s2-count").innerHTML = done.length
    ? `<span class="up"><i class="tri up"></i>สูงขึ้น<b class="num">${up}</b>คน</span><span class="down"><i class="tri down"></i>ลดลง<b class="num">${down}</b>คน</span>`
    : "";
  const w = (v) => `${((v / vmax) * 100).toFixed(1)}%`;
  $("s2-grid").innerHTML = !after ? `<p class="s2-empty">ยังไม่มีข้อมูลพอสำหรับสรุป</p>` : rows.map((r, i) => {
    const dir = !r.ok ? "" : r.b.rmssd > r.a.rmssd ? "up" : "down";
    return `<div class="s2-card" data-dir="${dir}" style="--i:${i}">
      <div class="s2-top"><span class="s2-id">${esc(r.p.p)}</span>${r.ok ? `<span class="s2-pct num"><i class="tri ${dir}"></i>${signPct(r.pct)}</span>` : ""}</div>
      ${r.ok ? `<div class="s2-bars"><span class="s2-bar pre" style="--w:${w(r.a.rmssd)}"></span><span class="s2-bar post" style="--w:${w(r.b.rmssd)}"></span></div>
      <div class="s2-vals num">${fmt(r.a.rmssd, 1)}<i class="arr"></i>${fmt(r.b.rmssd, 1)} ms</div>` : `<div class="s2-na">ข้อมูลไม่พอ</div>`}
    </div>`;
  }).join("");
  el.hidden = false;
  sum2.open = true;
  el.classList.remove("play");
  void el.offsetWidth;
  el.classList.add("play");
}
function closeSummary2() {
  $("summary2").hidden = true;
  sum2.open = false;
}

const VIEWS = ["overview", "people", "live", "devices"];
let view = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1)
  : (() => { try { return localStorage.getItem("hrv-view"); } catch { return null; } })() || "overview";
function show(v) {
  view = v;
  try { localStorage.setItem("hrv-view", v); } catch {}
  history.replaceState(null, "", `${location.pathname}${location.search}#${v}`);
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("on", el.id === `view-${v}`));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  if (snap) render();
}
function render() {
  renderHeader(snap);
  if (isAdmin) loadSessions(snap);
  ({ overview: renderOverview, people: renderPeople, live: renderLive, devices: renderDevices })[view](snap);
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "Escape") return closeMonitor();
  const i = Number(e.key) - 1;
  if (VIEWS[i]) show(VIEWS[i]);
  if (e.key === "f") document.documentElement.requestFullscreen?.();
});

$("ov-hr").addEventListener("click", () => openMonitor("group"));
$("ov-people").addEventListener("click", (e) => { const c = e.target.closest(".pc"); if (c) openMonitor(c.dataset.id); });
for (const id of ["ov-hr", "ov-people"]) $(id).addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  e.preventDefault();
  const c = e.target.closest(".pc");
  openMonitor(c ? c.dataset.id : "group");
});
$("tiles").addEventListener("click", (e) => { const t = e.target.closest(".tile"); if (t) openMonitor(t.dataset.id); });
$("mon-close").addEventListener("click", closeMonitor);
$("show-summary").addEventListener("click", () => post("/api/summary", { show: !snap?.summary }));
$("show-summary2").addEventListener("click", () => post("/api/summary2", { show: !snap?.summary2 }));

async function post(path, body) {
  const token = localStorage.getItem("hrv-admin-token") || "";
  let r;
  try {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 8000);
    r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Admin-Token": token },
                            body: JSON.stringify(body), signal: ctl.signal });
    clearTimeout(timer);
  } catch (e) {
    alert("NOT SENT: cannot reach the server.\nCheck this device is on the ROOT-JB WiFi, then press again.");
    return false;
  }
  if (r.status === 403) {
    const t = prompt(token ? "Wrong admin token. Enter it again" : "Enter admin token");
    if (t == null) return false;
    if (!/^[\x20-\x7e]+$/.test(t.trim())) { alert("The admin token must be English letters or digits"); return false; }
    localStorage.setItem("hrv-admin-token", t.trim());
    return post(path, body);
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) {   }
    alert(`NOT DONE: ${msg}`);
    return false;
  }
  return true;
}
let exportFor = null;
async function loadSessions(s) {
  if (!isAdmin || exportFor === s.session.id) return;
  exportFor = s.session.id;
  const list = await fetch("/api/sessions").then((r) => r.json()).catch(() => []);
  const sel = $("export-session");
  sel.innerHTML = list.map((x) => {
    const when = new Date(x.started * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    return `<option value="${x.id}">${x.id === s.session.id ? "This round" : `#${x.id}`} · ${esc(x.name)} · ${when}</option>`;
  }).join("");
  sel.value = String(s.session.id);
  sel.onchange();
}
$("export-session").onchange = () => {
  const v = $("export-session").value;
  $("export-xlsx").href = `/api/export.xlsx${v ? `?session=${v}` : ""}`;
};

if (isAdmin) {
  $("admin").hidden = false;
  document.querySelectorAll("#admin [data-phase]").forEach((b) => b.addEventListener("click", () => {
    if (confirm(`Switch to phase "${PHASE_LABEL[b.dataset.phase]}"?`)) post("/api/phase", { phase: b.dataset.phase });
  }));
  document.querySelectorAll("#admin [data-mode]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.mode === snap?.mode) return;
    const note = b.dataset.mode === "hr"
      ? "HR keeps tracking while people move, but HRV recording stops."
      : "HRV recording restarts (takes about 20 s). Participants should sit still.";
    if (confirm(`Switch to ${MODE_LABEL[b.dataset.mode]}?\n${note}`)) post("/api/mode", { mode: b.dataset.mode });
  }));
  $("new-session").addEventListener("click", () => {
    const name = prompt("Name for the new session (earlier sessions stay in the database)", "Session 2");
    if (name) post("/api/session", { name });
  });
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => $("conn").classList.add("ok");
  es.onmessage = (e) => {
    snap = JSON.parse(e.data);
    if (!snap.session) return;
    recordHr(snap);
    render();
    if (openFromUrl) { openMonitor(openFromUrl); openFromUrl = null; }
    renderMonitor(snap);
    syncSummary(snap);
  };
  es.onerror = () => { $("conn").classList.remove("ok"); };
  return es;
}
let stream = null, lastFrame = Date.now();
function reconnect() {
  if (stream) stream.close();
  lastFrame = Date.now();
  stream = connect();
  const onmsg = stream.onmessage;
  stream.onmessage = (e) => { lastFrame = Date.now(); onmsg(e); };
}
setInterval(() => { if (Date.now() - lastFrame > 6000) { $("conn").classList.remove("ok"); reconnect(); } }, 2000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - lastFrame > 2500) reconnect(); });
show(view);
reconnect();
