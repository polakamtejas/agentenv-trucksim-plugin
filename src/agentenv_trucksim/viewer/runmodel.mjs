// The model of one run, shared by the 3D playthrough (cinema.html) and the trailer (trailer.html): where every truck is
// at any moment, the weather and closures on its roads, each company's books, and the agent's turns as feed lines.
// No DOM and no map: every answer is a function of the run (runs/<id>.json), the road shapes and the moment asked about.

// ---- small helpers ------------------------------------------------------------------------------------------------
export const D2R = Math.PI / 180, M_PER_DEG = 111320;
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const lerp = (a, b, f) => a + (b - a) * f;
export const smooth = x => { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); };
export const smoother = x => { x = clamp(x, 0, 1); return x * x * x * (x * (x * 6 - 15) + 10); };
export const lerpAngle = (a, b, f) => a + ((((b - a) % 360) + 540) % 360 - 180) * f;
export const hash = s => { s = String(s); let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return (h >>> 0) / 4294967296; };
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const money = (v, sign = false) => v == null ? "—" : (v < 0 ? "−" : sign && v > 0 ? "+" : "") + "$" + Math.abs(Math.round(v)).toLocaleString("en-US");
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"];
export const clockOf = t => { const d = Math.floor(t / 96), m = Math.floor((t - d * 96) * 15); return `${DAYS[clamp(d, 0, 7)]} ${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`; };
export const offset = ([lon, lat], east, north) => [lon + east / (M_PER_DEG * Math.cos(lat * D2R)), lat + north / M_PER_DEG];
export const metersBetween = (p, q) => Math.hypot((q[0] - p[0]) * Math.cos((p[1] + q[1]) / 2 * D2R), q[1] - p[1]) * M_PER_DEG;
export const bearingOf = (p, q) => (Math.atan2((q[0] - p[0]) * Math.cos((p[1] + q[1]) / 2 * D2R), q[1] - p[1]) / D2R + 360) % 360;
export const step = (p, brg, m) => offset(p, Math.sin(brg * D2R) * m, Math.cos(brg * D2R) * m);
export async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.json(); }
export const SEAT_RGB = { A: [102, 146, 216], B: [255, 135, 68], C: [76, 190, 140] };   // Kenney's palette strips (scripts/build_models.py)
// A truck's semi is 8.5 model units: a model unit is this many meters at the camera's zoom (the trucks are drawn far
// bigger than life, growing a little as the camera comes down).
export function unitMeters(zoom, lat) {
  const mpp = 40075016.686 * Math.cos(lat * D2R) / (512 * 2 ** zoom), px = clamp(30 * 1.3 ** (zoom - 6), 26, 150);
  return px * mpp / 8.5;
}
// ---- light: Monday 00:00 is midnight; the sun is up about 07:00 to 18:00 ------------------------------------------
export const dayness = tick => { const h = ((tick % 96) + 96) % 96 / 4; return smooth((h - 6.3) / 1.6) * (1 - smooth((h - 17.4) / 1.6)); };

