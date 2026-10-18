// The console's view-model (LOG-120): a replay snapshot (app/floor/replay.js) in, the vitals, the
// station rows, the attention cards and the event log out. Pure apart from the attention memory.
// The future overhaul API serves the same model, so the panels do not change when the data is live.
//
// Attention rules (ideas.md section 22, LOG-121):
//   - a value with a display band that stays outside it for 10 s: an item (warning; alarm when it
//     is more than one band width out)
//   - a station in a fault or warning class: an item
//   - the verdict's root cause (alarm) and affected stations (warning): an item each
//   - an alarm item blinks until the operator acknowledges it (ISA-18.2 practice); warnings need no
//     acknowledgment
//   - an item that is no longer active shows "recovering" for HOLD_S after its last deviation, then
//     it leaves. An acknowledged item leaves at once. There is no close button: an item leaves by
//     its state. The event log keeps the record.
import { classify, derived } from "../../floor/states.js";

export const HOLD_S = 120;          // project choice: 2 min of plant time in the recovering state

export const AREAS = [
  { id: "back", name: "Frame line, side members", stations: ["pierce-1", "lmp-1", "blast-1"] },
  { id: "cross", name: "Cross member cell", stations: ["plasma-1", "press-1", "press-2", "weld-1", "weld-2"] },
  { id: "ced", name: "CED paint line", stations: ["ced-1"] },
  { id: "front", name: "Frame assembly", stations: ["rivet-1", "qa-scanner-1", "conveyor-1"] },
  { id: "cranes", name: "Cranes", stations: ["crane-1", "crane-2"] },
  { id: "engine", name: "Engine line", stations: ["washer-1", "cool-station", "leak-1", "pallet-1", "tight-1", "tight-2", "cold-1", "hot-1"] },
  { id: "util", name: "Utilities", stations: ["compressor-1", "cool-1", "chiller-1", "gas-1", "tr-1", "tr-2", "incomer-1"] },
];

// The vital value of each station; `band: true` gives it a display band and an attention rule.
export const VITAL = {
  "pierce-1": { tag: "pierce-1.pressure_bar", label: "hydraulics", band: true },
  "lmp-1": { tag: "lmp-1.oil_c", label: "oil", band: true },
  "blast-1": { tag: "blast-1.filter_dp_pa", label: "filter Δp", band: true },
  "plasma-1": { tag: "plasma-1.arc_a", label: "arc" },
  "press-1": { tag: "press-1.oil_c", label: "oil", band: true },
  "press-2": { tag: "press-2.flywheel_rpm", label: "flywheel", band: true },
  "weld-1": { tag: "weld-1.weld_a", label: "weld current" },
  "weld-2": { tag: "weld-2.weld_a", label: "weld current" },
  "ced-1": { tag: "ced-1.bath_c", label: "bath", band: true },
  "rivet-1": { tag: "rivet-1.last_install_kn", label: "last rivet" },
  "qa-scanner-1": { tag: "qa-scanner-1.power_w", label: "power" },
  "conveyor-1": { tag: "conveyor-1.speed_mps", label: "speed" },
  "crane-1": { tag: "crane-1.lift_t", label: "load" },
  "crane-2": { tag: "crane-2.lift_t", label: "load" },
  "washer-1": { tag: "washer-1.tank_c", label: "tank", band: true },
  "leak-1": { tag: "leak-1.last_decay_pa", label: "last decay" },
  "pallet-1": { tag: "pallet-1.power_kw", label: "power" },
  "tight-1": { tag: "tight-1.last_torque_nm", label: "last torque" },
  "tight-2": { tag: "tight-2.last_torque_nm", label: "last torque" },
  "cold-1": { tag: "cold-1.torque_nm", label: "torque" },
  "hot-1": { tag: "hot-1.load_pct", label: "load" },
  "compressor-1": { tag: "compressor-1.pressure_barg", label: "air header", band: true },
  "cool-1": { tag: "cool-1.supply_c", label: "supply", band: true },
  "chiller-1": { tag: "chiller-1.lwt_c", label: "leaving water", band: true },
  "gas-1": { tag: "gas-1.header_kpa", label: "gas header", band: true },
  "tr-1": { tag: "tr-1.load_pct", label: "load" },
  "tr-2": { tag: "tr-2.load_pct", label: "load" },
  "incomer-1": { tag: "incomer-1.p_kw", label: "site power" },
};
export const BAND_TAGS = [...Object.values(VITAL).filter((v) => v.band).map((v) => v.tag), "psu-a.voltage_v", "psu-b.voltage_v", "psu-c.voltage_v"];
const BAND_MIN = { _c: 2, _bar: 0.4, _barg: 0.3, _pa: 60, _rpm: 10, _kpa: 0.5, _v: 6 };

