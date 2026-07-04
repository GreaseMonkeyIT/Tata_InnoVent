"use client";
import { useMemo, useRef, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import SpriteText from "three-spritetext";

// react-force-graph-3d is three.js/WebGL, so it renders client-only.
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

// Node roles encode meaning: the source, the affected victims, everyone else.
const ROLE = {
  root: { color: "#f2495c", val: 10 }, // red   — source / root cause
  victim: { color: "#ff9830", val: 6 }, // amber — affected pod
  normal: { color: "#5dcaa5", val: 3 }, // teal  — other workload
};

// Edge contention -> colour: pale grey at low, bright orange-red at max.
function edgeColor(w) {
  const t = Math.min(Math.max(w ?? 0, 0), 1);
  const lo = [150, 158, 170];
  const hi = [255, 80, 20];
  const c = lo.map((v, i) => Math.round(v + (hi[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export default function Graph({ graph, topo }) {
  const fgRef = useRef();
  const wrapRef = useRef();
  const nodeCache = useRef(new Map());
  const sigRef = useRef("");
  const dataRef = useRef({ nodes: [], links: [] });
  const fitted = useRef(false);
  const prevCount = useRef(-1);
  const [size, setSize] = useState({ w: 800, h: 440 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Perf: the force graph runs its own rAF render loop forever. Pause it whenever the panel
  // is scrolled out of view (resume on return) — page scroll never contends with idle WebGL.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      const fg = fgRef.current;
      if (!fg) return;
      if (e?.isIntersecting) fg.resumeAnimation?.();
      else fg.pauseAnimation?.();
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Optimization: rebuild graphData only when something VISUAL changes (roles / edge state /
  // contention bucket). Steady state returns the same object (no per-poll re-heat); a storm makes
  // new node objects (seeded with the last position) so three.js rebuilds them with new colours.
  const data = useMemo(() => {
    const causalEdges = graph?.edges || [];
    const netEdges = topo?.edges || [];          // eBPF-discovered topology backbone
    const root = graph?.root?.[0]?.pod;
    const victims = new Set((graph?.blast_radius || []).map((b) => b.pod));
    const roleOf = (id) => (id === root ? "root" : victims.has(id) ? "victim" : "normal");

    const ids = new Set();
    causalEdges.forEach((e) => { ids.add(e.src); ids.add(e.dst); });
    (graph?.findings || []).forEach((f) => ids.add(f.pod));
    netEdges.forEach((e) => { ids.add(e.src); ids.add(e.dst); });

    const pairKey = (a, b) => a + "" + b;
    const causalSet = new Set(causalEdges.map((e) => pairKey(e.src, e.dst)));
    const causalRows = causalEdges.map((e) => ({
      src: e.src, dst: e.dst, kind: "causal",
      hot: e.state === "active" || e.state === "confirming",
      w: e.render_weight ?? e.confidence ?? Math.abs(e.r || 0),
      label: `${(e.evidence || []).join("+")}${e.r != null ? ` · r=${e.r}` : ""}`,
    }));
    // discovered network backbone (thin grey), minus any pair already drawn as a causal edge
    const netRows = netEdges
      .filter((e) => !causalSet.has(pairKey(e.src, e.dst)) && !causalSet.has(pairKey(e.dst, e.src)))
      .map((e) => ({ src: e.src, dst: e.dst, kind: "net", hot: false, w: 0, label: e.label || `:${e.port || "?"}` }));
    const rows = [...causalRows, ...netRows];

    // Re-layout only when something STRUCTURAL changes: roles, causal edge state/weight-bucket, or
    // the network edge SET (NOT its byte counts) -> the topology no longer re-heats every 5s poll.
    const sig = JSON.stringify([
      [...ids].map((id) => [id, roleOf(id)]).sort(),
      causalRows.map((l) => [l.src, l.dst, l.hot, Math.round((l.w || 0) * 8)]).sort(),
      netRows.map((l) => [l.src, l.dst]).sort(),
    ]);
    if (sig === sigRef.current) return dataRef.current;
    sigRef.current = sig;

    const cache = nodeCache.current;
    const nodes = [...ids].map((id) => {
      const prev = cache.get(id);
      const n = { id, role: roleOf(id) };
      if (prev) { n.x = prev.x; n.y = prev.y; n.z = prev.z; } // keep layout across the change
      cache.set(id, n);
      return n;
    });
    for (const id of [...cache.keys()]) if (!ids.has(id)) cache.delete(id);
    const links = rows.map((l) => ({ source: l.src, target: l.dst, kind: l.kind, hot: l.hot, w: l.w, label: l.label }));
    dataRef.current = { nodes, links };
    return dataRef.current;
  }, [graph, topo]);

  // Fit the camera once the layout settles (re-fit only when the node set changes), so it never
  // opens zoomed-out and doesn't yank a camera you've rotated. A timeout backs up onEngineStop.
  useEffect(() => {
    const n = data.nodes.length;
    if (n && n !== prevCount.current) {
      prevCount.current = n;
      fitted.current = false;
      const t = setTimeout(() => {
        if (!fitted.current && fgRef.current) { fgRef.current.zoomToFit(500, 50); fitted.current = true; }
      }, 1200);
      return () => clearTimeout(t);
    }
  }, [data]);

  return (
    <div ref={wrapRef} style={{ width: "100%", height: "100%" }}>
      <ForceGraph3D
        ref={fgRef}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="#181b1f"
        showNavInfo={false}
        onEngineStop={() => {
          if (!fitted.current && fgRef.current) { fgRef.current.zoomToFit(500, 50); fitted.current = true; }
        }}
        nodeRelSize={4}
        nodeVal={(n) => ROLE[n.role].val}
        nodeColor={(n) => ROLE[n.role].color}
        nodeOpacity={0.95}
        nodeResolution={18}
        nodeThreeObjectExtend={true}
        nodeThreeObject={(n) => {
          const s = new SpriteText(n.id);
          s.color = "#e6e6ea";
          s.textHeight = 3.5;
          const r = Math.cbrt(ROLE[n.role].val) * 4;
          s.position.set(0, -(r + 3), 0);
          return s;
        }}
        linkColor={(l) => (l.kind === "net" ? "#4b5159" : edgeColor(l.w))}
        linkWidth={(l) => (l.kind === "net" ? 0.4 : 0.8 + l.w * 3)}
        linkOpacity={0.6}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(l) => (l.hot ? 4 : 0)}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleColor={(l) => edgeColor(l.w)}
        linkLabel={(l) => l.label}
      />
    </div>
  );
}
