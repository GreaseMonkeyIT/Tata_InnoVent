"use client";
import { useEffect, useRef, useState } from "react";

// Live "allocated vs consuming" per pod. Polls /api/pod-resources (same origin via nginx -> the
// in-cluster API -> Prometheus; no CORS, no port-forward) and keeps a client-side ring buffer per
// pod so the sparkline is a window that scrolls with time.
const WINDOW = 60;          // samples kept per pod (~5 min at the 5s poll)
const TRACK = "#2a2d34";    // bar track (= the limit); not in globals.css

function fmtCpu(c) {
  if (c == null) return "—";
  return c < 1 ? Math.round(c * 1000) + "m" : Math.round(c * 100) / 100 + " CPU";
}
function fmtMem(b) {
  if (b == null) return "—";
  const u = ["B", "Ki", "Mi", "Gi", "Ti"];
  let i = 0, v = Math.abs(b);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + " " + u[i];
}
const fmt = { cpu: fmtCpu, mem: fmtMem };

// bar scale-max: the limit if set, else max(request, peak usage) * 1.15
function scaleMax(m, hist) {
  if (m.limit) return m.limit;
  const peak = Math.max(m.request || 0, m.usage || 0, ...(hist.length ? hist : [0]));
  return peak > 0 ? peak * 1.15 : 1;
}
function ratioColor(r) {
  if (r >= 0.9) return "var(--red)";
  if (r >= 0.7) return "var(--orange)";
  return "var(--green)";
}

function Spark({ hist, max, color }) {
  const W = 120, H = 30, n = hist.length;
  if (n < 2) return <svg width={W} height={H} style={{ flex: "none" }} />;
  const mx = max > 0 ? max : Math.max(...hist, 1);
  const xs = (i) => (i / (n - 1)) * (W - 2) + 1;
  const ys = (v) => H - 2 - Math.min(1, v / mx) * (H - 4);
  let line = "";
  let area = `M${xs(0).toFixed(1)} ${H} `;
  hist.forEach((v, i) => {
    const x = xs(i).toFixed(1), y = ys(v).toFixed(1);
    line += (i ? "L" : "M") + x + " " + y + " ";
    area += "L" + x + " " + y + " ";
  });
  area += `L${xs(n - 1).toFixed(1)} ${H} Z`;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ flex: "none" }}>
      <path d={area} fill={color} opacity="0.18" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

function Metric({ label, kind, m, hist }) {
  const f = fmt[kind];
  const use = m.usage || 0;
  const max = scaleMax(m, hist);
  const denom = m.limit || max;
  const ratio = denom > 0 ? use / denom : 0;
  const color = ratioColor(ratio);
  const usePct = Math.min(100, (use / max) * 100);
  const reqPct = m.request != null ? Math.min(100, (m.request / max) * 100) : null;
  const pctTxt = m.limit ? Math.round((use / m.limit) * 100) + "%"
    : m.request ? Math.round((use / m.request) * 100) + "%" : "—";
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, fontSize: 11, color: "var(--text-weak)", marginBottom: 3 }}>
        <span style={{ color: "var(--text)", fontWeight: 600, width: 34 }}>{label}</span>
        <span style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>{f(use)}</span>
        <span style={{ color: "var(--text-faint)" }}>used</span>
        <span style={{ marginLeft: "auto", color: "var(--text-faint)", fontVariantNumeric: "tabular-nums" }}>
          req {f(m.request)} · lim {f(m.limit)}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ position: "relative", flex: 1, height: 14, background: TRACK, borderRadius: 3, overflow: "hidden" }}>
          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: usePct.toFixed(1) + "%", background: color, borderRadius: "3px 0 0 3px", transition: "width .4s, background .4s" }} />
          {reqPct != null && <div style={{ position: "absolute", top: -2, bottom: -2, left: reqPct.toFixed(1) + "%", width: 2, background: "var(--purple)" }} />}
        </div>
        <div style={{ width: 42, textAlign: "right", fontSize: 11, color: "var(--text-weak)", fontVariantNumeric: "tabular-nums" }}>{pctTxt}</div>
        <Spark hist={hist} max={m.limit || max} color={color} />
      </div>
    </div>
  );
}

