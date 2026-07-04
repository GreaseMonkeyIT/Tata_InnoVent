"use client";
// INTERIM SVG floor (LOG-036) — superseded by the true-3D Floor.jsx (LOG-038). Kept UNIMPORTED
// as the one-line-swap fallback: import Floor from "./Floor2D" in page.jsx if the 3D scene misbehaves.
// A factory is statically wired: the causal graph exists from the get-go (the declared shared
// media = PLANT_DOMAINS), and the runtime pass only CONFIGURES ITS WEIGHTS. So the floor draws
// fixed geometry — isometric cuboids standing on one ground plane, overhead bus bars with 90°
// drops, a coolant run in front — and the live verdict renders ON those fixed wires (root red,
// victims amber, dashes marching src → dst). No force layout; nothing floats or rearranges.
import { useMemo } from "react";

// LOD-1 footprint classes by asset kind (cosmetic sizing only; unknown kinds render medium).
const SIZE = [
  [/^press/, { w: 88, h: 74 }],
  [/^furnace/, { w: 90, h: 62 }],
  [/^cnc/, { w: 78, h: 56 }],
  [/^conveyor/, { w: 104, h: 24 }],
  [/^compressor/, { w: 72, h: 48 }],
  [/^chiller/, { w: 74, h: 52 }],
  [/scanner|^qa/, { w: 48, h: 40 }],
];
const sizeOf = (n) => (SIZE.find(([re]) => re.test(n)) || [null, { w: 72, h: 50 }])[1];

