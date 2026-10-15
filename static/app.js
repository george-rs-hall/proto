/* shared helpers for the wallet (/) and agents (/agents) pages */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const gbp = p => "£" + ((p || 0) / 100).toFixed(2);
const pct = p => Math.round((p || 0) * 100) + "%";
const cap1 = t => String(t ?? "").charAt(0).toUpperCase() + String(t ?? "").slice(1);
const short = (s, a = 6, b = 4) => { s = String(s || ""); return s.length > a + b + 1 ? s.slice(0, a) + "…" + s.slice(-b) : s; };
const fmtDay = d => { try { return new Date(d).toLocaleDateString("en-GB", {day:"numeric", month:"short"}); } catch (e) { return d || ""; } };

async function api(path, opts) {
  const o = opts ? {method: opts.method || "POST", headers: {"content-type": "application/json"}, body: opts.body ? JSON.stringify(opts.body) : undefined} : {};
  const r = await fetch(path, o);
  if (!r.ok) throw new Error(path + " → " + r.status);
  return r.status === 204 ? null : r.json();
}
function toast(msg) {
  let t = $("#toast"); if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 2600);
}

/* signed objects may arrive as {payload, signature} or flat */
const pl = o => (o && o.payload) ? o.payload : (o || {});
const item0 = r => ((r && r.items) || [])[0] || {};
const amountOf = r => { const its = (r && r.items) || []; return its.reduce((x, i) => x + (i.price_pence || 0) * (i.qty || i.quantity || 1), 0) || (r && r.total_pence) || 0; };

/* status the UI shows for an audit row: pay splits into charge vs hold; undone rows are done */
function kind(row) {
  if (!row || !row.decision) return "run";
  if (row.decision === "pay") {
    if (/cancel|refund/.test(row.stripe_status || "")) return "done";
    return row.payment_mode === "hold" ? "hold" : "pay";
  }
  return row.decision;
}
const LABEL = {run:"Running", pay:"Paid", hold:"Held", ask:"Needs your OK", decline:"Declined", block:"Blocked", done:"Undone"};

/* facts: {relation, object} | "text" */
const FACT_TEXT = {waist_size: o => "Waist " + o, prefers_fit: o => cap1(o) + " fit", avoids_material: o => "Avoids " + o, shoe_size: o => "Shoe size " + o,
  tshirt_size: o => "T-shirt " + o, t_shirt_size: o => "T-shirt " + o, favourite_colour: o => "Likes " + o, allergic_to: o => cap1(o) + " allergy", diet: o => cap1(o),
  lives_in: o => "Lives in " + o, seat_preference: o => cap1(o) + " seat", hotel_budget: o => "Hotels ≤ " + o, phone: o => o, has_dog: o => "Dog called " + o,
  coffee_order: o => cap1(o), reads_on: o => "Reads on " + o, commutes_by: o => "Commutes by " + o, prefers_delivery: o => "Delivery: " + o,
  avoids_fragrance: o => "Avoids " + o, avoids: o => "Avoids " + o, travel_prefers: o => "Prefers " + o, prefers: o => "Prefers " + o, avoids_payment: o => "No " + o,
  clothing_budget: o => "Clothes ≤ " + o, gift_budget: o => "Gifts ≤ " + o, passport_expires: o => "Passport to " + o, charger: o => o + " charger", subscribes_to: o => cap1(o) + " subscriber"};
function factText(f) {
  if (typeof f === "string") return f;
  if (!f) return "";
  if (f.text) return f.text;
  const rel = f.relation || f.relationship_name || "", ob = f.object ?? f.target ?? "";
  return FACT_TEXT[rel] ? FACT_TEXT[rel](ob) : cap1(rel.replace(/_/g, " ")) + " " + ob;
}
function factsMap(facts) {
  const m = {}; (facts || []).forEach(f => { if (f && typeof f === "object") m[f.relation || f.relationship_name] = String(f.object ?? f.target ?? ""); });
  return m;
}

