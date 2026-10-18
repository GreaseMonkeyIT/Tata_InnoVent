// The two looks of the floor prototype (2026-10-18). Both draw the same scene graph; a theme only
// gives colors, materials, lights and the label style.
//
//   console: the present map look (Floor.jsx, LOG-104/105): dark bodies, thin chrome-blue outlines,
//            a lit grid, the status colors as the only bright marks.
//   shop:    a simulator look in the Gazebo manner: plain colored solids on a light ground with a
//            grid, a sun with shadows, no outlines. The operator picked this look for the new floor
//            (2026-10-18). "console" stays for comparison only.
//   shopDark: the shop look on a dark ground, for the console's Dark theme (below).
//
// The state classes (STATE_CLASS in states.js) take the same meaning in both:
//   active  the station does its work         idle   ready, waits for a part
//   setup   a person works at the station     fault  stopped by a fault or a trip
import * as THREE from "three";

// The colors of the old console's palette (lib/palette.js, removed with the old console, LOG-120)
// that the console look still uses.
const HEX = { text: "#d2d6e4", teal: "#12c6b3", amber: "#ff9c00", red: "#f2495c" };
const HOLO = { line: "#3d8bff", bright: "#9cc4ff", grid: "#2458b8", plateEdge: "rgba(120, 170, 255, 0.55)" };

const hex = (s) => parseInt(s.slice(1), 16);

export const THEMES = {
  console: {
    name: "console",
    label: "Console (old look)",
    bg: 0x04060b,
    ground: 0x070a11,
    gridFine: { color: hex(HOLO.grid), opacity: 0.1, step: 2 },
    gridMajor: { color: hex(HOLO.line), opacity: 0.16, step: 10 },
    edges: { color: hex(HOLO.line), opacity: 0.5 },
    shadows: false,
    lit: "lambert",
    roles: {
      frame: 0x2b3342, moving: 0x3c475b, guard: 0x222936, robot: 0x3c475b, tank: 0x1d2430, liquid: 0x0f3b4a,
      hot: 0x3a2a22, glass: 0x1a2a4a, crane: 0x2f3747, rail: 0x2c3444, roller: 0x39424f, panel: 0x262d3a,
      floorline: hex(HOLO.line), wall: 0x0b101b, column: 0x161c28, accent: 0x4a5568, cable: 0x2c3444,
      water: 0x0a3e3a, rack: 0x262d3a, pallet: 0x39424f,
    },
    part: { raw: 0x6b7382, blasted: 0xaab3c0, painted: 0x1e2128, paintedEdge: 0x8a93a6, engine: 0x7d8696, carrier: 0x9cc4ff },
    status: { active: hex(HEX.teal), idle: 0x4a5160, setup: hex(HEX.text), fault: hex(HEX.red), warn: hex(HEX.amber) },
    sel: hex(HOLO.bright),
    plate: { bg: "rgba(5, 10, 22, 0.82)", fg: HEX.text, border: HOLO.plateEdge, font: "Consolas, 'Cascadia Code', monospace" },
    wallOpacity: 0.55,
    arc: 0x9cc4ff,
    sunlit: null, slab: 0x070a11, officeOpacity: 0.4, areaInk: "rgba(120,170,255,0.45)", fade: 0x10141c,
    fence: [0x3d8bff, 0.12], cellWall: [0x0b101b, 0.6], lampOff: 0x1c2230, idleGlow: 0.25,
  },
  shop: {
    name: "shop",
    label: "Shop",
    bg: 0xd6dbe1,
    ground: 0xc4c9cf,
    gridFine: { color: 0x9aa1a9, opacity: 0.35, step: 1 },
    gridMajor: { color: 0x6f7780, opacity: 0.55, step: 10 },
    edges: null,
    shadows: true,
    lit: "standard",
    roles: {
      frame: 0x557aa6, moving: 0x8fa9c7, guard: 0xf0c22e, robot: 0xe9782f, tank: 0x7e8b97, liquid: 0x4f8fb3,
      hot: 0xc3c8ce, glass: 0x9fc9e8, crane: 0xf0c419, rail: 0x5b636d, roller: 0x9ba4ae, panel: 0xc9cdd2,
      floorline: 0xe8c22a, wall: 0xeef0f3, column: 0x8d97a2, accent: 0x3f4852, cable: 0x2d3238,
      water: 0x3f86b5, rack: 0xd5772b, pallet: 0x6a737c,
    },
    part: { raw: 0x4e535b, blasted: 0xb9c0c8, painted: 0x1d1f23, paintedEdge: null, engine: 0x59626d, carrier: 0x2f6fb5 },
    status: { active: 0x2fb344, idle: 0xffffff, setup: 0x2e6be6, fault: 0xe0333d, warn: 0xf2b705 },
    sel: 0x1677ff,
    plate: { bg: "rgba(255, 255, 255, 0.92)", fg: "#1d232b", border: "rgba(40, 52, 66, 0.35)", font: "'Segoe UI', Arial, sans-serif" },
    wallOpacity: 1.0,
    arc: 0xbfe3ff,
    sunlit: { sky: 0xffffff, groundSky: 0x9aa1a9, hemi: 1.1, sun: 1.6 },
    slab: 0xdfe2e6, officeOpacity: 0.85, areaInk: "rgba(60,70,82,0.55)", fade: 0xd7dade,
    fence: [0xf0c22e, 0.55], cellWall: [0xe4e7eb, 0.55], lampOff: 0x9aa1a9, idleGlow: 0.8,
  },
};

