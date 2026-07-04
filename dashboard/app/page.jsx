"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";

// The 3D causal graph is WebGL (touches window) -> client-only.
const Graph = dynamic(() => import("./Graph"), { ssr: false });
import Machines from "./Machines";
import Floor from "./Floor";

// Dev-only sample data so `next dev` renders populated for design review. NEVER used in the
// production static export (NODE_ENV==='production' there) — the deployed dashboard shows live
// engine data only, and surfaces empty/steady states honestly when the engine is quiet/down.
const DEV = process.env.NODE_ENV !== "production";
const MOCK = {
  "/api/health": { ok: true, services: { aggregator: "up", engine: "up" } },
  "/api/graph": {
    root: [{ pod: "press-1", score: 0.62, onset_s: 41 }],
    edges: [
      // floor plane: the PS1 rail-A story (design-review data for Floor.jsx's causal overlay)
      { src: "press-1", dst: "psu-a", r: 0.87, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.92, state: "active", render_weight: 0.92 },
      { src: "press-1", dst: "cnc-1", r: 0.83, evidence: ["write", "rail", "temporal"], signal: "bus_voltage", confidence: 0.9, state: "active", render_weight: 0.9 },
      { src: "press-1", dst: "qa-scanner-1", r: 0.61, evidence: ["stat", "rail"], signal: "bus_voltage", confidence: 0.66, state: "active", render_weight: 0.66 },
      { src: "press-1", dst: "cnc-1", r: 0.58, evidence: ["stat", "loop"], signal: "coolant_temp", confidence: 0.6, state: "active", render_weight: 0.6 },
      // edge plane: one pod-plane edge so the EDGE toggle stays reviewable
      { src: "cooling-monitor", dst: "timescaledb", r: 0.81, evidence: ["write", "pvc", "temporal"], signal: "psi_io", confidence: 0.92, state: "active" },
    ],
    blast_radius: [{ pod: "cnc-1", impact: 0.7, eta_s: 30 }, { pod: "qa-scanner-1", impact: 0.55, eta_s: 60 }, { pod: "psu-a", impact: 0.7, eta_s: 0 }, { pod: "timescaledb", impact: 0.3, eta_s: 90 }],
    findings: [{ pod: "press-1", class: "leak", onset_s: 30, severity: 0.8 }],
    incipient: [],
    meta: { pods: 16, active: 1, accepted_edges: 5, signal: "bus_voltage" },
  },
  "/api/narrative": { text: "press-1 is the likely root of the rail-A voltage sag; cnc-1 and qa-scanner-1 degrade with it. Recommend derating press-1.", source: "llm" },
  "/api/topology": { edges: [{ src: "cooling-monitor", dst: "timescaledb", port: 5432 }], source: "caretta" },
  "/api/pods": [
    { workload: "cooling-monitor", namespace: "factory-data", signal: "psi_io", value: 0.94, anomalous: true },
    { workload: "timescaledb", namespace: "factory-data", signal: "psi_io", value: 0.61, anomalous: true },
    { workload: "dcim-bridge", namespace: "factory-data", signal: "psi_io", value: 0.20, anomalous: false },
    { workload: "plc-gateway", namespace: "factory-core", signal: "psi_io", value: 0.05, anomalous: false },
    { workload: "mqtt-broker", namespace: "factory-core", signal: "psi_io", value: 0.03, anomalous: false },
    { workload: "vision-qc", namespace: "factory-edge", signal: "psi_io", value: 0.07, anomalous: false },
  ],
  "/api/pod-resources": {
    source: "prometheus", pods: [
      { namespace: "factory-data", pod: "cooling-monitor-x", workload: "cooling-monitor", cpu: { usage: 0.42, request: 0.5, limit: 1.0 }, mem: { usage: 640e6, request: 512e6, limit: 768e6 } },
      { namespace: "factory-data", pod: "timescaledb-0", workload: "timescaledb", cpu: { usage: 0.18, request: 0.25, limit: 0.5 }, mem: { usage: 537e6, request: 512e6, limit: 640e6 } },
      { namespace: "factory-data", pod: "dcim-bridge-x", workload: "dcim-bridge", cpu: { usage: 0.11, request: 0.1, limit: 0.25 }, mem: { usage: 252e6, request: 256e6, limit: 320e6 } },
      { namespace: "factory-core", pod: "plc-gateway-x", workload: "plc-gateway", cpu: { usage: 0.26, request: 0.25, limit: 0.5 }, mem: { usage: 126e6, request: 128e6, limit: 192e6 } },
      { namespace: "factory-core", pod: "mqtt-broker-x", workload: "mqtt-broker", cpu: { usage: 0.08, request: 0.1, limit: 0.25 }, mem: { usage: 115e6, request: 128e6, limit: 192e6 } },
      { namespace: "factory-edge", pod: "vision-qc-x", workload: "vision-qc", cpu: { usage: 0.51, request: 0.5, limit: 1.0 }, mem: { usage: 252e6, request: 256e6, limit: 512e6 } },
    ],
  },
  "/api/plant": {
    source: "sim",
    rails: { "psu-a": { volts: 346.3, v_src: 400.0 }, "psu-b": { volts: 372.8, v_src: 400.0 } },
    loop: { name: "cool-1", flow: 118.2, flow_nominal: 120.0, pump_health: 1.0 },
    trip_c: 78.0,
    devices: {
      "press-1":      { amps: 79.6, temp: 66.1, throughput: 99.2, rail: "psu-a", cooled: true },
      "press-2":      { amps: 37.8, temp: 55.9, throughput: 98.7, rail: "psu-a", cooled: true },
      "cnc-1":        { amps: 28.7, temp: 50.6, throughput: 84.4, rail: "psu-a", cooled: true },
      "qa-scanner-1": { amps: 7.2,  temp: null, throughput: 85.8, rail: "psu-a", cooled: false },
      "conveyor-1":   { amps: 18.2, temp: null, throughput: 100,  rail: "psu-b", cooled: false },
      "compressor-1": { amps: 6.7,  temp: null, throughput: 100,  rail: "psu-b", cooled: false },
      "furnace-1":    { amps: 30.4, temp: 65.3, throughput: 100,  rail: "psu-b", cooled: true },
      "chiller-1":    { amps: 22.0, temp: null, throughput: 100,  rail: "psu-b", cooled: false },
    },
    active_faults: ["PS1"],
  },
  "/api/recommendations": {
    source: "prometheus",
    right_sizing: [
      { verb: "resize", workload: "timescaledb", resource: "memory", detail: "Working-set at 0.86 of limit under load — raise the memory limit.", p95: 0.86 },
      { verb: "reclaim", workload: "vision-qc", resource: "cpu", detail: "p95 CPU 0.12 ≪ request 0.50 — over-provisioned, give it back.", p95: 0.12 },
    ],
    fairness: [{ namespace: "factory-core", gini: 0.12 }, { namespace: "factory-data", gini: 0.22 }, { namespace: "factory-edge", gini: 0.10 }],
  },
};

