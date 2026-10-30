"use client";
// The floor HUD (LOG-121): what the console draws on the 3D floor, in screen space over the canvas
// (research_hud_references.md rules 7 to 9, the EVE brackets and the DSP badges):
//   - a marker over each abnormal station: its glyph 40 px above the station top, a 1 px stalk down
//     to it, and the name and state word beside it in ink with a halo (no label box)
//   - the selection: four 2 px blue corner ticks around the station's screen outline, and a small
//     window beside it (name, state, one vital, "Inspect")
//   - a small window with the name and one vital of the station under the pointer
//   - links: "Show on the floor" (a dashed path from the root cause to each affected station) and the
//     floor overlays (power, air, water, gas, flow: a line from each source to each user), each with
//     an arrow head at the user end
//   - labels: a name and one value over each member of an overlay, or the names of all stations
// Labels that would overlap move apart: markers first, then overlay labels, then names; a marker
// moves up on a longer stalk.
// React renders the parts when the view-model changes (twice a second). The scene calls onFrame
// after each render, and place() moves the parts with the camera from the data-at attributes, so
// the overlay never waits for React.
import { useEffect, useRef } from "react";

const ticks = ({ x0, y0, x1, y1 }) => {
  const p = 6; x0 -= p; y0 -= p; x1 += p; y1 += p;
  const L = Math.max(4, Math.min(12, (x1 - x0) / 3, (y1 - y0) / 3));
  return `M${x0} ${y0 + L}V${y0}H${x0 + L}M${x1 - L} ${y0}H${x1}V${y0 + L}M${x1} ${y1 - L}V${y1}H${x1 - L}M${x0 + L} ${y1}H${x0}V${y1 - L}`;
};
const f = (n) => n.toFixed(1);
const MARKER_Y = -40;                       // the glyph centre above the station top, px
const SHIFTS = [0, -22, 22, -44, 44, -66];  // tries for a label that overlaps another one, px
const MARKER_SHIFTS = [0, -26, -52, -78];   // a marker only moves up (its stalk gets longer)

function Shape({ st, r }) {
  if (st === "alarm") return <><rect className="ring" x={-r - 4} y={-r - 4} width={2 * r + 8} height={2 * r + 8} /><rect className="f-alarm" x={-r} y={-r} width={2 * r} height={2 * r} /></>;
  if (st === "warn") return <path className="f-warn" d={`M0 ${f(-r * 1.1)}L${f(r * 1.15)} ${f(r * 0.95)}H${f(-r * 1.15)}Z`} />;
  return <circle className="f-recover" r={r * 0.85} />;
}

const overlaps = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

// Move overlapping labels apart. items: { g, t, box, prio, marker, r }; the box is in screen px.
function separate(items) {
  items.sort((a, b) => a.prio - b.prio || a.box.y0 - b.box.y0);
  const placed = [];
  for (const it of items) {
    const tries = it.marker ? MARKER_SHIFTS : SHIFTS;
    // the first free place; if every try overlaps, the label stays where it is
    const free = tries.find((d) => !placed.some((p) => overlaps(p, { ...it.box, y0: it.box.y0 + d, y1: it.box.y1 + d })));
    const dy = free ?? 0;
    placed.push({ ...it.box, y0: it.box.y0 + dy, y1: it.box.y1 + dy });
    if (it.marker) {
      it.g.querySelector(".mk-top").setAttribute("transform", `translate(0 ${dy})`);
      it.g.querySelector(".stalk").setAttribute("y2", f(MARKER_Y + it.r + 2 + dy));
    } else it.t.setAttribute("transform", `translate(0 ${dy})`);
  }
}