export function unitOf(tag) {
  const k = tag.split(".")[1] || "";
  const U = [["_kw", "kW"], ["_w", "W"], ["_barg", "barg"], ["_bar", "bar"], ["_kpa", "kPa"], ["_pa", "Pa"], ["_c", "°C"], ["_rpm", "rpm"],
    ["_pct", "%"], ["_nm", "N m"], ["_kn", "kN"], ["_mps", "m/s"], ["_m3h", "m³/h"], ["_a", "A"], ["_v", "V"], ["_t", "t"], ["_kg_min", "kg/min"]];
  for (const [s, u] of U) if (k.endsWith(s)) return u;
  return "";
}
export function fmt(v, tag) {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  const d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return Number(v).toFixed(tag?.endsWith(".pf") ? 2 : d);
}
export const label = (tag) => (tag.split(".")[1] || tag).replace(/_(kw|w|barg|bar|kpa|pa|c|rpm|pct|nm|kn|mps|m3h|a|v|t|kg_min)$/, "").replace(/_/g, " ");
// The name of a value in a sentence: the station's vital name when the tag is one ("air header").
export const vitalLabel = (tag) => Object.values(VITAL).find((x) => x.tag === tag)?.label || label(tag);
export const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

// ── the mock verdict (prototype): an air header loss at compressor-1 that starves plasma-1 ──────
export const SCENARIO = { onset: 3600, ramp: 300, recover: 7200, back: 120, warnAfter: 240, rootAfter: 90, drop: 1.3 };
export function airDrop(tRel) {
  const s = SCENARIO;
  if (tRel < s.onset) return 0;
  if (tRel < s.onset + s.ramp) return s.drop * (tRel - s.onset) / s.ramp;
  if (tRel < s.recover) return s.drop;
  if (tRel < s.recover + s.back) return s.drop * (1 - (tRel - s.recover) / s.back);
  return 0;
}
export function mockVerdict(tRel, on) {
  const s = SCENARIO;
  if (!on || tRel < s.onset + s.rootAfter) return { state: "steady", since: null };
  const opened = s.onset + s.rootAfter;
  const recovering = tRel >= s.recover;
  return {
    state: recovering ? (tRel < s.recover + s.back + 600 ? "recovering" : "steady") : "incident",
    opened, root: "compressor-1", medium: "air header", affected: tRel >= s.onset + s.warnAfter ? ["plasma-1"] : [],
    strength: Math.min(0.82, 0.4 + 0.42 * Math.min(1, (tRel - opened) / 300)),
    text: "The air header pressure fell at compressor-1 while it stayed loaded. plasma-1 reads low air at its inlet.",
    recoverText: "The air header pressure is back in its normal band.",
    todo: "Walk the air header from compressor-1: look for a large leak or an open drain. plasma-1 can wait at load.",
    rootWhat: "Air header pressure fell while it stayed loaded",
    affectedWhat: "Low air at its inlet, from the air header",
  };
}
export function applyOverlay(snap, tRel, on) {
  if (!on) return snap;
  const d = airDrop(tRel);
  if (!d && tRel < SCENARIO.onset) return snap;
  const values = { ...snap.values };
  if (values["compressor-1.pressure_barg"] != null) values["compressor-1.pressure_barg"] -= d;
  const override = {};
  const s = SCENARIO;
  if (tRel >= s.onset + s.warnAfter && tRel < s.recover + 60) override["plasma-1"] = { cls: "warn", text: "air pressure low" };
  return { ...snap, values, override };
}