async function getJSON(path) {
  try {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path} ${r.status}`);
    return await r.json();
  } catch (e) {
    if (DEV && MOCK[path] !== undefined) return MOCK[path];
    throw e;
  }
}

const RES_WORD = { psi_io: "disk I/O", psi_cpu: "CPU", psi_mem: "memory", bus_voltage: "rail voltage", coolant_temp: "coolant temperature" };
const ST = { hot: { c: "var(--red)", t: "hot" }, strained: { c: "var(--orange)", t: "strained" }, ok: { c: "var(--green)", t: "ok" } };
const m = (c) => (c ? Math.round(c * 1000) + "m" : "—");                 // cores -> millicpu
const Mi = (b) => (b ? Math.round(b / 1048576) + "MiB" : "—");          // bytes -> MiB
const pctOf = (use, req, lim) => { const d = lim || req; return d ? (use || 0) / d : null; };
const meterColor = (v) => (v == null ? "var(--text-faint)" : v >= 0.9 ? "var(--red)" : v >= 0.7 ? "var(--orange)" : "var(--green)");

const GRAFANA = { port: 30030, uid: "skn-psi", slug: "skn-psi", panels: [{ id: 1, cap: "PSI · I/O" }, { id: 2, cap: "PSI · CPU" }, { id: 3, cap: "PSI · memory" }] };

// PS-series: plant-physics faults (perturb the model; symptoms EMERGE). The S-series kernel
// motifs are retired to the regression bench — the frozen Codex build remains their home.
const SCN = [
  { id: "PS0", name: "Steady plant", desc: "No faults; baselines mature and the engine stays silent.", fire: false },
  { id: "PS1", name: "Rail-sag cascade", desc: "press-1 bearing friction → amps up → rail A sags → cnc-1 / qa-scanner degrade.", fire: true },
  { id: "PS2", name: "Duty-cycle aggressor stuck on", desc: "compressor-1 pinned at full draw on rail B — the no-matured-baseline case.", fire: true },
  { id: "PS5", name: "Coolant pump degradation", desc: "Flow drops → temps ramp toward the 78 °C trip; expect the forecast card first.", fire: true },
];

export default function Page() {
  const [graph, setGraph] = useState(null);
  const [narr, setNarr] = useState(null);
  const [health, setHealth] = useState(null);
  const [topo, setTopo] = useState(null);
  const [pods, setPods] = useState(null);
  const [podres, setPodres] = useState(null);
  const [plant, setPlant] = useState(null);
  const [recs, setRecs] = useState(null);
  const [live, setLive] = useState({});
  const [fired, setFired] = useState(null);
  const [plane, setPlane] = useState("floor");   // Causal Monitor view: plant floor | edge stack
  const [updated, setUpdated] = useState(null);
  const [host, setHost] = useState("");

  useEffect(() => { setHost(window.location.hostname); }, []);

  async function refresh() {
    try {
      const [g, n, h, t, p, pr, pl] = await Promise.all([
        getJSON("/api/graph"),
        getJSON("/api/narrative"),
        getJSON("/api/health"),
        getJSON("/api/topology").catch(() => null),
        getJSON("/api/pods").catch(() => null),
        getJSON("/api/pod-resources").catch(() => null),
        getJSON("/api/plant").catch(() => null),
      ]);
      setGraph(g); setNarr(n); setHealth(h);
      if (t) setTopo(t); if (p) setPods(p); if (pr) setPodres(pr); if (pl) setPlant(pl);
      setUpdated(new Date());
    } catch (e) { /* keep last good values */ }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const load = () => getJSON("/api/recommendations").then(setRecs).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  async function scenario(sid, action) {
    setFired(`${action === "reset" ? "resetting" : "firing"} ${sid}…`);
    setLive((l) => ({ ...l, [sid]: action !== "reset" }));
    try {
      const r = await fetch(`/api/scenarios/${sid}/${action}`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      setFired(r.ok
        ? `${sid} ${action === "reset" ? "reset — clears in ~2–3 min" : "fired — changes show in ~50s"} (${new Date().toLocaleTimeString()})`
        : `error ${r.status}: ${j.detail || ""}`);
    } catch (e) { setFired("error: " + e); }
  }

  // ── derive ──────────────────────────────────────────────────────────────
  const meta = graph?.meta || {};
  const root = graph?.root?.[0];
  const edges = graph?.edges || [];
  const blast = graph?.blast_radius || [];
  const blastSet = new Set(blast.map((b) => b.pod));
  const rootEdge = root
    ? [...edges.filter((e) => e.src === root.pod)].sort((a, b) => Math.abs(b.r || 0) - Math.abs(a.r || 0))[0]
    : null;
  const resWord = RES_WORD[meta.signal] || "resource";
  const engineUp = health?.ok;
  const statusOf = (w) => (w === root?.pod ? "hot" : blastSet.has(w) ? "strained" : null);

  // ── FLOOR/EDGE causal planes (LOG-036): one brain, two views. FLOOR = the static plant floor
  // (Floor.jsx — a factory is statically wired; the graph pre-exists, runtime only weights it).
  // EDGE = the pod plane, where topology genuinely IS discovered (force graph + caretta). The
  // plant entity set comes from /api/plant itself; cross-plane edges can't exist (domain
  // witnesses are default-deny), so EDGE is simply everything not fully inside the floor.
  const plantSet = new Set([
    ...Object.keys(plant?.devices || {}),
    ...Object.keys(plant?.rails || {}),
    ...(plant?.loop?.name ? [plant.loop.name] : []),
  ]);
  const inFloor = (e) => plantSet.has(e.src) && plantSet.has(e.dst);
  const edgeGraph = graph ? {
    ...graph,
    edges: edges.filter((e) => !inFloor(e)),
    findings: (graph.findings || []).filter((f) => !plantSet.has(f.pod)),
  } : graph;

  const resByWl = {};
  for (const p of podres?.pods || []) {
    const r = resByWl[p.workload] || (resByWl[p.workload] = { cpuUse: 0, cpuReq: 0, cpuLim: 0, memUse: 0, memReq: 0, memLim: 0 });
    r.cpuUse += p.cpu?.usage || 0; r.cpuReq += p.cpu?.request || 0; r.cpuLim += p.cpu?.limit || 0;
    r.memUse += p.mem?.usage || 0; r.memReq += p.mem?.request || 0; r.memLim += p.mem?.limit || 0;
  }
  const podRows = (pods || []).map((p) => {
    const r = resByWl[p.workload] || {};
    return {
      name: p.workload, ns: p.namespace, st: statusOf(p.workload) || (p.anomalous ? "strained" : "ok"),
      cpuUse: r.cpuUse, cpuReq: r.cpuReq, cpuLim: r.cpuLim, memUse: r.memUse, memReq: r.memReq, memLim: r.memLim,
      cpuPct: pctOf(r.cpuUse, r.cpuReq, r.cpuLim), memPct: pctOf(r.memUse, r.memReq, r.memLim), ioPct: p.value,
    };
  });
  // Matrix reads top-left → down: root first, then blast-radius, then steady (stable sort
  // keeps the API's hottest-first order within each rank).
  const stRank = { hot: 0, strained: 1, ok: 2 };
  podRows.sort((a, b) => stRank[a.st] - stRank[b.st]);

  const actions = [];
  if (root && rootEdge) {
    actions.push({
      cls: "act", verb: "throttle", nm: rootEdge.src || root.pod,
      ds: `Sourcing the ${resWord} storm — throttle to relieve ${rootEdge.dst || "downstream"}.`,
      ct: <><b>cites:</b> edge {rootEdge.src}→{rootEdge.dst} · {(rootEdge.evidence || []).join("+")}</>,
    });
  }
  for (const c of recs?.right_sizing || []) {
    const reclaim = c.verb === "reclaim";
    actions.push({
      cls: reclaim ? "good" : "act", verb: reclaim ? "reclaim" : "resize ↑", nm: c.workload, ds: c.detail,
      ct: <><b>cites:</b> p95 {c.resource || ""} vs {reclaim ? "request" : "limit"} · 1h window</>,
    });
  }
  const nsGinis = (recs?.fairness || []).map((f) => f.gini);   // pivot: API already scopes to the live namespaces (aiops|observability|plant)
  const fairness = nsGinis.length ? 1 - nsGinis.reduce((a, b) => a + b, 0) / nsGinis.length : null;

  return (
    <>
      <main className="app">
        {/* ── Causal Monitor ── */}
        <section className="viz">
          <Head title="Causal Monitor" meta={
            <><span className="dot" style={{ background: engineUp ? "var(--green)" : "var(--text-faint)" }} />
              {engineUp ? "engine online" : "engine offline"}</>} />
          <div className="cm2">
            <div className={`cm-graph${plane === "floor" ? " isfloor" : ""}`}>
              <div className="brk">topology · {plane === "floor" ? "plant floor" : "edge stack"}</div>
              <div className="gtog">
                <button className={plane === "floor" ? "on" : ""} onClick={() => setPlane("floor")}>floor</button>
                <button className={plane === "edge" ? "on" : ""} onClick={() => setPlane("edge")}>edge</button>
              </div>
              {plane === "floor" ? <Floor plant={plant} graph={graph} /> : <Graph graph={edgeGraph} topo={topo} />}
            </div>
            <div className="rootcard verdict">
              {root ? (
                <>
                  <div>
                    <div className="lbl">root cause</div>
                    <div className="who"><span className="sq" style={{ background: "var(--red)" }} />{root.pod}</div>
                    <div className="meta2">
                      {typeof (rootEdge?.confidence ?? root.score) === "number" ? `${(rootEdge?.confidence ?? root.score).toFixed(2)} confidence` : ""}
                      {root.onset_s != null ? ` · detect <${Math.ceil(root.onset_s)}s` : ""}
                    </div>
                    <p>{narr?.text || "…"}</p>
                  </div>
                  <div>
                    {rootEdge?.evidence?.length ? (
                      <div className="echips">{rootEdge.evidence.map((e) => <span key={e} className="echip">{e}</span>)}</div>
                    ) : null}
                    {blast.length ? (
                      <>
                        <div className="lbl">blast radius</div>
                        <div className="blast">
                          {blast.map((b) => (
                            <div key={b.pod} className="b"><span className="dot" style={{ background: ST[statusOf(b.pod) || "strained"].c }} />{b.pod}</div>
                          ))}
                        </div>
                      </>
                    ) : null}
                  </div>
                </>
              ) : (
                <div>
                  <div className="lbl">root cause</div>
                  <div className="who"><span className="sq" style={{ background: "var(--green)" }} />steady</div>
                  <p>{narr?.text || "No causal contention detected — the edge stack is at steady state."}</p>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ── Machines (plane 2 — the plant floor is the PRIMARY subject; pods below just host it) ── */}
        <section className="viz">
          <Head title="Machines" meta={`${Object.keys(plant?.devices || {}).length || "—"} assets · simulated plant`} />
          <Machines plant={plant} host={host} />
        </section>

        {/* ── Pods (secondary — the hosting plane: these workloads run the AIOps engine + SCADA) ── */}
        <section className="viz">
          <Head title="Pods" meta={`${meta.pods ?? podRows.length} workloads · aiops + scada hosts`} />
          <div className="pods">
            {podRows.length ? podRows.map((n) => (
              <div key={n.name} className={`pod ${n.st}`}>
                <div className="who"><span className="dot" style={{ background: ST[n.st].c }} /><span className="nm">{n.name}</span><span className="ns">{n.ns}</span></div>
                <div className="gauges">
                  <Gauge label="CPU" value={n.cpuPct} />
                  <Gauge label="MEM" value={n.memPct} />
                  <Gauge label="I/O" value={n.ioPct} />
                </div>
                <div className="pod-pop">
                  <div className="ttl">{n.name} · allocations vs utility</div>
                  <div className="prow hdr"><span /><span>use</span><span>req</span><span>lim</span></div>
                  <div className="prow"><span className="k">CPU</span><span className="v">{m(n.cpuUse)}</span><span className="v">{m(n.cpuReq)}</span><span className="v">{m(n.cpuLim)}</span></div>
                  <div className="prow"><span className="k">MEM</span><span className="v">{Mi(n.memUse)}</span><span className="v">{Mi(n.memReq)}</span><span className="v">{Mi(n.memLim)}</span></div>
                  <div className="prow"><span className="k">I/O</span><span className="v v3">PSI {n.ioPct != null ? Math.round(n.ioPct * 100) + "%" : "—"} · pressure (no limit)</span></div>
                </div>
              </div>
            )) : <div style={{ color: "var(--text-faint)" }}>waiting for telemetry…</div>}
          </div>
        </section>

        {/* ── Pressure (PSI) — retained Grafana graph + CPU/memory pressure (same d-solo embed) ── */}
        <section className="viz">
          <Head title="Pressure (PSI)" meta="grafana · prometheus" />
          <div className="gnote">Per-workload pressure-stall — the engine's three signals, live from Grafana over Prometheus.</div>
          <div className="gframes">
            {GRAFANA.panels.map((g) => (
              <div key={g.id} className="gframe">
                <div className="cap">{g.cap}</div>
                {host ? <iframe title={g.cap} loading="lazy" src={`http://${host}:${GRAFANA.port}/d-solo/${GRAFANA.uid}/${GRAFANA.slug}?orgId=1&panelId=${g.id}&theme=dark&from=now-15m&to=now&refresh=5s&timezone=Asia/Kolkata`} /> : <div style={{ height: 220 }} />}
              </div>
            ))}
          </div>
        </section>

        {/* ── Scenarios ── */}
        <section className="viz">
          <Head title="Scenarios" meta="solo mode" />
          {SCN.map((s) => {
            const on = live[s.id];
            return (
              <div key={s.id} className={`scn${on ? " live" : ""}`}>
                <span className="id">{s.id}</span>
                <div className="b"><div className="nm">{s.name}</div><div className="ds">{s.desc}</div></div>
                <span className="st">
                  <span className="dot" style={{ background: on ? "var(--accent)" : "var(--text-faint)" }} />
                  <span style={{ color: on ? "var(--accent)" : "var(--text-weak)" }}>{on ? "live" : "idle"}</span>
                </span>
                {s.fire && (on
                  ? <button className="btn" onClick={() => scenario(s.id, "reset")}>Reset</button>
                  : <button className="btn cyan" onClick={() => scenario(s.id, "trigger")}>Fire</button>)}
              </div>
            );
          })}
          {fired && <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-faint)" }}>{fired}</div>}
        </section>

        {/* ── Recommendations ── */}
        <section className="viz">
          <Head title="Recommendations" meta={`${actions.length} actions · cited`} />
          {actions.length ? actions.map((a, i) => (
            <div key={i} className="rec">
              <span className={`actpill ${a.cls}`}>{a.verb}</span>
              <div><div className="nm">{a.nm}</div><div className="ds">{a.ds}</div><div className="ct">{a.ct}</div></div>
            </div>
          )) : <div style={{ color: "var(--text-faint)" }}>{recs?.source === "unavailable" ? "Prometheus unavailable" : "all workloads right-sized — no actions"}</div>}
          {fairness != null && (
            <div className="fair">
              <span className="lbl">namespace fairness · edge</span>
              <div className="bar"><i style={{ width: `${Math.round(fairness * 100)}%` }} /></div>
              <span className="val">{fairness.toFixed(2)}</span>
            </div>
          )}
        </section>
      </main>

      <div className="statusbar">
        <span>signal: {meta.signal || "—"}</span>
        <span>↻ 5s · {updated ? updated.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }) : "…"} IST</span>
      </div>
    </>
  );
}

function Head({ title, meta }) {
  return (
    <div className="viz-head">
      <div className="h"><b>VISR</b> <span>· {title}</span></div>
      <div className="viz-meta">{meta}</div>
    </div>
  );
}

function Gauge({ label, value }) {
  const r = 22, C = 2 * Math.PI * r;
  const v = value == null ? 0 : Math.max(0, Math.min(1, value));
  return (
    <div className="gauge">
      <svg width="54" height="54" viewBox="0 0 54 54">
        <circle cx="27" cy="27" r={r} fill="none" stroke="rgba(204,204,220,0.12)" strokeWidth="5" />
        <circle cx="27" cy="27" r={r} fill="none" stroke={meterColor(value)} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={C} strokeDashoffset={C * (1 - v)} transform="rotate(-90 27 27)" />
        <text x="27" y="28" textAnchor="middle" dominantBaseline="middle"
          style={{ fontFamily: "var(--display)", fontWeight: 600, fontSize: "13px", fill: "var(--text)" }}>
          {value == null ? "—" : Math.round(v * 100) + "%"}
        </text>
      </svg>
      <div className="glabel">{label}</div>
    </div>
  );
}