/* what was asked: parse the request text for kind and colour */
const KINDS = ["jeans","shorts","trousers","chinos","shirt","t-shirt","jacket","shoes","trainers"];
const COLOURS = ["black","navy","charcoal","blue","white","grey","gray","brown","green","red"];
function asked(request) {
  const r = String(request || "").toLowerCase();
  return {kind: KINDS.find(k => r.includes(k)) || null, colour: COLOURS.find(c => r.includes(c)) || null};
}
/* line-by-line compare of the signed cart item against the request + signed memories */
function cartLines(it, row, facts) {
  const a = asked(row.request), F = factsMap(facts), L = [];
  if (it.kind) L.push(!a.kind || it.kind === a.kind ? ["ok", cap1(it.kind), "kind"] : ["no", `${cap1(it.kind)} — you asked for ${a.kind}`, "kind"]);
  if (it.colour) L.push(!a.colour || it.colour === a.colour ? ["ok", cap1(it.colour), "colour"] : ["no", `${cap1(it.colour)} — you asked for ${a.colour}`, "colour"]);
  if (it.waist != null) L.push(!F.waist_size || String(it.waist) === F.waist_size ? ["ok", "Waist " + it.waist, "waist_size"] : ["no", `Waist ${it.waist} — you're a ${F.waist_size}`, "waist_size"]);
  if (it.fit) L.push(!F.prefers_fit || it.fit === F.prefers_fit ? ["ok", cap1(it.fit) + " fit", "prefers_fit"] : ["no", `${cap1(it.fit)} fit — you prefer ${F.prefers_fit}`, "prefers_fit"]);
  if (it.material) {
    const avoid = F.avoids_material, m = avoid && String(it.material).toLowerCase().match(new RegExp("(\\d+)%\\s*" + avoid));
    L.push(m ? ["no", `${m[1]}% ${avoid} — you avoid ${avoid}`, "avoids_material"] : avoid && String(it.material).toLowerCase().includes(avoid) ? ["no", `${cap1(it.material)} — you avoid ${avoid}`, "avoids_material"] : ["ok", cap1(it.material), "avoids_material"]);
  }
  const amt = it.price_pence || 0, max = row.max_pence;
  if (max) L.push(amt <= max ? ["ok", `${gbp(amt)} — under your ${gbp(max).replace(".00", "")} limit`, "max_pence"] : ["no", `${gbp(amt)} — over ${gbp(max).replace(".00", "")}`, "max_pence"]);
  return L;
}
const windowText = h => !h ? "final sale" : h >= 168 ? `${Math.round(h / 24)} days` : `${h} hours`;
const windowShort = h => !h ? "final sale" : h >= 168 ? `${Math.round(h / 24)} days` : `${h}h`;
const isInj = r => (r.p_injection || 0) >= 0.8;
const injText = it => { const d = (it && it.description) || ""; return /SYSTEM NOTE|ignore|instruction/i.test(d) ? d : ""; };