export default function FloorHud({ sceneRef, markers = [], names = [], labels = [], links = [], sel, hover, onInspect }) {
  const layer = useRef(null);

  useEffect(() => {
    let alive = true, scene = null;
    const place = (s) => {
      const root = layer.current; if (!root) return;
      const W = root.clientWidth || 1920, H = root.clientHeight || 1080;
      const items = [];
      for (const g of root.querySelectorAll("[data-at]")) {
        const a = s.anchorOf(g.dataset.at);
        if (!a || !a.front) { g.setAttribute("visibility", "hidden"); continue; }
        g.setAttribute("visibility", "visible");
        if (g.dataset.kind === "sel") { g.querySelector(".ticks").setAttribute("d", ticks(a.box)); continue; }
        g.setAttribute("transform", `translate(${f(a.top.x)} ${f(a.top.y)})`);
        const t = g.querySelector("text.lbl"); if (!t) continue;
        // the text width, measured again only when the text changes
        if (t.dataset.k !== t.textContent) { t.dataset.k = t.textContent; t.dataset.w = t.getComputedTextLength(); }
        const w = +t.dataset.w, mid = t.getAttribute("text-anchor") === "middle";
        const x0 = a.top.x + (mid ? -w / 2 : +t.getAttribute("x")), cy = a.top.y + +t.getAttribute("y");
        const marker = g.dataset.kind === "mk";
        items.push({ g, t, marker, r: +(g.dataset.r || 0), prio: marker ? 0 : g.dataset.kind === "ov" ? 1 : 2,
          box: { x0: x0 - 3, x1: x0 + w + 3, y0: cy - (marker ? 13 : 9), y1: cy + (marker ? 13 : 9) } });
      }
      separate(items);
      // the two small windows: the selection readout right of the selection (left of it near the
      // attention window), the hover tip over the station
      for (const el of root.querySelectorAll(".tip[data-tipat]")) {
        const a = s.anchorOf(el.dataset.tipat);
        if (!a || !a.front) { el.style.visibility = "hidden"; continue; }
        const w = el.offsetWidth, h = el.offsetHeight;
        let x, y;
        if (el.dataset.mode === "sel") {
          x = a.box.x1 + 14; if (x + w > W - 464) x = a.box.x0 - 14 - w;
          y = Math.min(Math.max(a.box.y0, 56), H - 140 - h);
        } else { x = a.top.x - w / 2; y = a.top.y - h - 14; }
        el.style.transform = `translate(${f(x)}px, ${f(y)}px)`; el.style.visibility = "visible";
      }
      for (const p of root.querySelectorAll("[data-from]")) {
        const a = s.anchorOf(p.dataset.from), b = s.anchorOf(p.dataset.to);
        if (!a || !b || !a.front || !b.front) { p.setAttribute("visibility", "hidden"); continue; }
        p.setAttribute("visibility", "visible");
        // stop short of both stations, so the arrow head shows next to the user
        const dx = b.base.x - a.base.x, dy = b.base.y - a.base.y, d = Math.hypot(dx, dy) || 1, k = Math.min(20, d / 4);
        const x0 = a.base.x + (dx / d) * k, y0 = a.base.y + (dy / d) * k, x1 = b.base.x - (dx / d) * k, y1 = b.base.y - (dy / d) * k;
        for (const l of p.querySelectorAll("line")) { l.setAttribute("x1", f(x0)); l.setAttribute("y1", f(y0)); l.setAttribute("x2", f(x1)); l.setAttribute("y2", f(y1)); }
        const t = p.querySelector("text");
        if (t) { t.setAttribute("x", f((x0 + x1) / 2 + 8)); t.setAttribute("y", f((y0 + y1) / 2 - 8)); }
      }
    };
    const attach = () => {
      if (!alive) return;
      scene = sceneRef.current;
      if (scene) scene.onFrame = place; else requestAnimationFrame(attach);
    };
    attach();
    return () => { alive = false; if (scene && scene.onFrame === place) scene.onFrame = null; };
  }, [sceneRef]);

  const marked = new Set(markers.map((m) => m.station));
  const labelled = new Set(labels.map((l) => l.at));
  return (
    <div className="hud-layer" ref={layer}>
    <svg className="hud">
      <defs>
        <marker id="hud-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">
          <path className="arrow" d="M0 0 L10 5 L0 10 Z" />
        </marker>
      </defs>
      {links.map((l) => (
        <g key={`p-${l.from}-${l.to}`} data-from={l.from} data-to={l.to} className={l.dashed ? "dashed" : l.thin ? "thin" : ""} visibility="hidden">
          {!l.thin && <line className="fpath-halo" />}
          <line className="fpath" markerEnd={l.thin ? undefined : "url(#hud-arrow)"} />
          {l.text && <text>{l.text}</text>}
        </g>
      ))}
      {names.filter((n) => !marked.has(n) && !labelled.has(n)).map((n) => (
        <g key={`n-${n}`} data-at={n} data-kind="nm" visibility="hidden"><text className="lbl n" y={-12} textAnchor="middle" dominantBaseline="central">{n}</text></g>
      ))}
      {labels.filter((l) => !marked.has(l.at)).map((l) => (
        <g key={`o-${l.at}`} data-at={l.at} data-kind="ov" visibility="hidden">
          <text className="lbl" y={-12} textAnchor="middle" dominantBaseline="central">{l.name}{l.text ? <tspan className="n"> {l.text}</tspan> : null}</text>
        </g>
      ))}
      {markers.map((m) => {
        const r = m.root ? 12 : 10;
        return (
          <g key={`m-${m.station}`} data-at={m.station} data-kind="mk" data-r={r} className={`mk${m.blink ? " blink" : ""}`} visibility="hidden">
            <line className="stalk" x1="0" y1="0" x2="0" y2={MARKER_Y + r + 2} />
            <g className="mk-top">
              <g transform={`translate(0 ${MARKER_Y})`}><Shape st={m.level} r={r} /></g>
              <text className="lbl" x={r + 8} y={MARKER_Y} dominantBaseline="central">{m.label}</text>
            </g>
          </g>
        );
      })}
      {sel && <g key={`s-${sel.id}`} data-at={sel.id} data-kind="sel" visibility="hidden"><path className="ticks" /></g>}
    </svg>
    {sel && (
      <div key={`st-${sel.id}`} className="win tip" data-tipat={sel.id} data-mode="sel">
        <b>{sel.id}</b>
        {sel.lines.map((l, i) => <div key={i}>{l}</div>)}
        {onInspect && <button className="cmd" onClick={() => onInspect(sel.id)}>Inspect</button>}
      </div>
    )}
    {hover && !marked.has(hover.id) && hover.id !== sel?.id && (
      <div key={`ht-${hover.id}`} className="win tip" data-tipat={hover.id} data-mode="hover">
        <b>{hover.id}</b>{hover.line && <div>{hover.line}</div>}
      </div>
    )}
    </div>
  );
}