// ── display bands from the recording: a wide percentile band, not an engine verdict ─────────────
export function makeBands(rep) {
  const out = {};
  for (const tag of BAND_TAGS) {
    const k = rep.vTags.indexOf(tag);
    if (k < 0) continue;
    const xs = rep.rows.map((r) => r[k]).filter((x) => x != null).sort((a, b) => a - b);
    if (!xs.length) continue;
    const q = (p) => xs[Math.min(xs.length - 1, Math.max(0, Math.round(p * (xs.length - 1))))];
    let lo = q(0.005), hi = q(0.995);
    const suf = Object.keys(BAND_MIN).find((s) => tag.split(".")[1].endsWith(s));
    const w0 = Math.max(hi - lo, suf ? BAND_MIN[suf] : 0);
    const pad = 0.25 * w0;
    lo = (lo + hi) / 2 - w0 / 2 - pad; hi = lo + w0 + 2 * pad;
    out[tag] = { lo, hi, min: lo - (hi - lo) * 0.75, max: hi + (hi - lo) * 0.75 };
  }
  return out;
}
export function bandLevel(b, v) {
  if (!b || v == null) return null;
  if (v >= b.lo && v <= b.hi) return null;
  const out = v < b.lo ? b.lo - v : v - b.hi;
  return out > (b.hi - b.lo) ? "alarm" : "warn";
}

// ── series for sparklines and trends (2 s rows, the overlay applied) ─────────────────────────────
export function series(rep, tag, t, windowS, mock) {
  const k = rep.vTags.indexOf(tag); if (k < 0) return [];
  const i1 = Math.floor((t - rep.vT0) / rep.vDt), i0 = Math.max(0, i1 - Math.round(windowS / rep.vDt));
  const out = [];
  for (let i = i0; i <= Math.min(i1, rep.rows.length - 1); i++) {
    let v = rep.rows[i][k];
    if (mock && tag === "compressor-1.pressure_barg" && v != null) v -= airDrop(rep.vT0 + i * rep.vDt - rep.t0);
    out.push(v);
  }
  return out;
}

// ── the event log, computed once for the whole recording ─────────────────────────────────────────
const LOG_SETUP = { "pierce-1": "change", "plasma-1": "change" };
export function buildEventLog(rep, mock) {
  const L = [];
  for (const [name, arr] of Object.entries(rep.st)) {
    let prev = null;
    for (const [t, word] of arr) {
      const c = classify(name, word);
      if ((c.cls === "fault" || c.cls === "warn") && prev !== c.cls) L.push({ t, sev: c.cls === "fault" ? "alarm" : "warn", who: name, text: c.text });
      else if (prev === "fault" && c.cls !== "fault") L.push({ t, sev: "info", who: name, text: "fault cleared" });
      if (LOG_SETUP[name] === word) L.push({ t, sev: "info", who: name, text: c.text });
      prev = c.cls;
    }
  }
  for (const [t, kind, id, src, dst, info] of rep.events) {
    if (kind === "ship") L.push({ t, sev: "info", who: id, text: id.startsWith("FR") ? "frame shipped" : "engine shipped" });
    else if (kind === "new" && id.startsWith("L")) L.push({ t, sev: "info", who: id, text: `CED load hung on carrier ${info?.carrier}` });
    else if (kind === "new" && rep.items[id]?.type === "frame") L.push({ t, sev: "info", who: id, text: "frame riveted" });
    else if (kind === "move" && dst === "leak-1" && info?.ok === false) L.push({ t, sev: "warn", who: id, text: `leak test failed, ${info.decay_pa} Pa` });
  }
  if (mock) {
    const s = SCENARIO, t0 = rep.t0;
    L.push({ t: t0 + s.onset + s.rootAfter, sev: "alarm", who: "compressor-1", text: "root cause: air header pressure low" });
    L.push({ t: t0 + s.onset + s.warnAfter, sev: "warn", who: "plasma-1", text: "air pressure low at the inlet" });
    L.push({ t: t0 + s.recover + s.back, sev: "info", who: "compressor-1", text: "air header pressure back in band" });
  }
  return L.sort((a, b) => a.t - b.t);
}