/* plain-English why, derived from the row */
function whyText(r, facts, blockEntry) {
  const k = kind(r), it = item0(r), name = r.merchant_name || r.merchant_id;
  const bad = cartLines(it, r, facts).filter(l => l[0] === "no").map(l => l[1]);
  const lower = s => s.charAt(0).toLowerCase() + s.slice(1);
  const mism = bad.length ? bad.map((b, i) => i ? lower(b) : b).join("; ") : "";
  const match = r.p_match != null ? ` (${pct(r.p_match)} match)` : "";
  if (k === "block") return `${name} was flagged for prompt injection${blockEntry && blockEntry.at ? (/^today/.test(fmtDate(blockEntry.at)) ? " " + fmtDate(blockEntry.at).replace(/^today/, "today at") : " on " + fmtDate(blockEntry.at)) : " earlier"}. Blocked before any check — this cart wasn't even read.`;
  if (r.check_failure) return `The wallet's ${String(r.check_failure).replace(/_/g, " ")} check failed, so it stopped before reading the cart. Nothing was charged.`;
  if (k === "decline" && isInj(r)) return `${bad.length ? mism + ". " : ""}The cart hides an instruction aimed at your agent. We declined and blocked ${name}.`;
  if (k === "decline") return `${mism || "The signed cart doesn't match what you asked for"}${match}. ${r.undo && r.undo.window_hours ? "Nothing was charged." : "Final sale, so nothing was charged."}`;
  const pm = r.p_match != null ? ` (${pct(r.p_match)} match)` : "";
  const look = bad.length ? " Worth a look: " + mism.charAt(0).toLowerCase() + mism.slice(1) + "." : "";
  if (k === "pay" && r.undo && r.undo.window_hours) return `Paid — free to undo for ${windowText(r.undo.window_hours)}, so the wallet didn't need to be sure${pm}.${look || " It matches your request and every signed memory it was checked against."}`;
  if (k === "pay") return `${bad.length ? mism + match + ", but the expected loss was within the £10 the wallet spends on its own" : "Matches your request and every signed memory it was checked against"}. Charged — final sale, no undo.`;
  if (k === "hold") return `Held, not charged — free to undo for ${windowText(r.undo && r.undo.window_hours)}, so the wallet didn't need to be sure${pm}.${look}`;
  if (k === "ask") return `${bad.length ? mism + match + ". " : ""}${r.undo && r.undo.window_hours ? "" : "Final sale: no returns. "}If it's wrong you'd likely lose ${gbp(r.expected_loss_pence)}, over the £10 the wallet spends on its own — so it's your call.`;
  if (k === "done") return `You undid this purchase. Stripe status: ${r.stripe_status}.`;
  return r.rule || "";
}
/* the policy's decision as one plain line — a decline is a decision, not an error */
function policyLine(r) {
  const k = kind(r), w = r.undo && r.undo.window_hours;
  if (k === "block") return "Blocked: merchant flagged for prompt injection";
  if (k === "decline" && r.check_failure) return `Declined: ${String(r.check_failure).replace(/_/g, " ")} check failed`;
  if (k === "decline" && isInj(r)) return `Declined by policy: ${pct(r.p_injection)} injection ≥ 80% · merchant blocked`;
  if (k === "decline") return `Declined by policy: ${w ? "" : "final sale, "}${pct(r.p_match)} match < 25%`;
  if (k === "ask") return `Your call: ${w ? "" : "final sale, "}expected loss ${gbp(r.expected_loss_pence)} > £10`;
  return null;
}
/* paid/held only because undo is free: say so loudly */
function lowMatch(r) {
  const k = kind(r), w = r.undo && r.undo.window_hours;
  if (!["pay", "hold"].includes(k) || !w || r.p_match == null || r.p_match >= 0.8) return null;
  return `${r.p_match < 0.5 ? "Low" : "Partial"} match · ${pct(r.p_match)} — free to undo for ${windowText(w)}`;
}
function rowSub(r, facts) {
  const k = kind(r), it = item0(r), bad = cartLines(it, r, facts).filter(l => l[0] === "no");
  const w = r.undo && r.undo.window_hours;
  if (k === "pay" && bad[0] && w) return `Signed cart: ${bad[0][1]}`;
  if (k === "pay") return w ? `Free cancel for ${windowText(w)}` : "Charged · final sale";
  if (k === "hold") return `On hold · free cancel for ${windowShort(w)}${bad[0] ? " · " + bad[0][1].split(" — ")[0] : ""}`;
  if (k === "ask") return `${w ? "Over the auto limit" : "Final sale"} · ${gbp(r.expected_loss_pence)} at risk`;
  if (k === "block") return "Blocked on past record";
  if (k === "done") return /refund/.test(r.stripe_status) ? "Refunded" : "Cancelled · nothing charged";
  if (isInj(r)) return policyLine(r).replace(" · merchant blocked", "") + " · hidden instruction, merchant blocked";
  if (k === "decline") return policyLine(r) + (bad[0] ? " · " + bad[0][1] : "");
  return r.rule || "";
}

