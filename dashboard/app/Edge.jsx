"use client";
import Glyph from "./Glyph";
import { meterColor } from "./lib/palette";
import { milli, mib } from "./lib/format";

// Edge: the hosting plane. These workloads run the AIOps engine and SCADA on the box, and the
// same engine watches them with real kernel telemetry. Cards rank root first, then the blast
// radius, then steady. Allocations show inline, because a hover popover would clip inside a
// scrolling tab body.
function Gauge({ label, value }) {
  const r = 20, C = 2 * Math.PI * r;
  const v = value == null ? 0 : Math.max(0, Math.min(1, value));
  return (
    <div className="gauge">
      <svg width="50" height="50" viewBox="0 0 50 50">
        <circle cx="25" cy="25" r={r} fill="none" stroke="rgba(210,214,228,0.12)" strokeWidth="5" />
        <circle cx="25" cy="25" r={r} fill="none" stroke={meterColor(value)} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={C} strokeDashoffset={C * (1 - v)} transform="rotate(-90 25 25)" />
        <text x="25" y="26" textAnchor="middle" dominantBaseline="middle"
          style={{ fontFamily: "var(--display)", fontWeight: 600, fontSize: "12px", fill: "var(--text)" }}>
          {value == null ? "—" : Math.round(v * 100) + "%"}
        </text>
      </svg>
      <div className="glabel">{label}</div>
    </div>
  );
}

export default function Edge({ podRows, fairness }) {
  return (
    <div className="edge">
      <div className="edge-bar">
        <span className="lbl">edge node · aiops and scada workloads · real telemetry</span>
        {fairness != null && (
          <span className="fair" title="1 - mean Gini of pressure stall across the live namespaces">
            <span className="lbl">namespace fairness</span>
            <span className="bar"><i style={{ width: `${Math.round(fairness * 100)}%` }} /></span>
            <b>{fairness.toFixed(2)}</b>
          </span>
        )}
      </div>
      {podRows.length ? (
        <div className="pods">
          {podRows.map((n) => (
            <div key={n.name} className={`pod ${n.st}`}>
              <div className="who"><Glyph st={n.st} /><span className="nm">{n.name}</span><span className="ns">{n.ns}</span></div>
              <div className="gauges">
                <Gauge label="CPU" value={n.cpuPct} />
                <Gauge label="MEM" value={n.memPct} />
                <Gauge label="I/O" value={n.ioPct} />
              </div>
              <div className="alloc">
                <span>cpu</span><b>{milli(n.cpuUse)}</b><i>{milli(n.cpuReq)} req · {milli(n.cpuLim)} lim</i>
                <span>mem</span><b>{mib(n.memUse)}</b><i>{mib(n.memReq)} req · {mib(n.memLim)} lim</i>
              </div>
            </div>
          ))}
        </div>
      ) : <div className="empty">waiting for telemetry…</div>}
    </div>
  );
}