// ---- one run ------------------------------------------------------------------------------------------------------
// id: the run's id (for errors); model: the model's name, if the run's record lacks it; lane(S): how far right of a
// road's centre line a moving truck drives, in meters, when a model unit is S meters.
export function runModel(R, SHAPES, { id, model, lane }) {
  const SEAT = (R.result.model_seats || [])[0];
  if (!SEAT) throw new Error(`run ${id} has no agent seat: the playthrough follows an agent`);
  const AGENT = R.agents[SEAT] || {};
  const MODEL = (AGENT.model || model || "agent").replace(/^anthropic\//, "");
  const SEATS = Object.keys(R.seats).sort();
  const WEEK = R.week.week_ticks, TT = R.week.turn_ticks, NT = Math.round(WEEK / TT);
  const TURN = {}; (AGENT.turns || []).forEach(t => { TURN[t.turn] = t; });
  const MONDAY = new Date(R.week.monday + "T12:00:00Z");
  const dateOf = t => new Date(MONDAY.getTime() + Math.floor(t / 96) * 86400000).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const shortDate = d => new Date(MONDAY.getTime() + d * 86400000).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

  const CITY = Object.fromEntries(R.board.cities.map(c => [c.id, { ...c, p: [c.lon, c.lat] }]));
  const HUB = R.seats[SEAT].hub, HUB_NAME = CITY[HUB].name;
  const TRUCK = Object.fromEntries(R.trucks.map(t => [t.id, t]));
  const UNIT_INDEX = {}; SEATS.forEach(s => R.trucks.filter(t => t.seat === s).forEach((t, i) => { UNIT_INDEX[t.id] = i; }));
  const DRIVER = Object.fromEntries((R.drivers || []).map(d => [d.id, d]));
  const modelOf = t => t.class === "box" ? "box" : t.class === "van" ? "van" : (t.class === "tractor_reefer" || t.equipment === "reefer") ? "reefer" : "semi";
  const UNIT_LABEL = { semi: "semi", reefer: "reefer", box: "box", van: "van" };

  // Roads: the full OSRM shape of each corridor; a truck `mile` of `miles` along it is at that share of the shape's length.
  const SHAPE = Object.fromEntries(SHAPES.edges.map(s => [s.a + ">" + s.b, s.coords]));
  const EDGE = {}, ROADS = [];
  for (const e of R.board.edges) {
    const coords = SHAPE[e.a + ">" + e.b];
    if (!coords) throw new Error(`road_shapes.json has no shape for ${e.a}-${e.b}`);
    const mk = (cs, a, b) => { const cum = [0]; for (let i = 1; i < cs.length; i++) cum.push(cum[i - 1] + metersBetween(cs[i - 1], cs[i])); return { a, b, route: e.route, miles: e.miles, coords: cs, cum, len: cum[cum.length - 1] }; };
    EDGE[e.a + ">" + e.b] = mk(coords, e.a, e.b); EDGE[e.b + ">" + e.a] = mk([...coords].reverse(), e.b, e.a);
    ROADS.push({ coords, route: e.route, interstate: /^I /.test(e.route) });
  }
  const edgeOf = (a, b) => { const E = EDGE[a + ">" + b]; if (!E) throw new Error(`no road ${a}-${b}`); return E; };
  function along(a, b, mile) {
    const E = edgeOf(a, b), target = clamp(mile / E.miles, 0, 1) * E.len;
    let lo = 1, hi = E.cum.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (E.cum[mid] < target) lo = mid + 1; else hi = mid; }
    const p = E.coords[lo - 1], q = E.coords[lo], f = (target - E.cum[lo - 1]) / ((E.cum[lo] - E.cum[lo - 1]) || 1);
    return { p: [lerp(p[0], q[0], f), lerp(p[1], q[1], f)], brg: bearingOf(p, q) };
  }
  function stretch(a, b, m0, m1) {
    const E = edgeOf(a, b), t0 = clamp(m0 / E.miles, 0, 1) * E.len, t1 = clamp(m1 / E.miles, 0, 1) * E.len, pts = [along(a, b, m0).p];
    E.coords.forEach((c, i) => { if (E.cum[i] > t0 && E.cum[i] < t1) pts.push(c); });
    pts.push(along(a, b, m1).p); return pts;
  }
  // Each city's truck lot sits in the widest gap between its roads; seats park in rows, units side by side.
  const LOT = {};
  for (const id in CITY) {
    const brgs = Object.values(EDGE).filter(E => E.a === id).map(E => bearingOf(CITY[id].p, along(E.a, E.b, Math.min(6, E.miles * 0.3)).p)).sort((x, y) => x - y);
    let best = 135, gap = -1;
    brgs.forEach((b, i) => { const n = i + 1 < brgs.length ? brgs[i + 1] : brgs[0] + 360; if (n - b > gap) { gap = n - b; best = (b + (n - b) / 2) % 360; } });
    LOT[id] = best;
  }
  function slot(city, tid, S) {
    const brg = LOT[city], back = 10 + { A: 0, B: 1, C: 2 }[TRUCK[tid].seat] * 11, side = (UNIT_INDEX[tid] - 4.5) * 2.2;
    return { p: step(step(CITY[city].p, brg, back * S), brg + 90, side * S), brg };
  }

  // ---- where a truck is ---------------------------------------------------------------------------------------------
  const SNAP = R.snapshots;
  function snapAt(tid, tick) {
    if (!SNAP.length) throw new Error("this replay has no snapshots");
    if (tick < SNAP[0][0]) return { s0: [TRUCK[tid].hub, null, null, 0, "stopped", "OFF", SNAP[0][1][tid][6]], s1: SNAP[0][1][tid], f: clamp(tick / SNAP[0][0], 0, 1) };
    let i = clamp(Math.floor((tick - SNAP[0][0]) / 4), 0, SNAP.length - 1);
    while (i > 0 && SNAP[i][0] > tick) i--;
    while (i + 1 < SNAP.length && SNAP[i + 1][0] <= tick) i++;
    const n = SNAP[i + 1];
    return { s0: SNAP[i][1][tid], s1: n ? n[1][tid] : null, f: n ? (tick - SNAP[i][0]) / (n[0] - SNAP[i][0]) : 0, t0: SNAP[i][0], t1: n ? n[0] : null };
  }
  // A held truck waits until its closure reopens, then drives on. The snapshots are hourly, so between one that has it
  // held and the next, it stands still until the reopening, not for the whole hour, nor creeping through the closure.
  const REOPEN = {}; for (const e of R.events) if (e.kind === "closure_end") REOPEN[e.closure_id] = e.t;
  const HOLDS = {}; for (const e of R.events) if (e.kind === "held") (HOLDS[e.truck] ||= []).push({ t: e.t, until: REOPEN[e.closure] ?? WEEK });
  function released(tid, t0) {   // when the hold the truck was in at snapshot time t0 ends
    let h = null; for (const x of HOLDS[tid] || []) if (x.t <= t0) h = x;
    if (!h) throw new Error(`run ${id}: ${tid} is held at tick ${t0} with no held event before it`);
    return h.until;
  }
  function logical(tid, tick) {   // {city} or {a, b, mile} between the hourly snapshots, with the earlier snapshot's state
    const at = snapAt(tid, tick), { s0, s1, t0, t1 } = at;
    let f = at.f, state = s0[4];
    if (state === "held" && s1) {
      const r = clamp(released(tid, t0), t0, t1);
      if (tick < r) f = 0; else { f = r < t1 ? (tick - r) / (t1 - r) : 1; state = s1[4]; }
    }
    let w;
    if (s1 && s0[1] && s1[1] && s0[1] === s1[1] && s0[2] === s1[2]) w = { a: s0[1], b: s0[2], mile: lerp(s0[3], s1[3], f) };
    else if (s1 && s0[1] && s1[1] && s0[2] === s1[1]) {        // through a city onto the next road within the hour
      const d1 = edgeOf(s0[1], s0[2]).miles - s0[3], g = f * (d1 + s1[3]);
      w = g < d1 ? { a: s0[1], b: s0[2], mile: s0[3] + g } : { a: s1[1], b: s1[2], mile: g - d1 };
    }
    else if (s1 && s0[0] && s1[1] && s1[1] === s0[0]) w = { a: s1[1], b: s1[2], mile: s1[3] * f };
    else if (s1 && s0[1] && s1[0] && s1[0] === s0[2]) w = { a: s0[1], b: s0[2], mile: lerp(s0[3], edgeOf(s0[1], s0[2]).miles, f) };
    else w = s0[0] ? { city: s0[0] } : { a: s0[1], b: s0[2], mile: s0[3] };
    return { ...w, state, crew: s0[6] || [] };
  }
  function world(tid, tick, S) {   // on the map: parked in its lot, or on the right of its road, easing between the two
    const L = logical(tid, tick);
    if (L.city) { const s = slot(L.city, tid, S); return { p: s.p, brg: s.brg, parked: true, L }; }
    const E = edgeOf(L.a, L.b), here = along(L.a, L.b, L.mile), d0 = clamp(L.mile / E.miles, 0, 1) * E.len, d1 = E.len - d0, Z = 30 * S;
    let p = step(here.p, here.brg + 90, lane(S));
    if (d0 < Z) { const s = slot(L.a, tid, S).p, k = smooth(d0 / Z); p = [lerp(s[0], p[0], k), lerp(s[1], p[1], k)]; }
    else if (d1 < Z) { const s = slot(L.b, tid, S).p, k = smooth(d1 / Z); p = [lerp(s[0], p[0], k), lerp(s[1], p[1], k)]; }
    return { p, brg: here.brg, parked: false, L };
  }
  function pose(tid, tick, S) {
    const w = world(tid, tick, S);
    if (w.parked) return w;
    const a = world(tid, tick - 0.2, S).p, b = world(tid, tick + 0.2, S).p;
    if (metersBetween(a, b) > 0.4 * S) w.brg = bearingOf(a, b);
    return w;
  }

  // Trails: each truck's road-centre position every quarter tick, for the TripsLayer.
  const TRIPS = R.trucks.map(t => {
    const path = [], ts = [];
    for (let k = 0; k <= WEEK; k += 0.25) {
      const L = logical(t.id, k);
      path.push(L.city ? CITY[L.city].p : along(L.a, L.b, L.mile).p); ts.push(k);
    }
    return { id: t.id, seat: t.seat, path, ts };
  });

  // ---- weather and closures ------------------------------------------------------------------------------------------
  const WX = {}, WX_CITY = {}, open = {};
  const addWx = (e, to) => {
    const iv = { from: e.t, to, type: e.type, f: e.speed_factor, where: e.where };
    if (!e.spans) { (WX_CITY[e.city] ||= []).push(iv); return; }          // a v1 replay: weather at a city
    for (const [a, b, m0, m1] of e.spans) {
      const k = `${a}>${b}>${m0}>${m1}`;
      if (!WX[k]) { const path = stretch(a, b, m0, m1); WX[k] = { k, a, b, m0, m1, path, mid: along(a, b, (m0 + m1) / 2).p, km: (m1 - m0) * 1.609, iv: [] }; }
      WX[k].iv.push(iv);
    }
  };
  for (const e of R.events) {
    if (e.kind === "weather_start") open[e.event_id] = e;
    if (e.kind === "weather_end" && open[e.event_id]) { addWx(open[e.event_id], e.t); delete open[e.event_id]; }
  }
  Object.values(open).forEach(e => addWx(e, WEEK));
  const WX_LIST = Object.values(WX);
  const WX_KIND = t => /Winter|Snow|Blizzard|Ice|Sleet|Freezing/.test(t) ? "snow" : /Cold|Chill|Frost/.test(t) ? "cold" : /Rain|Flood|Thunder|Tornado|Tropical|Hurricane|Surge|Lightning|Hail/.test(t) ? "rain" : /Fog/.test(t) ? "fog" : "wind";
  const WX_RGB = { snow: [214, 236, 255], cold: [150, 205, 255], rain: [92, 156, 255], fog: [190, 194, 206], wind: [200, 214, 170] };
  function weatherAt(tick) {
    const out = [];
    for (const w of WX_LIST) {
      let worst = null, fade = 0;
      for (const iv of w.iv) if (iv.from - 2 <= tick && tick < iv.to + 2) {
        fade = Math.max(fade, Math.min(smooth((tick - iv.from + 2) / 4), smooth((iv.to + 2 - tick) / 4)));
        if (!worst || iv.f < worst.f) worst = iv;
      }
      if (worst) out.push({ w, iv: worst, sev: 1 - worst.f, fade, kind: WX_KIND(worst.type) });
    }
    return out;
  }
  const CLOSURES = [], shut = {};
  for (const e of R.events) {
    if (e.kind === "closure_start") shut[e.closure_id] = { ...e, from: e.t };
    if (e.kind === "closure_end" && shut[e.closure_id]) { CLOSURES.push({ ...shut[e.closure_id], to: e.t }); delete shut[e.closure_id]; }
  }
  Object.values(shut).forEach(c => CLOSURES.push({ ...c, to: WEEK }));
  CLOSURES.forEach(c => {
    const miles = edgeOf(c.a, c.b).miles;
    c.path = stretch(c.a, c.b, Math.max(0, c.m0 - 1.5), Math.min(miles, c.m1 + 1.5));
    c.ends = [along(c.a, c.b, c.m0), along(c.a, c.b, c.m1)];
    c.mid = along(c.a, c.b, (c.m0 + c.m1) / 2).p;
    c.label = c.road.split(" ").slice(0, 2).join("-");
  });
  const closuresAt = tick => CLOSURES.filter(c => c.from <= tick && tick < c.to);

  // ---- money --------------------------------------------------------------------------------------------------------
  const REVENUE = new Set(["linehaul", "fuel_surcharge", "detention_pay", "tonu_fee", "terminal_in_transit"]);
  const BOOKS = {};
  for (const s of SEATS) {
    let p = 0, rev = 0, cost = 0; const pts = [[0, 0, 0, 0]];
    R.ledger.filter(l => l.seat === s).sort((a, b) => a.t - b.t).forEach(l => {
      for (const [k, v] of Object.entries(l.items)) { if (REVENUE.has(k)) { p += v; rev += v; } else { p -= v; cost += v; } }
      pts.push([l.t, p, rev, cost]);
    });
    const L = R.seats[s].ledger_usd, rv = [...REVENUE].reduce((a, k) => a + (L[k] || 0), 0);
    pts.push([WEEK, R.seats[s].profit_usd, rv, rv - R.seats[s].profit_usd]);
    BOOKS[s] = pts;
  }
  function booksAt(s, tick) {   // [profit, revenue, costs] at tick, between the turns' ledgers
    const pts = BOOKS[s];
    let i = 1; while (i < pts.length - 1 && pts[i][0] < tick) i++;
    const a = pts[i - 1], b = pts[i], f = clamp((tick - a[0]) / ((b[0] - a[0]) || 1), 0, 1);
    return [lerp(a[1], b[1], f), lerp(a[2], b[2], f), lerp(a[3], b[3], f)];
  }
  const REFS = R.result.references || null, SCORE = (R.result.rewards || {})[SEAT];

  // ---- the agent's turns, as feed lines ------------------------------------------------------------------------------
  const parse = s => { try { return JSON.parse(s); } catch { return null; } };
  const list = v => Array.isArray(v) ? v.join(", ") : (v ?? "");
  function callLine(c) {
    if (c.say != null) return { say: true, fn: c.say };
    const a = c.args || {}, raw = String(c.out ?? ""), o = parse(raw);
    if (raw.startsWith("ERROR")) return { fn: `${c.tool}(${Object.values(a).map(v => typeof v === "object" ? "…" : v).join(", ")})`, res: raw.replace(/^ERROR: (Error executing tool [a-z_]+: )?/, ""), err: true };
    switch (c.tool) {
      case "list_loads": { const n = (raw.match(/"id":"L-/g) || []).length; return { fn: `list_loads(${Object.entries(a).map(([k, v]) => `${k}=${v}`).join(", ")})`, res: o ? `${plural(n, "load")} on the board` : `${n}+ loads on the board` }; }
      case "plan_trip": return { fn: `plan_trip(${a.truck}, ${list(a.load_ids || a.stops)})`, res: o ? `${o.feasible ? "feasible" : "not feasible"} · est ${money(o.est_profit_usd, true)}${(o.late_deliveries || []).length ? ` · ${o.late_deliveries.length} late` : ""}${(o.road_closures || []).length ? " · a closure on the way" : ""}` : "a plan" };
      case "accept_load": return { fn: `accept_load(${a.load_id})`, res: "claimed · settles when every seat ends the turn", act: true };
      case "release_load": return { fn: `release_load(${a.load_id})`, res: o ? `released · −$${o.penalty_usd}` : raw, act: true };
      case "dispatch": return { fn: `dispatch(${(a.assignments || []).map(x => `${x.truck}→${list(x.load_ids || x.stops)}`).join("; ")})`, res: o && o.results ? o.results.map(r => `${r.truck} ${r.ok ? "rolls" : "refused"}`).join(" · ") : "dispatched", act: true };
      case "reposition": return { fn: `reposition(${a.truck} → ${a.city})`, res: o && o.plan ? `arrives ${o.plan.finishes}` : raw, act: true };
      case "assign_drivers": return { fn: `assign_drivers(${a.truck}, ${list(a.driver_ids)})`, res: o && o.crew ? o.crew.map(x => x.name).join(" & ") + " at the wheel" : raw, act: true };
      case "hire_temp_driver": return { fn: `hire_temp_driver(${Object.values(a).join(", ")})`, res: o ? `${o.driver} arrives ${o.arrives} · ${o.rate}` : raw, act: true };
      case "handle_breakdown": return { fn: `handle_breakdown(${a.truck}, ${a.option})`, res: o ? `${o.hours} h · ${money(o.usd)}` : raw, act: true };
      case "schedule_maintenance": return { fn: `schedule_maintenance(${a.truck}, ${a.city})`, res: raw.slice(0, 80), act: true };
      case "end_turn": return { fn: "end_turn(notes)", res: "notes kept for next turn · the world runs 6 hours", act: true };
      case "get_forecast": return { fn: "get_forecast()", res: o ? `${(o.weather_now || []).length} roads hit now · ${(o.weather_next_72h || []).length} in the next 72 h` : "the forecast" };
      case "get_market": return { fn: "get_market()", res: o ? "diesel " + Object.values(o.diesel_usd_per_gal || {}).map(v => "$" + v).join(" / ") : "the market" };
      case "get_truck": return { fn: `get_truck(${a.truck})`, res: o ? `${o.where} · ${o.state}` : raw.slice(0, 60) };
      case "list_drivers": return { fn: "list_drivers()", res: `${(raw.match(/"driver":"/g) || []).length}${o ? "" : "+"} drivers` };
      case "situation": return { fn: "situation()", res: o ? `profit ${money(o.profit_usd, true)} · turn ${o.turn + 1} of ${o.turns_total}` : "the situation" };
      default: return { fn: `${c.tool}(${Object.values(a).map(v => typeof v === "object" ? "…" : v).join(", ")})`, res: raw.slice(0, 70) };
    }
  }
  const FEED = {}; for (const k in TURN) FEED[k] = TURN[k].calls.map(callLine);
  const firstSentences = (s, max = 230) => { const t = String(s || "").replace(/\s+/g, " ").trim(); if (t.length <= max) return t; const cut = t.slice(0, max); const i = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("; ")); return (i > 80 ? cut.slice(0, i + 1) : cut.replace(/\s\S*$/, "")) + " …"; };

  // ---- what happened, for captions and the camera -------------------------------------------------------------------
  const KEY = { breakdown: 9, held: 8, turned_back: 8, detour: 7, missed_pickup: 7, declined: 6, temp_hired: 6, adverse_conditions: 5, crew_change: 5 };
  const LOADS = {}; R.events.filter(e => e.kind === "offer_posted").forEach(e => { LOADS[e.load] = e; });
  const DISPATCHES = R.events.filter(e => e.kind === "dispatch");
  const truckOfLoad = (lid, before) => { let tid = null; for (const e of DISPATCHES) if (e.t <= before && (e.loads || []).includes(lid)) tid = e.truck; return tid; };
  const MINE = R.events.filter(e => e.seat === SEAT);
  const KEY_EVENTS = MINE.filter(e => KEY[e.kind]).map(e => ({ ...e, truck: e.truck || (e.load ? truckOfLoad(e.load, e.t) : null) }));
  const crewNames = ids => ids.map(id => (DRIVER[id] || {}).name || id).join(" & ");
  const route = r => r.replace(/^I /, "I-").replace(/^US /, "US-");
  function eventCaption(e) {
    const t = TRUCK[e.truck], where = e.where ? ` on ${e.where.replace(/^I (\d+)/, "I-$1")}` : "";
    switch (e.kind) {
      case "breakdown": { const fix = R.events.find(x => x.kind === "repair_ordered" && x.truck === e.truck && Math.abs(x.t - e.t) <= 2); return [`${e.truck} breaks down`, `Its ${e.component} failed${where}.${fix ? ` Repair: ${fix.option}, ${money(fix.usd)}, paid by ${fix.paid_by || "the company"}.` : ""}`]; }
      case "detour": return [`${e.truck} detours around a closed road`, `The road ahead closed after it left: it goes via ${(e.via || []).map(c => CITY[c] ? CITY[c].name : c).join(" → ")} instead of the direct way.`];
      case "turned_back": return [`${e.truck} turns back`, `A closure blocks the road ahead; the driver turns around and takes ${(e.via || []).map(c => CITY[c] ? CITY[c].name : c).join(" → ")}.`];
      case "adverse_conditions": return ["Bad-weather hours", `${(DRIVER[e.driver] || {}).name || e.driver} on ${e.truck} uses the adverse-conditions exception (49 CFR 395.1(b)): up to 2 more hours of driving, because of a ${e.why}.`];
      case "missed_pickup": return ["A missed pickup", `No truck reached ${e.load}${LOADS[e.load] ? ` (${CITY[LOADS[e.load].orig].name} → ${CITY[LOADS[e.load].dest].name})` : ""} in its window: ${money(-e.penalty)}.`];
      case "crew_change": return [`Slip-seat on ${e.truck}`, `${e.truck} goes back out with ${crewNames(e.crew || [])} at the wheel.`];
      case "temp_hired": return ["A temp driver", `The agent hires a temp driver for ${e.days || 1} day(s).`];
      case "held": return [`${e.truck} held by a closure`, `${route(e.road)} is closed; it waits, announced to reopen about ${clockOf(e.reopens_about)}.`];
      case "declined": return ["An owner-operator says no", `${e.truck}'s owner-operator turns the trip down: it doesn't pay their rate.`];
      default: return [e.kind, ""];
    }
  }
  function truckBlurb(tid, tick) {
    const t = TRUCK[tid], L = logical(tid, tick), d = DISPATCHES.filter(e => e.truck === tid && e.t <= tick).pop();
    const load = d && (d.loads || []).find(l => !R.events.some(x => x.kind === "delivered" && x.load === l && x.t <= tick));
    const o = load && LOADS[load];
    const unit = { semi: "tractor + 53' dry van", reefer: "tractor + 53' reefer", box: "26' box truck", van: "cargo van" }[modelOf(t)];
    const job = o ? `On ${load}: ${CITY[o.orig].name} → ${CITY[o.dest].name}${o.expedite ? ", expedited" : ""}.` : L.state === "empty" ? "Running empty to its next pickup." : "";
    return [`${tid}${L.city ? ` at ${CITY[L.city].name}` : ` on ${route(edgeOf(L.a, L.b).route)} → ${CITY[L.b].name}`}`, `${unit}${t.owner === "owner_operator" ? " (an owner-operator's)" : ""} · ${crewNames(L.crew) || "no driver"}. ${job}`];
  }
  return { SEAT, AGENT, MODEL, SEATS, WEEK, TT, NT, TURN, MONDAY, dateOf, shortDate, CITY, HUB, HUB_NAME, TRUCK, UNIT_INDEX, DRIVER, modelOf, UNIT_LABEL, EDGE, ROADS, edgeOf, along, stretch, LOT, slot, SNAP, snapAt, logical, world, pose, TRIPS, WX, WX_CITY, WX_LIST, WX_KIND, WX_RGB, weatherAt, CLOSURES, closuresAt, REVENUE, BOOKS, booksAt, REFS, SCORE, callLine, FEED, firstSentences, KEY, LOADS, DISPATCHES, truckOfLoad, MINE, KEY_EVENTS, crewNames, route, eventCaption, truckBlurb };
}