/* ---------------- JSON viewer ---------------- */
function hl(obj) {
  const s = esc(typeof obj === "string" ? obj : JSON.stringify(obj, null, 2));
  return s.replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false)\b|\bnull\b|(-?\d+(?:\.\d+)?)/g, (m, str, colon, bool, num) => {
    if (str) { let inner = colon ? `<span class="jk">${str}</span>${colon}` : `<span class="js">${str}</span>`;
      return inner.replace(/(SYSTEM NOTE:[^&]*)/, '<span class="inj">$1</span>'); }
    if (bool) return `<span class="jb">${m}</span>`; if (m === "null") return `<span class="jz">null</span>`; return `<span class="jn">${m}</span>`; });
}
const jbox = (title, obj, right = "") => `<div class="json"><div class="jh"><span>${title}</span><span>${right}</span></div><pre>${hl(obj ?? null)}</pre></div>`;

/* wallet check trace rows */
/* opts.spans: check spans (name = step) — when present they are the single source for durations
   opts.jevLong: label Jev in full (first mention in a drawer) */
const JEV_LONG = "Jev · TypeSafe's match model", JEV_TIP = "Jev — TypeSafe's match model";
function checkRows(trace, opts = {}) {
  let prev = 0; const sp = opts.spans || [];
  return `<div class="checks">${(trace || []).map(c => { const skip = c.ok == null && /^skipped/i.test(c.detail || ""); let d = Math.max(0, Math.round((c.t_ms || 0) - prev)); prev = c.t_ms || prev;
    const span = sp.find(x => x.name === c.step || x.name === String(c.step).replace(/ /g, "_") || (x.attrs || {}).step === c.step);
    if (span && span.end_ms != null) d = Math.round(span.end_ms - span.start_ms);
    const pol = /^policy$/i.test(c.step);
    const s = pol ? `<span class="${c.ok === true ? "ok-t" : c.ok === false ? "bad-t" : "warn-t"}">→</span>` : c.ok === true ? '<span class="ok-t">✓</span>' : c.ok === false ? '<span class="bad-t">✕</span>' : skip ? '<span class="dim">–</span>' : '<span class="warn-t">!</span>';
    const det = pol ? String(c.detail || "").replace(/^(pay|decline|ask|block):\s*/i, (m, d) => ({pay: "Pay — ", decline: "Declined — ", ask: "Ask you — ", block: "Blocked — "})[d.toLowerCase()]) : c.detail;
    const jev = /^jev$/i.test(c.step), name = jev ? (opts.jevLong ? JEV_LONG : "Jev") : c.step;
    return `<div class="cr ${skip ? "skip" : ""}"><span class="s">${s}</span><span class="nm" ${jev ? `title="${JEV_TIP}"` : ""}>${esc(name)}</span><span class="dt">${esc(pctify(det))}</span><span class="ms">${skip ? "" : d + " ms"}</span></div>`; }).join("")}</div>`;
}
/* "P(match) 0.94" → "94% match", "p < 0.25" → "match < 25%" — one format for scores in UI copy */
function pctify(t) {
  return String(t ?? "")
    .replace(/P\(match\)\s*([<>≤≥]=?)\s*(0?\.\d+|1(?:\.0+)?)/gi, (m, op, v) => `match ${op} ${Math.round(v * 100)}%`)
    .replace(/P\(inj(?:ection)?\)\s*([<>≤≥]=?)\s*(0?\.\d+|1(?:\.0+)?)/gi, (m, op, v) => `injection ${op} ${Math.round(v * 100)}%`)
    .replace(/P\(match\)\s*(0?\.\d+|1(?:\.0+)?)/gi, (m, v) => `${Math.round(v * 100)}% match`)
    .replace(/P\(inj(?:ection)?\)\s*(0?\.\d+|1(?:\.0+)?)/gi, (m, v) => `${Math.round(v * 100)}% injection`)
    .replace(/\bp\s*([<>≤≥]=?)\s*(0?\.\d+)/g, (m, op, v) => `match ${op} ${Math.round(v * 100)}%`)
    .replace(/(\d+%)\s*([<>≤≥]=?)\s*p\s*([<>≤≥]=?)\s*(0?\.\d+)/g, (m, a, op1, op2, v) => `${a} ${op1} match ${op2} ${Math.round(v * 100)}%`)
    .replace(/(0?\.\d+)\s*([<>≤≥]=?)\s*p\b/g, (m, v, op) => `${Math.round(v * 100)}% ${op} match`)
    .replace(/(\d+% (?:match|injection))\s*([<>≤≥]=?)\s*(0?\.\d+|1(?:\.0+)?)(\s*floor)?/g, (m, a, op, v) => `${a} ${op} ${Math.round(v * 100)}%`)
    .replace(/(\d+% match) · \1/g, "$1");
}
/* one date format: "today 19:20" or "15 Oct 2026" */
function fmtDate(d, withTime) {
  if (!d) return ""; const x = new Date(d); if (isNaN(x)) return String(d);
  const t = x.toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit"});
  if (x.toDateString() === new Date().toDateString()) return "today " + t;
  return x.toLocaleDateString("en-GB", {day: "numeric", month: "short", year: "numeric"}).replace("Sept", "Sep") + (withTime ? " " + t : "");
}
const traceTotal = tr => (tr && tr.length) ? Math.round(tr[tr.length - 1].t_ms || 0) : 0;

