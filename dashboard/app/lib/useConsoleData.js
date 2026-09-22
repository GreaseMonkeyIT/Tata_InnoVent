import { useEffect, useState } from "react";
import { getJSON } from "./api";
import { RES_WORD, pctOf } from "./format";

// All console data in one hook: the polling loops, the operator actions, and the values derived
// from the verdict. Every panel reads the same snapshot, so two panels never disagree.
export default function useConsoleData() {
  const [graph, setGraph] = useState(null);
  const [narr, setNarr] = useState(null);
  const [health, setHealth] = useState(null);
  const [topo, setTopo] = useState(null);
  const [pods, setPods] = useState(null);
  const [podres, setPodres] = useState(null);
  const [plant, setPlant] = useState(null);
  const [recs, setRecs] = useState(null);
  const [audit, setAudit] = useState(null);
  const [scada, setScada] = useState(null);       // 2F.2 tag browser (/api/tags)
  const [fleet, setFleet] = useState(null);       // 2H virtual PLC fleet (/api/fleet)
  const [actions, setActions] = useState(null);   // 3D act loop (/api/actions)
  const [tasks, setTasks] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [scenarios, setScenarios] = useState(null); // SCENARIOS.md 5.1: the fault catalogue (/api/scenarios)
  const [pending, setPending] = useState({});     // sid -> in-flight trigger or reset (button busy state)
  const [fired, setFired] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [feedErr, setFeedErr] = useState(null);   // the last failed core call: {what, at}
  const [tagsOkAt, setTagsOkAt] = useState(null); // the last time the tag server answered (PS6 blind band)
  const [now, setNow] = useState(() => Date.now()); // a 5 s clock, so a stale feed renders as stale

  async function refresh() {
    // Each call fails on its own: a dead engine must not freeze the plant, the tags, or the
    // fleet panels, and a dead tag server must show as blind, not as the last good picture.
    const miss = [];
    const core = (path) => getJSON(path).catch(() => { miss.push(path); return null; });
    const [g, n, h, t, p, pr, pl, tg, fl, ac, sc] = await Promise.all([
      core("/api/graph"),
      core("/api/narrative"),
      core("/api/health"),
      getJSON("/api/topology").catch(() => null),
      getJSON("/api/pods").catch(() => null),
      getJSON("/api/pod-resources").catch(() => null),
      getJSON("/api/plant").catch(() => null),
      getJSON("/api/tags").catch(() => null),
      getJSON("/api/fleet").catch(() => null),
      getJSON("/api/actions").catch(() => null),
      getJSON("/api/scenarios").catch(() => null),
    ]);
    if (g) setGraph(g); if (n) setNarr(n); if (h) setHealth(h);
    if (t) setTopo(t); if (p) setPods(p); if (pr) setPodres(pr); if (pl) setPlant(pl);
    if (tg) setScada(tg);
    if (tg && tg.source === "scada") setTagsOkAt(new Date());
    if (fl) setFleet(fl); if (ac) setActions(ac);
    if (Array.isArray(sc)) setScenarios(sc);
    if (miss.length) setFeedErr({ what: miss.join(", "), at: new Date() });
    else { setFeedErr(null); setUpdated(new Date()); }
  }
  const loadRecs = () => getJSON("/api/recommendations").then(setRecs).catch(() => {});
  // 2E audit trail: every fire, reset, and denied attempt lands here from the hash-chained ledger.
  // 10 s, so the 3D relief row (written about 60 s after an Execute) shows up promptly.
  const loadAudit = () => getJSON("/api/audit").then(setAudit).catch(() => {});

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    loadRecs();
    const t = setInterval(loadRecs, 30000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    loadAudit();
    const t = setInterval(loadAudit, 10000);
    return () => clearInterval(t);
  }, []);
  // 2H: the task library and the profiles change only with a deploy, so load them once
  useEffect(() => {
    getJSON("/api/fleet/tasks").then((x) => setTasks(Array.isArray(x) ? x : [])).catch(() => {});
    getJSON("/api/fleet/profiles").then((x) => setProfiles(Array.isArray(x) ? x : [])).catch(() => {});
  }, []);

  // After a fleet or act-loop change, pull the truth now instead of waiting for the 5 s tick.
  function fleetChanged() {
    getJSON("/api/fleet").then(setFleet).catch(() => {});
    getJSON("/api/actions").then(setActions).catch(() => {});
    getJSON("/api/plant").then(setPlant).catch(() => {});
    loadAudit();
  }

  // Dev review switch: reload every source after the mock state changes.
  function reloadAll() { refresh(); loadRecs(); loadAudit(); }

  // Fire or reset one scenario. The button state comes from the sim's real active_faults, so it
  // stays correct across reloads. Nothing is set optimistically.
  async function scenario(sid, action) {
    setPending((p) => ({ ...p, [sid]: true }));
    setFired(`${action === "reset" ? "resetting" : "firing"} ${sid}…`);
    // the catalogue says how long each fault takes to show (PS2 waits for the relay, PS6 for a leak)
    const expect = (scenarios || []).find((s) => s.id === sid)?.expect_s;
    try {
      const r = await fetch(`/api/scenarios/${sid}/${action}`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      const why = r.status === 401 ? "operator login required" : j.detail || j.status || r.statusText || "";
      setFired(r.ok
        ? `${sid} ${action === "reset" ? "reset · the floor clears in about 5 s, the verdict in 1 to 3 min" : `fired · expect the verdict in about ${expect || 90} s`} (${new Date().toLocaleTimeString()})`
        : `error ${r.status}: ${why}`);
      getJSON("/api/plant").then(setPlant).catch(() => {});
      getJSON("/api/scenarios").then((x) => Array.isArray(x) && setScenarios(x)).catch(() => {});
    } catch (e) {
      setFired("error: " + e);
    } finally {
      setPending((p) => ({ ...p, [sid]: false }));
    }
  }

  // Full reset of every fault owner (SCENARIOS.md 5.1): plant-sim, the rogue-ews injector, and the
  // tag server's leak. The API writes one audit row for it, so this recovers any unknown state.
  async function resetAll() {
    setPending((p) => ({ ...p, __all: true }));
    setFired("resetting every fault owner…");
    try {
      const r = await fetch(`/api/scenarios/reset-all`, { method: "POST" });
      const j = await r.json().catch(() => ({}));
      const why = r.status === 401 ? "operator login required" : typeof j.detail === "string" ? j.detail : "";
      setFired(r.ok
        ? `all faults reset (${new Date().toLocaleTimeString()})`
        : `reset error ${r.status}: ${why}`);
      getJSON("/api/plant").then(setPlant).catch(() => {});
      getJSON("/api/scenarios").then((x) => Array.isArray(x) && setScenarios(x)).catch(() => {});
    } catch (e) {
      setFired("reset error: " + e);
    } finally {
      setPending((p) => ({ ...p, __all: false }));
    }
  }

  // ── derive ──────────────────────────────────────────────────────────────
  const meta = graph?.meta || {};
  const root = graph?.root?.[0] || null;
  const edges = graph?.edges || [];
  const blast = graph?.blast_radius || [];
  const blastSet = new Set(blast.map((b) => b.pod));
  const rootEdge = root
    ? [...edges.filter((e) => e.src === root.pod)].sort((a, b) => Math.abs(b.r || 0) - Math.abs(a.r || 0))[0] || null
    : null;
  const resWord = RES_WORD[meta.signal] || "resource";
  // The verdict role of an asset or pod: root cause, or the warning role for the blast radius and
  // for an incipient forecast (a limit is near). The map uses the same roles.
  const incipSet = new Set((graph?.incipient || []).map((f) => f.pod));
  const statusOf = (w) => (w === root?.pod ? "hot" : blastSet.has(w) || incipSet.has(w) ? "strained" : null);

  // FLOOR and EDGE causal planes (LOG-036): one brain, two views. FLOOR is the static plant floor
  // (a factory is statically wired, so the graph pre-exists and runtime only weights it). EDGE is
  // the pod plane, where the topology really is discovered. Domain witnesses are default-deny, so
  // cross-plane edges cannot exist, and EDGE is everything not fully inside the floor.
  const segs = plant?.segments || {};
  const plantSet = new Set([
    ...Object.keys(plant?.devices || {}),
    ...Object.keys(plant?.rails || {}),
    ...(plant?.loop?.name ? [plant.loop.name] : []),
    ...Object.keys(segs),                                           // field-1 and its members (PS3)
    ...Object.values(segs).flatMap((s) => Object.keys(s.members || {})),
  ]);
  const inFloor = (e) => plantSet.has(e.src) && plantSet.has(e.dst);

  // The causal chain as hops: root, the medium it crossed, the next machine, and so on. PS2 crosses
  // two media (compressor-1 → rail psu-b → chiller-1 → loop cool-1 → the cooled machines). A hop
  // follows the edge to a machine that has its own outgoing edges. The rest are the victims.
  const mediaNames = new Set([...Object.keys(plant?.rails || {}), ...(plant?.loop?.name ? [plant.loop.name] : []), ...Object.keys(segs)]);
  const mediumOf = (e) => {
    const ev = e.evidence || [];
    if (mediaNames.has(e.dst)) return `${plant?.rails?.[e.dst] ? "rail" : segs[e.dst] ? "segment" : "loop"} ${e.dst}`;
    if (ev.includes("rail")) { const r = plant?.devices?.[e.src]?.rail; return r ? `rail ${r}` : "rail"; }
    if (ev.includes("loop")) return `loop ${plant?.loop?.name || ""}`.trim();
    if (ev.includes("net")) {
      const seg = Object.entries(segs).find(([, s]) => s.members?.[e.src] && s.members?.[e.dst]);
      return seg ? `segment ${seg[0]}` : "segment";
    }
    return RES_WORD[e.signal] || null;
  };
  const chain = [];
  if (root) {
    const out = (n) => edges.filter((e) => e.src === n && !mediaNames.has(e.dst));
    let cur = root.pod;
    const seen = new Set([cur]);
    for (let hop = 0; hop < 3; hop++) {
      const nexts = out(cur).filter((e) => !seen.has(e.dst));
      const relay = nexts.filter((e) => out(e.dst).some((x) => !seen.has(x.dst) && x.dst !== cur))
        .sort((a, b) => Math.abs(b.r || 0) - Math.abs(a.r || 0))[0];
      if (!relay) break;
      chain.push({ medium: mediumOf(relay), node: relay.dst });
      seen.add(relay.dst);
      cur = relay.dst;
    }
    const last = [...out(cur)].sort((a, b) => Math.abs(b.r || 0) - Math.abs(a.r || 0))[0];
    if (last) chain.push({ medium: mediumOf(last), node: null });
  }
  const chainNodes = new Set([root?.pod, ...chain.map((h) => h.node).filter(Boolean)]);
  const integrity = graph?.integrity || [];
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
  const stRank = { hot: 0, strained: 1, ok: 2 };
  const podRows = (pods || []).map((p) => {
    const r = resByWl[p.workload] || {};
    return {
      name: p.workload, ns: p.namespace, st: statusOf(p.workload) || (p.anomalous ? "strained" : "ok"),
      cpuUse: r.cpuUse, cpuReq: r.cpuReq, cpuLim: r.cpuLim, memUse: r.memUse, memReq: r.memReq, memLim: r.memLim,
      cpuPct: pctOf(r.cpuUse, r.cpuReq, r.cpuLim), memPct: pctOf(r.memUse, r.memReq, r.memLim), ioPct: p.value,
    };
  }).sort((a, b) => stRank[a.st] - stRank[b.st]);   // root first, then blast radius, then steady

  // Advisory cards. An executable act-loop proposal replaces the advisory throttle for its asset.
  const advisory = [];
  const executable = new Set((actions?.proposals || []).map((p) => p.asset));
  if (root && rootEdge && !executable.has(root.pod)) {
    advisory.push({
      kind: "act", verb: "throttle", name: rootEdge.src || root.pod,
      detail: `Sources the ${resWord} contention. Throttle it to relieve ${rootEdge.dst || "downstream"}.`,
      cites: `edge ${rootEdge.src}→${rootEdge.dst} · ${(rootEdge.evidence || []).join("+")}`,
    });
  }
  for (const c of recs?.right_sizing || []) {
    const reclaim = c.verb === "reclaim";
    advisory.push({
      kind: reclaim ? "good" : "act", verb: reclaim ? "reclaim" : "resize ↑", name: c.workload, detail: c.detail,
      cites: `p95 ${c.resource || ""} vs ${reclaim ? "request" : "limit"} · 1 h window`,
    });
  }
  // The API already scopes fairness to the live namespaces (aiops, observability, plant)
  const nsGinis = (recs?.fairness || []).map((f) => f.gini);
  const fairness = nsGinis.length ? 1 - nsGinis.reduce((a, b) => a + b, 0) / nsGinis.length : null;

  return {
    graph, narr, health, topo, pods, podres, plant, recs, audit, scada, fleet, actions, tasks, profiles,
    pending, fired, updated,
    scenario, resetAll, fleetChanged, reloadAll,
    scenarios, feedErr, now, tagsOkAt,
    derived: { meta, root, rootEdge, edges, blast, blastSet, resWord, statusOf, plantSet, edgeGraph, podRows, advisory, fairness,
      chain, chainNodes, mediaNames, integrity },
  };
}