export default function PodResources() {
  const [pods, setPods] = useState(null);
  const [src, setSrc] = useState(null);
  const [sort, setSort] = useState("name");
  const histRef = useRef(new Map()); // "ns/pod" -> { cpu:[], mem:[] }

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await fetch("/api/pod-resources", { cache: "no-store" });
        if (!r.ok) throw new Error(r.status);
        const j = await r.json();
        if (!alive) return;
        const seen = new Set();
        for (const p of j.pods || []) {
          const k = p.namespace + "/" + p.pod;
          seen.add(k);
          let h = histRef.current.get(k);
          if (!h) { h = { cpu: [], mem: [] }; histRef.current.set(k, h); }
          h.cpu.push(p.cpu?.usage || 0); if (h.cpu.length > WINDOW) h.cpu.shift();
          h.mem.push(p.mem?.usage || 0); if (h.mem.length > WINDOW) h.mem.shift();
        }
        for (const k of [...histRef.current.keys()]) if (!seen.has(k)) histRef.current.delete(k);
        setPods(j.pods || []);
        setSrc(j.source);
      } catch (e) { /* keep last good values */ }
    }
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (pods == null) return <div style={{ color: "var(--text-faint)" }}>loading…</div>;
  if (src === "unavailable") return <div style={{ color: "var(--text-faint)" }}>Prometheus unavailable</div>;
  if (pods.length === 0) return <div style={{ color: "var(--text-faint)" }}>no pods</div>;

  const pctOf = (m, hist) => { const d = m.limit || scaleMax(m, hist); return d > 0 ? (m.usage || 0) / d : 0; };
  const arr = [...pods].sort((a, b) => {
    const ha = histRef.current.get(a.namespace + "/" + a.pod) || { cpu: [], mem: [] };
    const hb = histRef.current.get(b.namespace + "/" + b.pod) || { cpu: [], mem: [] };
    if (sort === "cpu") return pctOf(b.cpu, hb.cpu) - pctOf(a.cpu, ha.cpu);
    if (sort === "mem") return pctOf(b.mem, hb.mem) - pctOf(a.mem, ha.mem);
    return (a.namespace + a.pod).localeCompare(b.namespace + b.pod);
  });

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <label style={{ fontSize: 11, color: "var(--text-weak)", display: "flex", alignItems: "center", gap: 6 }}>
          sort
          <select value={sort} onChange={(e) => setSort(e.target.value)}
            style={{ background: "#0e0f13", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 3, padding: "4px 6px", fontSize: 12 }}>
            <option value="name">name</option>
            <option value="cpu">CPU %</option>
            <option value="mem">MEM %</option>
          </select>
        </label>
      </div>
      <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))" }}>
        {arr.map((p) => {
          const k = p.namespace + "/" + p.pod;
          const h = histRef.current.get(k) || { cpu: [], mem: [] };
          const parts = p.pod.split("-");
          const hash = parts.length > 2 ? parts.slice(-2).join("-") : "";
          return (
            <div key={k} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: "10px 12px" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
                <span style={{ fontWeight: 600 }}>{p.workload}</span>
                {hash && <span style={{ color: "var(--text-faint)", fontSize: 11, fontFamily: "ui-monospace, Menlo, Consolas, monospace" }}>{hash}</span>}
                <span className="chip" style={{ marginLeft: "auto" }}>{p.namespace}</span>
              </div>
              <Metric label="CPU" kind="cpu" m={p.cpu} hist={h.cpu} />
              <Metric label="MEM" kind="mem" m={p.mem} hist={h.mem} />
            </div>
          );
        })}
      </div>
    </>
  );
}
