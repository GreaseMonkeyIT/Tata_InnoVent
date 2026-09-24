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
// Rail A idles near 0.900 * Vsrc, rail B dips to about 0.887 in the compressor duty window, and
// PS1 drives rail A to about 0.866. The ENGINE judges deviation (2C'). These bands keep the
// console readable without flicker at the boundaries.
export const railSt = (r) => (r.volts < 0.872 * r.v_src ? "hot" : r.volts < 0.882 * r.v_src ? "strained" : "ok");
export const machSt = (d, trip) =>
  d.tripped || (d.cooled && d.temp != null && d.temp >= trip - 3) || d.throughput < 70 ? "hot"
    : (d.cooled && d.temp != null && d.temp >= trip - 8) || d.throughput < 82 ? "strained" : "ok";
export const tempSt = (t, trip) => (t >= trip - 3 ? "hot" : t >= trip - 8 ? "strained" : "ok");
export const thruSt = (p) => (p < 70 ? "hot" : p < 82 ? "strained" : "ok");
export const tempColor = (t, trip) => ST_COLOR[tempSt(t, trip)];
export const thruColor = (p) => ST_COLOR[thruSt(p)];
export const meterColor = (v) => (v == null ? "var(--text-faint)" : v >= 0.9 ? ST_COLOR.hot : v >= 0.7 ? ST_COLOR.strained : ST_COLOR.ok);
export const flowSt = (loop) => {
  const nom = loop?.flow_nominal ?? 120;
  return !loop ? "ok" : loop.flow < 0.6 * nom ? "hot" : loop.flow < 0.85 * nom ? "strained" : "ok";
};

// SCADA tag quality -> status word
export const QST = { GOOD: "ok", STALE: "strained", BAD: "hot" };