// ── the model ────────────────────────────────────────────────────────────────────────────────────
export function createModel(rep, { mock = true } = {}) {
  const bands = makeBands(rep);
  const log = buildEventLog(rep, mock);
  const mem = { out: {}, cards: {} };

  function station(name, snap, roles) {
    const s = snap.states[name];
    const ov = snap.override?.[name];
    const c = ov || (s?.word != null ? classify(name, s.word) : derived(name, snap.values)) || classify(name, null);
    const vit = VITAL[name];
    const v = vit ? snap.values[vit.tag] : null;
    const band = vit?.band ? bands[vit.tag] : null;
    const bl = bandLevel(band, v);
    const kw = snap.values[`${name}.power_kw`] ?? (snap.values[`${name}.power_w`] != null ? snap.values[`${name}.power_w`] / 1000 : null);
    const role = roles[name] || null;
    const level = role === "root" || c.cls === "fault" ? "alarm" : role === "affected" || c.cls === "warn" || bl ? (bl === "alarm" ? "alarm" : "warn") : null;
    const glyph = level === "alarm" ? "alarm" : level === "warn" ? "warn" : c.cls === "active" ? "ok" : c.cls === "setup" ? "busy" : "idle";
    return { name, cls: c.cls, text: c.text, word: s?.word ?? null, since: s?.since ?? null, vital: vit ? { ...vit, value: v, unit: unitOf(vit.tag), band, level: bl } : null, kw, role, level, glyph };
  }

  return {
    bands, log,
    step(snap, { acked = new Set() } = {}) {
      const t = snap.t, tRel = t - rep.t0;
      const verdict = mockVerdict(tRel, mock);
      const roles = {};
      if (verdict.state === "incident") { roles[verdict.root] = "root"; for (const a of verdict.affected) roles[a] = "affected"; }
      const stations = {};
      for (const a of AREAS) for (const n of a.stations) stations[n] = station(n, snap, roles);
      // attention: active causes per station
      const active = {};
      const add = (st, level, reason, tags) => {
        const c = active[st] || (active[st] = { station: st, level: null, reasons: [], tags: new Set() });
        if (level === "alarm" || !c.level) c.level = level;
        c.reasons.push(reason); for (const x of tags || []) c.tags.add(x);
      };
      // the verdict first, so that its sentence leads the item
      if (verdict.state === "incident") {
        add(verdict.root, "alarm", verdict.rootWhat, [VITAL[verdict.root]?.tag].filter(Boolean));
        for (const a of verdict.affected) add(a, "warn", verdict.affectedWhat, [VITAL[verdict.root]?.tag].filter(Boolean));
      }
      for (const tag of BAND_TAGS) {
        const lvl = bandLevel(bands[tag], snap.values[tag]);
        if (lvl) { mem.out[tag] ??= t; if (t - mem.out[tag] >= 10) add(tag.split(".")[0].replace(/^psu-.*/, "tr-1"), lvl, `${cap(vitalLabel(tag))} outside its normal band`, [tag]); }
        else delete mem.out[tag];
      }
      for (const st of Object.values(stations)) if (st.cls === "fault" || st.cls === "warn") add(st.name, st.cls === "fault" ? "alarm" : "warn", cap(st.text), st.vital?.band ? [st.vital.tag] : []);
      // items: active ones, then the ones recovering; a leaving item shows once more (its fade-out)
      for (const [st, c] of Object.entries(active)) {
        const prev = mem.cards[st];
        mem.cards[st] = { ...c, tags: [...c.tags], since: prev && prev.state === "active" ? prev.since : t, last: t, state: "active", root: roles[st] === "root" };
      }
      for (const [st, c] of Object.entries(mem.cards)) {
        if (active[st]) continue;
        if (c.leaving || t < c.since) { delete mem.cards[st]; continue; }
        c.state = "recover"; c.level = "recover"; c.root = false;
        if (t - c.last > HOLD_S || acked.has(`${st}@${c.since}`)) c.leaving = true;
      }
      const rank = (c) => (c.state === "active" ? 0 : 2) + (c.root ? -1 : 0) + (c.level === "alarm" ? 0 : 0.5);
      const cards = Object.values(mem.cards).map((c) => {
        const key = `${c.station}@${c.since}`, ack = acked.has(key);
        return { ...c, key, acked: ack, blink: c.level === "alarm" && c.state === "active" && !ack, needsAck: c.level === "alarm" && !ack };
      }).sort((a, b) => rank(a) - rank(b) || b.since - a.since);
      // plant and area vitals
      const v = snap.values;
      const frames = new Set(snap.items.filter((i) => i.id.startsWith("FR")).map((i) => i.id.slice(0, 9)));
      const engines = snap.items.filter((i) => i.type === "engine").length;
      const kits = snap.items.filter((i) => i.type === "kit" || i.type === "cross member").length;
      const cedLoad = snap.items.find((i) => i.type === "CED load" && i.place?.startsWith("ced-1:") && i.place !== "ced-1:in" && i.place !== "ced-1:out");
      const assembly = snap.items.filter((i) => ["rivet-1", "qa-scanner-1", "conveyor-1"].includes(i.place) || i.place === "rivet-1:out").length;
      const onHook = snap.items.filter((i) => i.place?.startsWith("crane-")).map((i) => i.id);
      const plant = [
        { k: "site", label: "Site power", value: v["incomer-1.p_kw"], unit: "kW", tag: "incomer-1.p_kw" },
        { k: "pf", label: "PF", value: v["incomer-1.pf"], unit: "", tag: "incomer-1.pf" },
        { k: "air", label: "Air header", value: v["compressor-1.pressure_barg"], unit: "barg", tag: "compressor-1.pressure_barg", level: stations["compressor-1"].vital?.level },
        { k: "water", label: "Cooling supply", value: v["cool-1.supply_c"], unit: "°C", tag: "cool-1.supply_c", level: stations["cool-1"].vital?.level },
        { k: "frames", label: "Frames shipped", value: snap.shipped.frames, unit: "" },
        { k: "engines", label: "Engines shipped", value: snap.shipped.engines, unit: "" },
      ];
      const areaV = {
        back: [{ label: "frames in line", value: frames.size }, { label: "shipped", value: snap.shipped.frames }],
        cross: [{ label: "kits and members", value: kits }],
        ced: [{ label: "bath", value: v["ced-1.bath_c"], unit: "°C" }, { label: "load", text: cedLoad ? cedLoad.place.slice(6).replace("hoist>", "to ") : "none" }],
        front: [{ label: "in assembly", value: assembly }],
        cranes: [{ label: "on the hook", text: onHook.length ? onHook.join(", ") : "none" }],
        engine: [{ label: "on the line", value: engines }, { label: "shipped", value: snap.shipped.engines }],
        util: [{ label: "air", value: v["compressor-1.pressure_barg"], unit: "barg" }, { label: "site", value: v["incomer-1.p_kw"], unit: "kW" }],
      };
      const areas = AREAS.map((a) => {
        const sts = a.stations.map((n) => stations[n]);
        const level = sts.some((s) => s.level === "alarm") ? "alarm" : sts.some((s) => s.level === "warn") ? "warn" : null;
        return { ...a, list: sts, level, vitals: areaV[a.id] || [], abnormal: sts.filter((s) => s.level) };
      });
      const i = log.findIndex((e) => e.t > t);
      const events = (i < 0 ? log : log.slice(0, i));
      // tracked IDs per station, its buffers included ("pierce-1:in" counts for pierce-1)
      const at = {};
      for (const it of snap.items) { const st = (it.place || "").split(":")[0]; if (st) at[st] = (at[st] || 0) + 1; }
      return { t, tRel, verdict, roles, stations, areas, plant, cards, events, at, values: v, bands, focus: verdict.state === "incident" ? [verdict.root, ...verdict.affected] : [] };
    },
  };
}