// Same contention ramp as Graph.jsx: pale grey at low weight, orange-red at max.
function edgeColor(w) {
  const t = Math.min(Math.max(w ?? 0, 0), 1);
  const lo = [150, 158, 170], hi = [255, 80, 20];
  const c = lo.map((v, i) => Math.round(v + (hi[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

const DX = 16, DY = 10;          // isometric depth
const FY = 320;                  // the single ground plane (every cuboid's front-bottom)
const BUS_Y = 70;                // overhead busway
const PIPE_Y = 430;              // coolant run (foreground trench)
const GAP = 46, PSU_W = 34, GROUP_GAP = 78;

function Cuboid({ x, w, h, st, tripped }) {
  const yT = FY - h;
  const stroke = tripped ? "var(--red)" : st === "hot" ? "var(--red)" : st === "strained" ? "var(--orange)" : "rgba(93,202,165,0.5)";
  const front = st === "hot" ? "rgba(242,73,92,0.10)" : st === "strained" ? "rgba(255,152,48,0.08)" : "rgba(204,204,220,0.03)";
  return (
    <g>
      <polygon points={`${x},${yT} ${x + DX},${yT - DY} ${x + w + DX},${yT - DY} ${x + w},${yT}`}
        fill="rgba(204,204,220,0.07)" stroke={stroke} strokeWidth="1" strokeDasharray={tripped ? "4 3" : "none"} />
      <polygon points={`${x + w},${yT} ${x + w + DX},${yT - DY} ${x + w + DX},${FY - DY} ${x + w},${FY}`}
        fill="rgba(204,204,220,0.045)" stroke={stroke} strokeWidth="1" strokeDasharray={tripped ? "4 3" : "none"} />
      <rect x={x} y={yT} width={w} height={h} fill={front} stroke={stroke} strokeWidth="1.2"
        strokeDasharray={tripped ? "4 3" : "none"} />
      {tripped && (
        <text x={x + w / 2} y={yT + h / 2 + 4} textAnchor="middle" fill="var(--red)"
          style={{ font: "600 11px var(--mono)", letterSpacing: 1 }}>OPEN</text>
      )}
    </g>
  );
}

export default function Floor({ plant, graph }) {
  const geo = useMemo(() => {
    const rails = Object.keys(plant?.rails || {});
    const devs = Object.entries(plant?.devices || {});
    if (!rails.length || !devs.length) return null;

    // fixed layout: one group per rail — PSU block, then that rail's machines, wide gaps
    const pos = {}, railGeom = {};
    let x = 46;
    for (const r of rails) {
      const members = devs.filter(([, d]) => d.rail === r).map(([n]) => n);
      const psuX = x;
      x += PSU_W + 30;
      for (const n of members) {
        const s = sizeOf(n);
        pos[n] = { x, ...s, cx: x + s.w / 2, dropX: x + s.w / 2 + DX / 2, topY: FY - s.h - DY };
        x += s.w + GAP;
      }
      railGeom[r] = { psuX, psuCx: psuX + PSU_W / 2 + DX / 2, x1: x - GAP + 10, members };
      x += GROUP_GAP;
    }
    const loopName = plant?.loop?.name || null;
    const cooled = devs.filter(([, d]) => d.cooled).map(([n]) => n);
    const pumpX = 24;
    return { rails, devs, pos, railGeom, loopName, cooled, pumpX, width: Math.max(1080, x - GROUP_GAP + 20) };
  }, [plant]);

  if (!geo) return <div style={{ color: "var(--text-faint)", padding: 24 }}>waiting for plant telemetry…</div>;

  const { rails, devs, pos, railGeom, loopName, cooled, pumpX, width } = geo;
  const root = graph?.root?.[0]?.pod;
  const victims = new Set((graph?.blast_radius || []).map((b) => b.pod));
  const stOf = (n) => (n === root ? "hot" : victims.has(n) ? "strained" : "ok");
  const trippedOf = (n) => !!plant?.devices?.[n]?.tripped;

  // wire anchors — every causal edge renders along the SAME fixed wires the skeleton draws
  const railAnchor = (n) => (pos[n] ? { x: pos[n].dropX, y: pos[n].topY } : railGeom[n] ? { x: railGeom[n].psuCx, y: BUS_Y } : null);
  const pipeAnchor = (n) => (pos[n] ? { x: pos[n].cx, y: FY } : n === loopName ? { x: pumpX + 17, y: PIPE_Y } : null);
  const pathFor = (e) => {
    if (e.signal === "coolant_temp") {
      const a = pipeAnchor(e.src), b = pipeAnchor(e.dst);
      return a && b ? `M ${a.x} ${a.y} V ${PIPE_Y} H ${b.x} V ${b.y}` : null;
    }
    const a = railAnchor(e.src), b = railAnchor(e.dst);
    return a && b ? `M ${a.x} ${a.y} V ${BUS_Y} H ${b.x} V ${b.y}` : null;
  };
  const live = (graph?.edges || []).filter((e) => e.state === "active" || e.state === "confirming");

  return (
    <div className="floor">
      <svg viewBox={`0 0 ${width} 500`} xmlns="http://www.w3.org/2000/svg">
        <defs>
          <marker id="flarrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="9" markerHeight="9"
            markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M 0 0 L 8 4 L 0 8 z" fill="context-stroke" />
          </marker>
        </defs>

        {/* ground plane */}
        <line x1="16" y1={FY} x2={width - 16} y2={FY} stroke="rgba(204,204,220,0.14)" strokeWidth="1.5" />
        <line x1={16 + DX} y1={FY - DY} x2={width - 16 + DX} y2={FY - DY} stroke="rgba(204,204,220,0.05)" strokeWidth="1" />

        {/* coolant run (static skeleton): pump block + trench pipe + 90° risers to cooled machines */}
        {loopName && (
          <g>
            <line x1={pumpX + 17} y1={PIPE_Y} x2={Math.max(...cooled.map((n) => pos[n]?.cx || 0)) || pumpX + 17} y2={PIPE_Y}
              stroke="rgba(54,197,224,0.28)" strokeWidth="2.5" />
            {cooled.map((n) => pos[n] && (
              <line key={n} x1={pos[n].cx} y1={FY} x2={pos[n].cx} y2={PIPE_Y} stroke="rgba(54,197,224,0.18)" strokeWidth="1.5" />
            ))}
            <rect x={pumpX} y={PIPE_Y - 26} width="34" height="26" fill="rgba(54,197,224,0.08)" stroke="rgba(54,197,224,0.45)" strokeWidth="1.2" />
            <text x={pumpX} y={PIPE_Y + 18} fill="var(--text-weak)" style={{ font: "10.5px var(--mono)", letterSpacing: 1 }}>
              {loopName.toUpperCase()} · {plant?.loop?.flow != null ? Math.round(plant.loop.flow) : "—"} L/min
            </text>
          </g>
        )}

        {/* rails (static skeleton): PSU on the floor, riser, overhead bus, 90° drops */}
        {rails.map((r) => {
          const g = railGeom[r];
          const railSt = stOf(r);
          const busStroke = railSt === "hot" ? "var(--red)" : railSt === "strained" ? "var(--orange)" : "rgba(204,204,220,0.28)";
          const volts = plant?.rails?.[r]?.volts;
          return (
            <g key={r}>
              <Cuboid x={g.psuX} w={PSU_W} h={60} st={railSt} />
              <line x1={g.psuCx} y1={FY - 60 - DY} x2={g.psuCx} y2={BUS_Y} stroke={busStroke} strokeWidth="1.5" />
              <line x1={g.psuCx} y1={BUS_Y} x2={g.x1} y2={BUS_Y} stroke={busStroke} strokeWidth="4" strokeLinecap="round" />
              <text x={g.psuX} y={BUS_Y - 14} fill="var(--text-weak)" style={{ font: "11px var(--mono)", letterSpacing: 1.5 }}>
                RAIL {r.toUpperCase()} · <tspan fill={railSt === "ok" ? "var(--text)" : railSt === "hot" ? "var(--red)" : "var(--orange)"}>
                  {volts != null ? volts.toFixed(1) : "—"} V</tspan>
              </text>
              <text x={g.psuX + 2} y={FY + 16} fill="var(--text-faint)" style={{ font: "10px var(--mono)", letterSpacing: 1 }}>{r}</text>
              {g.members.map((n) => pos[n] && (
                <line key={n} x1={pos[n].dropX} y1={BUS_Y} x2={pos[n].dropX} y2={pos[n].topY}
                  stroke="rgba(204,204,220,0.16)" strokeWidth="1.5" />
              ))}
            </g>
          );
        })}

        {/* machines (LOD-1 cuboids, one plane) + labels */}
        {devs.map(([n, d]) => pos[n] && (
          <g key={n}>
            <Cuboid x={pos[n].x} w={pos[n].w} h={pos[n].h} st={stOf(n)} tripped={trippedOf(n)} />
            <text x={pos[n].cx} y={FY + 16} textAnchor="middle"
              fill={stOf(n) === "hot" ? "var(--red)" : "var(--text-weak)"}
              style={{ font: "600 10.5px var(--mono)", letterSpacing: 0.5 }}>{n}</text>
            <text x={pos[n].cx} y={FY + 30} textAnchor="middle" fill="var(--text-faint)" style={{ font: "10px var(--mono)" }}>
              {d.amps != null ? `${d.amps.toFixed(1)} A` : ""}{d.cooled && d.temp != null ? ` · ${Math.round(d.temp)} °C` : ""}
            </text>
          </g>
        ))}

        {/* live causal overlay — evidence-weighted activity ON the fixed wires, marching src → dst */}
        {live.map((e, i) => {
          const p = pathFor(e);
          const w = e.render_weight ?? e.confidence ?? Math.abs(e.r || 0);
          return p ? (
            <path key={i} d={p} fill="none" stroke={edgeColor(w)} strokeWidth={1.6 + w * 2.6} opacity="0.9"
              strokeDasharray="7 5" className="fl-flow" markerEnd="url(#flarrow)">
              <title>{e.src} → {e.dst} · {(e.evidence || []).join("+")}{e.r != null ? ` · r=${e.r}` : ""}</title>
            </path>
          ) : null;
        })}
      </svg>
    </div>
  );
}
