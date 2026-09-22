// Dev-only sample data so `next dev` renders a populated console for design review. The production
// static export NEVER uses it (api.js checks NODE_ENV). The deployed console shows live engine data
// only, and shows empty and steady states honestly when the engine is quiet or down.
//
// Review states, picked with ?mock= in the dev preview (SCENARIOS.md 7):
//   (none)     PS1 incident: root press-1, blast radius, an Execute proposal
//   steady     calm plant: no root, no faults, a full act-loop story in the event log
//   forecast   PS5 coolant ramp: no root yet, trip ETAs from the engine's incipient findings
//   chain      PS2: compressor-1 -> rail psu-b -> chiller-1 (overload trip) -> loop cool-1 -> machines
//   network    PS3: hmi-gw floods segment field-1, the stamping cell link lags and drops
//   integrity  PS4A: a setpoint changed with no signed ledger row, and the act loop is blocked
//   blind      PS6: the tag server leaks toward its limit, then the SCADA view goes blind
const INCIDENT = {
  "/api/health": { ok: true, services: { aggregator: "up", engine: "up" }, auth: "enforced" },
  "/api/graph": {
    root: [{ pod: "press-1", score: 0.62, onset_s: 41 }],
    edges: [
      // floor plane: the PS1 rail-A story (design-review data for the Floor.jsx causal overlay)
      { src: "press-1", dst: "psu-a", r: 0.87, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.92, state: "active", render_weight: 0.92 },
      { src: "press-1", dst: "cnc-1", r: 0.83, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.9, state: "active", render_weight: 0.9 },
      { src: "press-1", dst: "qa-scanner-1", r: 0.61, evidence: ["stat", "rail"], signal: "bus_voltage", confidence: 0.66, state: "active", render_weight: 0.66 },
      { src: "press-1", dst: "cnc-1", r: 0.58, evidence: ["stat", "loop"], signal: "coolant_temp", confidence: 0.6, state: "active", render_weight: 0.6 },
      // edge plane: one pod-plane edge so the EDGE toggle stays reviewable
      { src: "tag-server", dst: "historian-db", r: 0.81, evidence: ["write", "ebpf", "temporal"], signal: "psi_io", confidence: 0.92, state: "active" },
    ],
    blast_radius: [{ pod: "cnc-1", impact: 0.7, eta_s: 30 }, { pod: "qa-scanner-1", impact: 0.55, eta_s: 60 }, { pod: "psu-a", impact: 0.7, eta_s: 0 }, { pod: "historian-db", impact: 0.3, eta_s: 90 }],
    findings: [{ pod: "press-1", class: "leak", onset_s: 30, severity: 0.8 }],
    incipient: [],
    meta: { pods: 16, active: 1, accepted_edges: 5, signal: "bus_voltage" },
  },
  "/api/narrative": { text: "press-1 is the likely root of the rail-A voltage sag; cnc-1 and qa-scanner-1 degrade with it. Recommend derating press-1.", source: "llm", model: "gemma4:e4b-it-qat" },
  "/api/topology": { edges: [{ src: "tag-server", dst: "historian-db", port: 5432 }], source: "caretta" },
  "/api/pods": [
    { workload: "tag-server", namespace: "plant", signal: "psi_io", value: 0.94, anomalous: true },
    { workload: "historian-db", namespace: "plant", signal: "psi_io", value: 0.61, anomalous: true },
    { workload: "plant-sim", namespace: "plant", signal: "psi_io", value: 0.20, anomalous: false },
    { workload: "openplc", namespace: "plant", signal: "psi_io", value: 0.05, anomalous: false },
    { workload: "correlation-engine", namespace: "aiops", signal: "psi_io", value: 0.03, anomalous: false },
    { workload: "api", namespace: "aiops", signal: "psi_io", value: 0.07, anomalous: false },
  ],
  "/api/pod-resources": {
    source: "prometheus", pods: [
      { namespace: "plant", pod: "tag-server-x", workload: "tag-server", cpu: { usage: 0.42, request: 0.5, limit: 1.0 }, mem: { usage: 640e6, request: 512e6, limit: 768e6 } },
      { namespace: "plant", pod: "historian-db-0", workload: "historian-db", cpu: { usage: 0.18, request: 0.25, limit: 0.5 }, mem: { usage: 537e6, request: 512e6, limit: 640e6 } },
      { namespace: "plant", pod: "plant-sim-x", workload: "plant-sim", cpu: { usage: 0.11, request: 0.1, limit: 0.25 }, mem: { usage: 252e6, request: 256e6, limit: 320e6 } },
      { namespace: "plant", pod: "openplc-x", workload: "openplc", cpu: { usage: 0.26, request: 0.25, limit: 0.5 }, mem: { usage: 126e6, request: 128e6, limit: 192e6 } },
      { namespace: "aiops", pod: "correlation-engine-x", workload: "correlation-engine", cpu: { usage: 0.08, request: 0.1, limit: 0.25 }, mem: { usage: 115e6, request: 128e6, limit: 192e6 } },
      { namespace: "aiops", pod: "api-x", workload: "api", cpu: { usage: 0.51, request: 0.5, limit: 1.0 }, mem: { usage: 252e6, request: 256e6, limit: 512e6 } },
    ],
  },
  "/api/plant": {
    source: "sim",
    rails: { "psu-a": { volts: 346.3, v_src: 400.0 }, "psu-b": { volts: 372.8, v_src: 400.0 }, "psu-c": { volts: 388.6, v_src: 400.0 } },
    loop: { name: "cool-1", flow: 118.2, flow_nominal: 120.0, pump_health: 1.0 },
    trip_c: 78.0,
    cells: {
      stamping: { plc: "plc-stamping", rail: "psu-a", connected: true, mode: "closed-loop", fail_open: true, machines: ["press-1", "press-2"] },
      "pack-1": { plc: "plc-pack-1", rail: "psu-c", connected: true, mode: "closed-loop", fail_open: false, machines: ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1"] },
    },
    devices: {
      "press-1":      { amps: 79.6, temp: 66.1, throughput: 99.2, rail: "psu-a", cooled: true, cell: "stamping", controller: "plc-stamping", commanded: { run: true, speed_pct: 100 }, speed_pct: 100 },
      "press-2":      { amps: 37.8, temp: 55.9, throughput: 98.7, rail: "psu-a", cooled: true, cell: "stamping", controller: "plc-stamping", commanded: { run: true, speed_pct: 100 }, speed_pct: 100 },
      "pack-conveyor-1": { amps: 8.9, temp: null, throughput: 100, rail: "psu-c", cooled: false, cell: "pack-1", controller: "plc-pack-1", commanded: { run: true, speed_pct: 100 }, speed_pct: 100 },
      "pack-wrapper-1":  { amps: 13.6, temp: 41.2, throughput: 100, rail: "psu-c", cooled: true, cell: "pack-1", controller: "plc-pack-1", commanded: { run: true, speed_pct: 100 }, speed_pct: 100 },
      "pack-labeler-1":  { amps: 3.9, temp: null, throughput: 97.1, rail: "psu-c", cooled: false, cell: "pack-1", controller: "plc-pack-1", commanded: { run: true, speed_pct: 100 }, speed_pct: 100 },
      "cnc-1":        { amps: 28.7, temp: 50.6, throughput: 84.4, rail: "psu-a", cooled: true },
      "qa-scanner-1": { amps: 7.2,  temp: null, throughput: 85.8, rail: "psu-a", cooled: false },
      "conveyor-1":   { amps: 18.2, temp: null, throughput: 100,  rail: "psu-b", cooled: false },
      "compressor-1": { amps: 6.7,  temp: null, throughput: 100,  rail: "psu-b", cooled: false },
      "furnace-1":    { amps: 30.4, temp: 65.3, throughput: 100,  rail: "psu-b", cooled: true },
      "chiller-1":    { amps: 22.0, temp: null, throughput: 100,  rail: "psu-b", cooled: false },
    },
    active_faults: ["PS1"],
  },
  // 2F.2 tag browser: built programmatically so the mock stays in lockstep with the shape
  // /api/tags serves (scada/tags.py tag_table + live values).
  "/api/tags": (() => {
    const A = { "press-1": ["psu-a", 79.6, 66.1, 99.2], "press-2": ["psu-a", 37.8, 55.9, 98.7],
      "cnc-1": ["psu-a", 28.7, 50.6, 84.4], "qa-scanner-1": ["psu-a", 7.2, null, 85.8],
      "conveyor-1": ["psu-b", 18.2, null, 100], "compressor-1": ["psu-b", 6.7, null, 100],
      "furnace-1": ["psu-b", 30.4, 65.3, 100], "chiller-1": ["psu-b", 22.0, null, 100] };
    const HK = { "press-1": 0.55, "press-2": 0.55, "cnc-1": 0.55, "furnace-1": 1.0 };
    const RV = { "psu-a": 346.3, "psu-b": 372.8 };
    const T = (a, s) => `PLANT.${a.toUpperCase().replace(/-/g, "_")}.${s}`;
    const rows = [];
    const add = (asset, signal, unit, kind, address, value, i) =>
      rows.push({ tag: T(asset, signal), asset, signal, unit, kind, address, value,
                  quality: value == null ? "BAD" : (i % 9 === 7 ? "STALE" : "GOOD"), ts: 1752741600 });
    let i = 0; const cooled = Object.keys(HK);
    cooled.forEach((m, k) => add(m, "TEMP", "degC", "measured", `%MW${k}`, A[m][2], i++));
    add("cool-1", "FLOW", "L/min", "measured", "%MW4", 118.2, i++);
    add("cool-1", "PUMP_HEALTH", "ratio", "measured", "%MW5", 1.0, i++);
    Object.keys(RV).forEach((r, k) => add(r, "VOLTS", "V", "measured", `%MW${6 + k}`, RV[r], i++));
    Object.keys(A).forEach((m, k) => add(m, "AMPS", "A", "measured", `%MW${8 + k}`, A[m][1], i++));
    Object.keys(A).forEach((m, k) => add(m, "THROUGHPUT", "pct", "measured", `%MW${24 + k}`, A[m][3], i++));
    cooled.forEach((m, k) => add(m, "TRIP", "bool", "measured", `%QX0.${k}`, 0, i++));
    Object.keys(A).forEach((m) => add(m, "VOLTS", "V", "derived", `= ${T(A[m][0], "VOLTS")}`, RV[A[m][0]], i++));
    cooled.forEach((m) => add(m, "HEAT", "W", "derived", `= ${HK[m]} * ${T(m, "AMPS")}`, HK[m] * A[m][1], i++));
    add("cool-1", "TRIP_LIMIT", "degC", "derived", "= const 78.0", 78.0, i++);
    return { source: "scada", plc_connected: true,
             historian: { connected: true, rows_total: 128740, rows_per_s: 41.0 }, tags: rows };
  })(),
  "/api/audit": {
    chain_ok: true, count: 3, auth: "enforced",
    entries: [
      { ts: 1752740400.2, actor: "operator", verb: "fleet-create", target: "plc-pack-1", status: "requested", evidence: {}, hash: "a1" },
      { ts: 1752741300.4, actor: "viewer", verb: "trigger", target: "PS5", status: "denied", evidence: {}, hash: "a2" },
      { ts: 1752741420.9, actor: "operator", verb: "trigger", target: "PS1", status: "fired", evidence: {}, hash: "a3" },
    ],
  },
  // 2H fleet and the 3D act loop (FLEET.md sections 10 and 11): design-review data only
  "/api/fleet": {
    source: "k8s", enroll: "enabled",
    plcs: [
      { name: "plc-stamping", profile: "siemens-s7-1200", profile_label: "Siemens S7-1200 (virtual)", protocol: { kind: "s7comm", port: 102 },
        managed: "static", task: { name: "stamping-line", title: "Stamping line", sha256: "3f9a1c7be2d04411", interval_ms: 100 },
        cell: { name: "stamping", rail: "psu-a", machines: ["press-1", "press-2"] }, state: "RUN", fault: null, ready: true,
        scan: { last_ms: 0.034, avg_ms: 0.032, max_ms: 0.21, overruns: 0 }, scada: { enrolled: true, connected: true, rtt_ms: 0.41, tags: 23, good: 23 },
        phases: [{ phase: "requested", ts: 1752741000 }, { phase: "scheduled", ts: 1752741000.6 }, { phase: "running", ts: 1752741003.1 },
          { phase: "enrolled", ts: 1752741003.9 }, { phase: "polling", ts: 1752741004.8 }, { phase: "in_window", ts: 1752741011.2 }] },
      { name: "plc-pack-1", profile: "generic-iec", profile_label: "IEC 61131-3 soft PLC", protocol: { kind: "modbus", port: 502 },
        managed: "ui", task: { name: "packaging-cell", title: "Packaging cell sequencer", sha256: "a81d09e4c3b27f55", interval_ms: 100 },
        cell: { name: "pack-1", rail: "psu-c", machines: ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1"] }, state: "STARTING", fault: null, ready: false,
        scan: { last_ms: null, avg_ms: null, max_ms: null, overruns: null }, scada: { enrolled: false, connected: false, rtt_ms: null, tags: 0, good: 0 },
        phases: [{ phase: "requested", ts: 1752741400 }, { phase: "scheduled", ts: 1752741400.7 }, { phase: "running", ts: 1752741403.3 },
          { phase: "enrolled", ts: null }, { phase: "polling", ts: null }, { phase: "in_window", ts: null }] },
    ],
  },
  "/api/fleet/profiles": [
    { id: "siemens-s7-1200", label: "Siemens S7-1200 (virtual)", protocol: "s7comm", port: 102 },
    { id: "generic-iec", label: "IEC 61131-3 soft PLC", protocol: "modbus", port: 502 },
  ],
  "/api/fleet/tasks": [
    { name: "stamping-line", title: "Stamping line", description: "Both presses run continuously at their DERATE_PCT setpoint.", profile_hint: "siemens-s7-1200",
      cell: { name: "stamping", rail_default: "psu-a", machines_fixed: ["press-1", "press-2"] }, st: "PROGRAM stamping_line\n  (* … *)\nEND_PROGRAM" },
    { name: "packaging-cell", title: "Packaging cell sequencer", description: "Conveyor runs, the wrapper cycles 20 s on and 10 s off, the labeler follows.", profile_hint: "generic-iec",
      cell: { name: "packaging", rail_default: "psu-c", machines: [{ prefix: "pack-conveyor", i_base: 9, cooled: false }, { prefix: "pack-wrapper", i_base: 14, cooled: true }, { prefix: "pack-labeler", i_base: 4, cooled: false }] },
      st: "PROGRAM packaging_cell\n  VAR\n    conveyor_ready AT %IX0.0 : BOOL;\n    (* … *)\n  END_VAR\n  (* … *)\nEND_PROGRAM" },
    { name: "packaging-cell-rush", title: "Packaging cell sequencer (rush)", description: "Rush cadence: wrapper 10 s on, 5 s off.", profile_hint: "generic-iec",
      cell: { name: "packaging", rail_default: "psu-c", machines: [{ prefix: "pack-conveyor", i_base: 9, cooled: false }, { prefix: "pack-wrapper", i_base: 14, cooled: true }, { prefix: "pack-labeler", i_base: 4, cooled: false }] },
      st: "PROGRAM packaging_cell_rush\nEND_PROGRAM" },
  ],
  "/api/actions": {
    write: "enabled", target_pct: 55,
    proposals: [{ id: "5c2e19ab77f0d3e1", verb: "derate", asset: "press-1", plc: "plc-stamping", tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", from: 100, to: 55,
      cites: { root: "press-1", edge: "press-1→psu-a", evidence: ["write", "rail", "temporal"], confidence: 0.92, signal: "bus_voltage" },
      expected: "press-1 draws less current, rail psu-a recovers" }],
    active: [],
  },
  "/api/recommendations": {
    source: "prometheus",
    right_sizing: [
      { verb: "resize", workload: "historian-db", resource: "memory", detail: "Working-set at 0.86 of limit under load — raise the memory limit.", p95: 0.86 },
      { verb: "reclaim", workload: "api", resource: "cpu", detail: "p95 CPU 0.12 ≪ request 0.50 — over-provisioned, give it back.", p95: 0.12 },
    ],
    fairness: [{ namespace: "aiops", gini: 0.12 }, { namespace: "plant", gini: 0.22 }, { namespace: "observability", gini: 0.10 }],
  },
};

// Patch a copy of the incident plant: per-device field overrides plus top-level fields.
function plantWith(devicePatch, top) {
  const p = INCIDENT["/api/plant"];
  const devices = {};
  for (const [n, d] of Object.entries(p.devices)) devices[n] = { ...d, ...(devicePatch[n] || {}) };
  return { ...p, devices, ...top };
}

const FLEET_RUN = {
  ...INCIDENT["/api/fleet"],
  plcs: INCIDENT["/api/fleet"].plcs.map((c) => c.name !== "plc-pack-1" ? c : {
    ...c, state: "RUN", ready: true,
    scan: { last_ms: 0.041, avg_ms: 0.039, max_ms: 0.33, overruns: 0 }, scada: { enrolled: true, connected: true, rtt_ms: 7.3, tags: 32, good: 32 },
    phases: [{ phase: "requested", ts: 1752741400 }, { phase: "scheduled", ts: 1752741400.2 }, { phase: "running", ts: 1752741401.2 },
      { phase: "enrolled", ts: 1752741402.8 }, { phase: "polling", ts: 1752741405.9 }, { phase: "in_window", ts: 1752741415.6 }],
  }),
};

const QUIET_GRAPH = { root: [], edges: [], blast_radius: [], findings: [], incipient: [], meta: { pods: 16, active: 0, accepted_edges: 0, signal: "bus_voltage" } };

const STEADY = {
  "/api/graph": QUIET_GRAPH,
  "/api/narrative": { text: "Steady state: no causal contention detected across 16 workloads.", source: "steady", model: null },
  "/api/plant": plantWith(
    { "press-1": { amps: 42.9, temp: 58.4, throughput: 100 }, "cnc-1": { throughput: 99.1 }, "qa-scanner-1": { throughput: 99.4 } },
    { rails: { "psu-a": { volts: 361.2, v_src: 400.0 }, "psu-b": { volts: 372.8, v_src: 400.0 }, "psu-c": { volts: 386.5, v_src: 400.0 } }, active_faults: [] }),
  "/api/pods": INCIDENT["/api/pods"].map((p) => ({ ...p, value: Math.min(p.value, 0.12), anomalous: false })),
  "/api/actions": { write: "enabled", target_pct: 55, proposals: [], active: [] },
  "/api/fleet": FLEET_RUN,
  "/api/audit": {
    chain_ok: true, count: 5, auth: "enforced",
    entries: [
      { ts: 1752741420.9, actor: "operator", verb: "trigger", target: "PS1", status: "fired", evidence: {}, hash: "s1" },
      { ts: 1752741466.3, actor: "operator", verb: "execute", target: "press-1", status: "executed", hash: "s2",
        evidence: { tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", from: 100, to: 55, root: "press-1", evidence: ["write", "rail", "temporal"] } },
      { ts: 1752741526.4, actor: "visr", verb: "relief", target: "press-1", status: "measured", hash: "s3",
        evidence: { after_s: 60, rail: "psu-a", volts_before: 344.1, volts_after: 356.8, amps_before: 84.9, amps_after: 47.2 } },
      { ts: 1752741590.0, actor: "operator", verb: "reset", target: "PS1", status: "reset", evidence: {}, hash: "s4" },
      { ts: 1752741611.7, actor: "operator", verb: "restore", target: "press-1", status: "restored", hash: "s5",
        evidence: { tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", from: 55, to: 100 } },
    ],
  },
};

const FORECAST = {
  "/api/graph": {
    ...QUIET_GRAPH,
    incipient: [
      { pod: "press-1", class: "trip", signal: "coolant_temp", eta_s: 38, value: 71.2, limit: 78, headroom_frac: 0.09 },
      { pod: "furnace-1", class: "trip", signal: "coolant_temp", eta_s: 52, value: 69.8, limit: 78, headroom_frac: 0.11 },
    ],
  },
  "/api/narrative": { text: "Early warning: press-1 coolant temperature is climbing toward the 78 °C trip (71 °C now) — projected trip in ~38s.", source: "forecast", model: null },
  "/api/plant": plantWith(
    { "press-1": { amps: 42.9, temp: 71.2, throughput: 100 }, "press-2": { temp: 66.3 }, "cnc-1": { temp: 63.0, throughput: 99.1 },
      "qa-scanner-1": { throughput: 99.4 }, "furnace-1": { temp: 69.8 } },
    { rails: STEADY["/api/plant"].rails, loop: { name: "cool-1", flow: 64.0, flow_nominal: 120.0, pump_health: 0.52 }, active_faults: ["PS5"] }),
  "/api/actions": STEADY["/api/actions"],
  "/api/fleet": FLEET_RUN,
  "/api/audit": {
    chain_ok: true, count: 2, auth: "enforced",
    entries: [
      { ts: 1752740400.2, actor: "operator", verb: "fleet-create", target: "plc-pack-1", status: "requested", evidence: {}, hash: "f1" },
      { ts: 1752741700.5, actor: "operator", verb: "trigger", target: "PS5", status: "fired", evidence: {}, hash: "f2" },
    ],
  },
};

// The fault catalogue (/api/scenarios), with the given ids active
const CAT = [
  ["PS0", "Steady plant", "no fault · baselines mature · the engine stays silent", "", null],
  ["PS1", "Rail-sag cascade", "press-1 bearing friction → amps up → rail A sags → cnc-1 and qa-scanner-1 degrade", "Milford Haven refinery, 1994: 275 alarms in the last 11 minutes", "plant"],
  ["PS2", "Power sag trips the chiller", "compressor-1 stuck on → rail B sags → chiller-1 overload trips → coolant flow drops", "Azure Australia East, 2023: a power sag tripped the chillers", "plant"],
  ["PS3", "Control network storm", "hmi-gw floods segment field-1 → the stamping cell link lags and drops", "Browns Ferry Unit 3, 2006: network traffic stopped both recirculation pump drives", "plant"],
  ["PS4A", "Setpoint write with no record", "a rogue client writes press-1 DERATE over S7comm, outside the SCADA write path", "Stuxnet 2010, FrostyGoop 2024, Ukraine grid 2015", "ews"],
  ["PS4B", "Current report contradicts the feeder", "press-1 AMPS to the PLC replays a normal value while the real current rises", "Stuxnet replayed normal values. Buncefield 2005: a stuck gauge.", "plant"],
  ["PS5", "Coolant pump degradation", "flow drops → temperatures ramp toward the 78 °C trip (forecast first)", "LG Polymers, Visakhapatnam, 2020: no sensor at the tank top", "plant"],
  ["PS6", "The monitor runs out of memory", "the tag server leaks toward its 128 MiB limit → forecast → the SCADA view goes blind", "Toyota, 2023: a full disk stopped 12 plants", "scada"],
];
const catalogue = (on) => CAT.map(([id, name, mechanism, anchor, owner]) => ({
  id, name, mechanism, anchor, owner, plane: owner === "ews" ? "integrity" : owner === "scada" ? "edge" : "plant",
  triggerable: id !== "PS0", expect: "", expect_s: { PS1: 90, PS2: 150, PS3: 90, PS4A: 45, PS4B: 40, PS5: 60, PS6: 120 }[id] || 90,
  active: id === "PS0" ? on.length === 0 : on.includes(id),
}));
INCIDENT["/api/scenarios"] = catalogue(["PS1"]);
STEADY["/api/scenarios"] = catalogue([]);
FORECAST["/api/scenarios"] = catalogue(["PS5"]);

const CHAIN = {
  "/api/scenarios": catalogue(["PS2"]),
  "/api/graph": {
    root: [{ pod: "compressor-1", score: 0.43, onset_s: 35 }, { pod: "chiller-1", score: 0.18, onset_s: 95 }],
    edges: [
      { src: "compressor-1", dst: "chiller-1", r: 1.0, lag_s: 5, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.96, state: "active", render_weight: 0.96 },
      { src: "compressor-1", dst: "conveyor-1", r: 1.0, lag_s: 5, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.94, state: "active", render_weight: 0.94 },
      { src: "compressor-1", dst: "psu-b", r: 1.0, lag_s: 5, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.94, state: "active", render_weight: 0.94 },
      { src: "chiller-1", dst: "press-1", r: 0.94, lag_s: 15, evidence: ["write", "loop", "temporal"], signal: "coolant_temp", confidence: 0.9, state: "active", render_weight: 0.9 },
      { src: "chiller-1", dst: "furnace-1", r: 0.93, lag_s: 30, evidence: ["write", "loop", "temporal"], signal: "coolant_temp", confidence: 0.88, state: "active", render_weight: 0.88 },
      { src: "chiller-1", dst: "cnc-1", r: 0.95, lag_s: 5, evidence: ["write", "loop", "temporal"], signal: "coolant_temp", confidence: 0.9, state: "active", render_weight: 0.9 },
    ],
    blast_radius: [{ pod: "chiller-1", impact: 0.9, eta_s: 5 }, { pod: "conveyor-1", impact: 0.8, eta_s: 5 }, { pod: "psu-b", impact: 0.8, eta_s: 5 },
      { pod: "cnc-1", impact: 0.7, eta_s: 10 }, { pod: "press-1", impact: 0.7, eta_s: 20 }, { pod: "furnace-1", impact: 0.6, eta_s: 35 }],
    findings: [{ pod: "compressor-1", class: "shift", onset_s: 35, severity: 1 }, { pod: "press-1", class: "leak", onset_s: 110, severity: 0.8 }],
    incipient: [{ pod: "furnace-1", class: "trip", signal: "coolant_temp", eta_s: 44, value: 72.4, limit: 78, headroom_frac: 0.07 },
      { pod: "press-1", class: "trip", signal: "coolant_temp", eta_s: 61, value: 70.1, limit: 78, headroom_frac: 0.1 }],
    meta: { pods: 17, active: 7, accepted_edges: 14, signal: "bus_voltage" },
  },
  "/api/narrative": { text: "compressor-1 stuck on sags rail psu-b. The sustained undervoltage tripped the chiller-1 overload, so coolant flow fell and the cooled machines are heating. Forecast: furnace-1 trips in about 44 s.", source: "fallback", model: null },
  "/api/plant": plantWith(
    { "compressor-1": { amps: 57.1 }, "chiller-1": { amps: 0.2, tripped: true, trip_reason: "overload" }, "press-1": { temp: 70.1 }, "furnace-1": { temp: 72.4 }, "cnc-1": { temp: 63.9 } },
    { rails: { "psu-a": { volts: 360.8, v_src: 400.0, amps: 112.6 }, "psu-b": { volts: 363.4, v_src: 400.0, amps: 104.1 }, "psu-c": { volts: 386.5, v_src: 400.0, amps: 0 } },
      loop: { name: "cool-1", flow: 53.7, flow_nominal: 120.0, pump_health: 1.0 }, active_faults: ["PS2"] }),
  "/api/actions": { write: "enabled", target_pct: 55, proposals: [], active: [], blocked: [] },
  "/api/fleet": FLEET_RUN,
};

const NETWORK = {
  "/api/scenarios": catalogue(["PS3"]),
  "/api/graph": {
    root: [{ pod: "hmi-gw", score: 0.51, onset_s: 30 }],
    edges: [{ src: "hmi-gw", dst: "plc-stamping", r: 0.97, lag_s: 5, evidence: ["write", "net", "temporal"], signal: "field_latency", confidence: 0.92, state: "active", render_weight: 0.92 }],
    blast_radius: [{ pod: "plc-stamping", impact: 0.9, eta_s: 5 }],
    findings: [{ pod: "plc-stamping", class: "burst", onset_s: 35, severity: 0.9 }], incipient: [],
    meta: { pods: 17, active: 3, accepted_edges: 3, signal: "field_latency" },
  },
  "/api/narrative": { text: "hmi-gw floods field network segment field-1. The stamping PLC link now lags and drops, so press-1 and press-2 fall back to fail-open.", source: "fallback", model: null },
  "/api/plant": plantWith({}, {
    active_faults: ["PS3"],
    segments: { "field-1": { capacity_fps: 1000, utilization: 1.51, latency_ms: 48.2, drop_ratio: 0.34,
      members: { "hmi-gw": { kind: "talker", offered_fps: 1500, latency_ms: 48.2 },
        "plc-stamping": { kind: "plc", cell: "stamping", offered_fps: 8, latency_ms: 1210, sync_age_s: 6.1, failures: 4 } } } } }),
  "/api/fleet": FLEET_RUN,
};

const INTEGRITY = {
  "/api/scenarios": catalogue(["PS4A"]),
  "/api/graph": {
    ...QUIET_GRAPH,
    integrity: [{ id: "unsigned_write/plc-stamping/FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", kind: "unsigned_write", status: "open",
      plc: "plc-stamping", asset: "press-1", tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", address: "%MW10", from: 100.0, to: 30.0,
      reason: "no ledger row", clients: ["rogue-ews"] }],
  },
  "/api/narrative": { text: "Steady state: no causal contention detected. An integrity finding is open: press-1 DERATE changed from 100 to 30 with no signed ledger row.", source: "steady", model: null },
  "/api/plant": plantWith({ "press-1": { amps: 12.7, speed_pct: 30, commanded: { run: true, speed_pct: 30 } } },
    { rails: { "psu-a": { volts: 371.4, v_src: 400.0, amps: 82.1 }, "psu-b": { volts: 372.8, v_src: 400.0, amps: 76.6 }, "psu-c": { volts: 386.5, v_src: 400.0, amps: 0 } }, active_faults: [] }),
  "/api/actions": { write: "enabled", target_pct: 55, proposals: [],
    active: [{ asset: "press-1", plc: "plc-stamping", tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", value: 30.0, quality: "GOOD", signed: false }],
    blocked: [{ asset: "press-1", plc: "plc-stamping", reason: "unsigned write on FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT" }] },
  "/api/fleet": FLEET_RUN,
  "/api/audit": {
    chain_ok: true, count: 3, auth: "enforced",
    entries: [
      { ts: 1752741700.5, actor: "operator", verb: "trigger", target: "PS4A", status: "fired", evidence: {}, hash: "i1" },
      { ts: 1752741741.2, actor: "visr", verb: "unsigned", target: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", status: "detected", hash: "i2",
        evidence: { plc: "plc-stamping", asset: "press-1", tag: "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", from: 100.0, to: 30.0, reason: "no ledger row", clients: ["rogue-ews"] } },
    ],
  },
};

const BLIND = {
  "/api/scenarios": catalogue(["PS6"]),
  "/api/graph": { ...QUIET_GRAPH, incipient: [{ pod: "tag-server", class: "leak", signal: "mem", eta_s: 42, value: 118 * 1048576, limit: 128 * 1048576, headroom_frac: 0.08 }] },
  "/api/narrative": { text: "Early warning: tag-server memory is climbing toward its 128 MiB limit. Projected OOM in about 42 s.", source: "forecast", model: null },
  "/api/tags": { source: "unavailable", tags: [] },
  "/api/fleet": FLEET_RUN,
};

const VARIANTS = { steady: STEADY, forecast: FORECAST, chain: CHAIN, network: NETWORK, integrity: INTEGRITY, blind: BLIND };

export function mockVariant() {
  return typeof window === "undefined" ? "incident" : new URLSearchParams(window.location.search).get("mock") || "incident";
}

// A fresh, slightly varied plant object per call, so the dev sparklines move like live data.
function jitterPlant(p) {
  const j = (v, a) => (v == null ? v : +(v + (Math.random() - 0.5) * 2 * a).toFixed(2));
  const devices = {};
  for (const [n, d] of Object.entries(p.devices)) {
    devices[n] = { ...d, amps: j(d.amps, d.amps * 0.02), temp: j(d.temp, 0.25), throughput: Math.min(100, j(d.throughput, 0.6)) };
  }
  const rails = {};
  for (const [n, r] of Object.entries(p.rails)) rails[n] = { ...r, volts: j(r.volts, 0.7) };
  return { ...p, devices, rails, loop: p.loop && { ...p.loop, flow: j(p.loop.flow, 0.9) } };
}

export function mockFor(path) {
  const v = VARIANTS[mockVariant()];
  const out = v && v[path] !== undefined ? v[path] : INCIDENT[path];
  return path === "/api/plant" && out ? jitterPlant(out) : out;
}