/* the signed cart, as the wallet reads it */
function cartCard(offer, request) {
  const o = pl(offer), it = (o.items || [])[0] || {}, u = o.undo || {}, a = asked(request);
  const flag = (k, v) => { if (k === "kind" && a.kind && v !== a.kind) return "flag"; if (k === "colour" && a.colour && v !== a.colour) return "flag"; if (k === "material" && /polyester/.test(v)) return "warnf"; return ""; };
  const signer = offer && (offer.signer || offer.key_id) || (o.merchant_id ? "merchant " + o.merchant_id : "merchant");
  const sigOk = offer && offer.signature_ok === false ? false : true;
  return `<div class="cart">${it.image ? `<img src="${esc(it.image)}" alt="">` : `<div></div>`}<div class="cb">
    <div class="li"><span>${esc(it.title || it.kind || "item")}</span><span class="mono">${gbp(o.total_pence || it.price_pence)}</span></div>
    <div class="attrs">${["kind", "colour", "waist", "fit", "material"].filter(k => it[k] != null).map(k => `<span class="${flag(k, String(it[k]))}">${k} ${esc(it[k])}</span>`).join("")}</div>
    ${it.description ? `<div class="mono" style="font-size:13px"><span class="${injText(it) ? "inj" : ""}">${esc(it.description)}</span></div>` : ""}
    <div class="foot"><span>undo: ${u.window_hours ? `free cancel ${windowText(u.window_hours)}` : "final sale — no undo"}</span><span class="${sigOk ? "ok-t" : "bad-t"}">signed by ${esc(signer)} ${sigOk ? "✓" : "✕"}</span></div>
    ${offer && offer.signature ? `<div class="mono dim" style="font-size:12.5px;word-break:break-all">sig ${esc(short(offer.signature, 24, 8))}</div>` : ""}
  </div></div>`;
}
function verdictBig(row) {
  const k = kind(row), amt = amountOf(row);
  return {pay: "Paid " + gbp(amt), hold: "Held " + gbp(amt), ask: "Needs your OK", decline: isInj(row) ? "Declined · merchant blocked" : "Declined", block: "Blocked on past record", done: "Undone", run: "Checking…"}[k];
}
function verdict(row, facts) {
  const k = kind(row), amt = amountOf(row);
  return `<div class="verdict ${k}"><div class="big">${verdictBig(row)}</div><div class="amt">${k === "pay" || k === "hold" ? gbp(amt) : k === "ask" ? gbp(row.expected_loss_pence) + " at risk" : "£0.00"}</div>
    ${lowMatch(row) ? `<div style="margin:8px 0 2px"><span class="lowchip">${esc(lowMatch(row))}</span></div>` : ""}
    <div class="why">${esc(whyText(row, facts))}</div>
    <div class="meta"><span>rule: ${esc(pctify(row.rule))}</span>${row.p_match != null ? `<span>${pct(row.p_match)} match</span>` : ""}${row.p_injection != null ? `<span>${pct(row.p_injection)} injection</span>` : ""}${row.payment_id ? `<span>stripe ${esc(row.payment_id)} · ${esc(row.stripe_status)}</span>` : ""}<span>${esc(row.id)}</span></div></div>`;
}