// The Shop look on a dark ground, for the console's Dark theme (2026-10-18, an open point for the
// operator: research_hud_references.md section 8 item 3). The same solids and lamps as "shop"; only
// the ground, the building and the light change.
THEMES.shopDark = {
  ...THEMES.shop,
  name: "shopDark",
  label: "Shop, dark ground",
  bg: 0x121519,
  ground: 0x1c2024,
  gridFine: { color: 0x39404a, opacity: 0.3, step: 1 },
  gridMajor: { color: 0x4d5560, opacity: 0.45, step: 10 },
  roles: { ...THEMES.shop.roles, wall: 0x30363d, column: 0x4f5862, panel: 0x3c434b, floorline: 0xa88a1c, rail: 0x6a737d, rack: 0xa95e22, cable: 0x15181b },
  part: { ...THEMES.shop.part, painted: 0x0d0f12, raw: 0x6a7079 },
  sel: 0x5b9cff,
  plate: { ...THEMES.shop.plate, bg: "rgba(14, 18, 22, 0.86)", fg: "#e8eaed" },
  sunlit: { sky: 0xc9d0d8, groundSky: 0x16191d, hemi: 0.85, sun: 1.25 },
  slab: 0x262b30, officeOpacity: 0.8, areaInk: "rgba(200,210,222,0.32)", fade: 0x2a2f35,
  fence: [0xd8ae2a, 0.45], cellWall: [0x3c434b, 0.55], lampOff: 0x4a525b,
};

// A material per role, made once per theme and shared (a state change swaps a material, it never
// edits one).
export function materialKit(theme) {
  const cache = new Map();
  const make = (color, opts = {}) => {
    const key = `${color}:${JSON.stringify(opts)}`;
    if (cache.has(key)) return cache.get(key);
    const m = theme.lit === "standard"
      ? new THREE.MeshStandardMaterial({ color, roughness: 0.72, metalness: 0.08, ...opts })
      : new THREE.MeshLambertMaterial({ color, ...opts });
    m.userData.shared = true;
    cache.set(key, m);
    return m;
  };
  const role = (r, opts) => make(theme.roles[r] ?? theme.roles.frame, opts);
  const color = (c, opts) => make(c, opts);
  const lamp = (cls) => make(theme.status[cls] ?? theme.status.idle, { emissive: theme.status[cls] ?? theme.status.idle, emissiveIntensity: cls === "idle" ? theme.idleGlow : 0.9 });
  const glow = (c, i = 1.2) => make(c, { emissive: c, emissiveIntensity: i });
  const edge = theme.edges ? new THREE.LineBasicMaterial({ color: theme.edges.color, transparent: true, opacity: theme.edges.opacity, depthWrite: false }) : null;
  if (edge) edge.userData.shared = true;
  const dispose = () => { for (const m of cache.values()) m.dispose(); edge?.dispose(); cache.clear(); };
  return { role, color, lamp, glow, edge, dispose };
}
