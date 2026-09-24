"use client";
import Glyph from "./Glyph";
import { meterColor } from "./lib/palette";
import { milli, mib } from "./lib/format";

// Edge: the hosting plane. These workloads run the AIOps engine and SCADA on the box, and the
// same engine watches them with real kernel telemetry. The cards stay in fixed places (LOG-083):
// grouped by role in a fixed group order, then by name. A state change colors the card edge and
// the glyph, and never moves the card.
//
// A gauge shows use as a share of the limit. With no limit it shows the share of the request, and
// that gauge turns amber above 100 % but never red, because no limit can stop the workload. With
// neither (the Helm charts of the observability stack set none), the gauge shows the absolute use
// and "no quota" instead of a blank.
const GROUPS = [
  { ns: "aiops", title: "AIOps engine" },
  { ns: "plant", title: "Plant and SCADA" },
  { ns: "fleet", title: "PLC fleet" },
  { ns: "observability", title: "Observability" },
];

function Gauge({ label, frac, soft, abs }) {
  const r = 20, C = 2 * Math.PI * r;
  const v = frac == null ? 0 : Math.max(0, Math.min(1, frac));
  const color = frac == null ? "transparent" : soft ? (frac >= 1 ? "var(--amber)" : "var(--teal)") : meterColor(frac);
  const text = frac != null ? `${Math.round(frac * 100)}%` : abs || "—";
  return (
    <div className="gauge">
      <svg width="50" height="50" viewBox="0 0 50 50">
        <circle cx="25" cy="25" r={r} fill="none" stroke="rgba(210,214,228,0.12)" strokeWidth="5" />
        <circle cx="25" cy="25" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={C} strokeDashoffset={C * (1 - v)} transform="rotate(-90 25 25)" />
        <text x="25" y="26" textAnchor="middle" dominantBaseline="middle"
          style={{ fontFamily: "var(--display)", fontWeight: 600, fontSize: text.length > 4 ? "10px" : "12px", fill: frac == null ? "var(--text-weak)" : "var(--text)" }}>
          {text}
        </text>
      </svg>
      <div className="glabel">{label}</div>
    </div>
  );
}

// use / limit, else use / request (soft), else no share
const share = (use, req, lim) => (lim ? { frac: (use || 0) / lim } : req ? { frac: (use || 0) / req, soft: true } : { frac: null });
const quota = (req, lim, fmt) => [req ? `${fmt(req)} req` : null, lim ? `${fmt(lim)} lim` : null].filter(Boolean).join(" · ") || "no quota";

function PodCard({ n }) {
  const has = n.cpuUse != null || n.memUse != null;
  return (
    <div className={`pod ${n.st}`}>
      <div className="who"><Glyph st={n.st} /><span className="nm">{n.name}</span></div>
      <div className="gauges">
        <Gauge label="CPU" {...share(n.cpuUse, n.cpuReq, n.cpuLim)} abs={n.cpuUse ? milli(n.cpuUse) : null} />
        <Gauge label="MEM" {...share(n.memUse, n.memReq, n.memLim)} abs={n.memUse ? mib(n.memUse).replace("MiB", "Mi") : null} />
        <Gauge label="I/O" frac={n.ioPct} />
      </div>
      {has ? (
        <div className="alloc">
          <span>cpu</span><b>{milli(n.cpuUse)}</b><i>{quota(n.cpuReq, n.cpuLim, milli)}</i>
          <span>mem</span><b>{mib(n.memUse)}</b><i>{quota(n.memReq, n.memLim, mib)}</i>
        </div>
      ) : <div className="alloc-none">no resource data</div>}
    </div>
  );
}

export default function Edge({ podRows, fairness }) {
  const known = new Set(GROUPS.map((g) => g.ns));
  const others = [...new Set(podRows.map((n) => n.ns).filter((ns) => !known.has(ns)))].sort();
  const groups = [...GROUPS, ...others.map((ns) => ({ ns, title: ns }))]
    .map((g) => ({ ...g, rows: podRows.filter((n) => n.ns === g.ns).sort((a, b) => a.name.localeCompare(b.name)) }))
    .filter((g) => g.rows.length);
  return (
    <div className="edge">
      <div className="edge-bar">
        <span className="lbl">edge node</span>
        {fairness != null && (
          <span className="fair" title="1 - mean Gini of pressure stall across the live namespaces">
            <span className="lbl">fairness</span>
            <span className="bar"><i style={{ width: `${Math.round(fairness * 100)}%` }} /></span>
            <b>{fairness.toFixed(2)}</b>
          </span>
        )}
      </div>
      {podRows.length ? groups.map((g) => (
        <section key={g.ns} className="pod-grp">
          <div className="pod-grp-h"><span className="brk">{g.title}</span><i>{g.rows.length}</i></div>
          <div className="pods">
            {g.rows.map((n) => <PodCard key={n.name} n={n} />)}
          </div>
        </section>
      )) : <div className="empty">waiting for telemetry…</div>}
    </div>
  );
}