/* ---------------- top bar (shared) ---------------- */
const TOP = {onRun: null, onReset: null};
function topbar(active) {
  const h = document.createElement("header"); h.className = "top";
  h.innerHTML = `<div class="brand"><div class="mark"><svg viewBox="0 0 20 20"><use href="#pmark"/></svg></div><span class="wm">proto</span> <span class="crumb">/</span> <span class="muted" style="font-weight:500">George Hall</span></div>
  <nav class="seg"><a href="/" class="${active === "wallet" ? "on" : ""}">Wallet <span class="badge" id="wbadge">0</span><span class="kbd">1</span></a><a href="/agents" class="${active === "agents" ? "on" : ""}">Agents <span class="kbd">2</span></a></nav>
  <div class="spacer"></div>
  <div class="clock"><span class="live" id="live"><i></i><span id="livetxt">Idle</span></span><span>T+<b id="clk">00.000</b>s</span></div>
  <button class="btn" id="reset">Reset</button>
  <button class="btn primary" id="play">Run</button>`;
  document.body.prepend(h);
  const sym = document.createElement("div");
  sym.innerHTML = `<svg width="0" height="0" style="position:absolute"><defs><symbol id="pmark" viewBox="0 0 20 20"><path fill="currentColor" d="M2 0h5.2v20H2z"/><path fill="currentColor" d="M9.2 0H11a7 7 0 0 1 0 14H9.2z"/><circle cx="16.2" cy="18" r="2" fill="currentColor"/></symbol></defs></svg>`;
  document.body.prepend(sym.firstChild);
  $("#play").onclick = () => startRun();
  $("#reset").onclick = async () => {
    try { await api("/api/reset", {}); toast("Wallet reset"); } catch (e) { toast("Reset failed: " + e.message); }
    TOP.onReset && TOP.onReset();
  };
  document.addEventListener("keydown", e => {
    if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea/i.test(e.target.tagName)) return;
    if (e.key === "1" && active !== "wallet") location.href = "/";
    if (e.key === "2" && active !== "agents") location.href = "/agents";
  });
}
let RUN_BUSY = false;
async function startRun() {
  if (RUN_BUSY) return; RUN_BUSY = true; $("#play").disabled = true;
  try { const r = await api("/api/run", {}); TOP.onRun && TOP.onRun(r); }
  catch (e) { toast("Run failed: " + e.message); }
  finally { RUN_BUSY = false; $("#play").disabled = false; }
}
function setLive(on, label, ms) {
  $("#live").classList.toggle("on", !!on); $("#livetxt").textContent = label;
  $("#clk").textContent = ((ms || 0) / 1000).toFixed(3).padStart(6, "0");
}
const runElapsed = run => {
  if (!run) return 0;
  const evMax = Math.max(0, ...(run.agents || []).flatMap(a => (a.events || []).map(e => e.t_ms || 0)));
  const running = (run.agents || []).some(a => a.status === "running");
  if (!running) {
    const ends = (run.agents || []).map(a => a.ended_at && run.started_at ? Date.parse(a.ended_at) - Date.parse(run.started_at) : 0);
    return Math.max(evMax, ...ends);
  }
  const t = run.started_at ? Date.now() - Date.parse(run.started_at) : evMax;
  return Math.max(evMax, isFinite(t) ? t : evMax);
};
