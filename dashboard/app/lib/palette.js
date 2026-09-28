// VISR palette for code that cannot read CSS variables (three.js materials, SVG strokes).
// Keep these values in step with the tokens in globals.css. Color roles (LOG-080): only status
// markers take a color, teal = normal, amber = warning, red = alarm. Everything else is gray
// (normal = sparklines, bands, card edges). Blue = operator command (fill only), black = a data well.
export const HEX = {
  void: "#000000",
  bg: "#0a0d14",
  panel: "#11151f",
  text: "#d2d6e4",
  teal: "#12c6b3",
  normal: "#8a93a6",
  blue: "#0000b3",
  blueEdge: "#5a5fe6",
  amber: "#ff9c00",
  red: "#f2495c",
};

// The hologram chrome (the digital-twin theme): the luminous Tata blue that draws wireframes, the
// floor grid, glass edges, and the energy flow. Always dim (thin lines or low alpha), so the three
// status colors above stay the only bright marks. Keep in step with --holo in scifi.css.
export const HOLO = {
  line: "#3d8bff",          // wireframe edges, landing rings, brackets
  bright: "#9cc4ff",        // selection, window strips
  grid: "#2458b8",          // the fine floor grid
  flow: "#bcd6ff",          // energy particles on the bus bars (bright enough to bloom)
  coolant: "#5fd8ff",       // coolant particles in the trench
  plateEdge: "rgba(120, 170, 255, 0.55)",   // the border of a floating tag plate
};

// Status word -> CSS color. The Glyph component adds the shape (ISA-101 redundant coding).
export const ST_COLOR = { hot: "var(--red)", strained: "var(--amber)", ok: "var(--normal)" };

// Contention ramp for conduits and graph edges: gray at low weight, amber at half, red at full.
// It uses the same scale as the status colors, so a heavy edge reads as an alarm.
export function edgeRGB(w) {
  const t = Math.min(Math.max(w ?? 0, 0), 1);
  const gray = [150, 158, 170], amber = [255, 156, 0], red = [242, 73, 92];
  const [a, b, k] = t < 0.5 ? [gray, amber, t / 0.5] : [amber, red, (t - 0.5) / 0.5];
  return a.map((v, i) => Math.round(v + (b[i] - v) * k));
}
export const edgeColor = (w) => `rgb(${edgeRGB(w).join(", ")})`;

// Display bands only, matched to the sim's measured physics so a steady plant reads calm.
// LOG-100: the rails drop 3 to 4 % at full load (rail A about 0.966 * Vsrc, rail B about 0.96 in the
// compressor window), so a steady plant and PS1 (about 0.955) stay "ok". A supply dip (PS7) reads hot. The ENGINE judges deviation (2C'). These bands keep the
// console readable without flicker at the boundaries.
export const railSt = (r) => (r.volts < 0.872 * r.v_src ? "hot" : r.volts < 0.882 * r.v_src ? "strained" : "ok");
// LOG-103: throughput is graded against each machine's own range, learned over the PS0 soak at the
// baseline lock (`thru_band` from /api/plant, deploy/engine-baselines.sh). One rule for every machine:
// 18 points under the learned low edge is "strained", 30 points under is "hot". A machine with no band
// has the low edge 100, which is the fixed rule of LOG-062 (under 82 strained, under 70 hot).
export const lowEdge = (d) => (Array.isArray(d?.thru_band) && d.thru_band.length === 2 ? d.thru_band[0] : 100);
export const machSt = (d, trip) => {
  const t = thruSt(d.throughput, d);
  return d.tripped || (d.cooled && d.temp != null && d.temp >= trip - 3) || t === "hot" ? "hot"
    : (d.cooled && d.temp != null && d.temp >= trip - 8) || t === "strained" ? "strained" : "ok";
};
export const tempSt = (t, trip) => (t >= trip - 3 ? "hot" : t >= trip - 8 ? "strained" : "ok");
export const thruSt = (p, d) => { const lo = lowEdge(d); return p < lo - 30 ? "hot" : p < lo - 18 ? "strained" : "ok"; };
export const tempColor = (t, trip) => ST_COLOR[tempSt(t, trip)];
export const thruColor = (p, d) => ST_COLOR[thruSt(p, d)];
export const meterColor = (v) => (v == null ? "var(--text-faint)" : v >= 0.9 ? ST_COLOR.hot : v >= 0.7 ? ST_COLOR.strained : ST_COLOR.ok);
export const flowSt = (loop) => {
  const nom = loop?.flow_nominal ?? 120;
  return !loop ? "ok" : loop.flow < 0.6 * nom ? "hot" : loop.flow < 0.85 * nom ? "strained" : "ok";
};

// SCADA tag quality -> status word
export const QST = { GOOD: "ok", STALE: "strained", BAD: "hot" };
