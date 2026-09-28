"use client";
// The plant floor as a holographic digital twin (three.js, plain scene graph).
//
// Doctrine kept from the LOG-038 floor: the wiring is STATIC, the live verdict only paints activity
// onto fixed conduits, and every colored mark is a measured state (teal normal, amber warning, red
// alarm). What this version adds is the twin look:
//   - machine silhouettes per kind (press, cnc, furnace, compressor, chiller, conveyor, scanner)
//     drawn as dark bodies with luminous wireframe edges in the Tata blue chrome hue
//   - a lit floor grid, glass walls, a landing ring under every machine
//   - light that comes from data: energy flows along a bus bar at a speed set by that rail's
//     measured feeder current, and coolant flows along the trench at the measured loop flow
//   - a tag plate per machine that shows on click, on hover, and for an alarm; a calm hall shows none
//   - bloom (UnrealBloomPass) so lamps, edges and flows glow like a hologram
//   - a slow auto-orbit that stops for good at the operator's first touch
// The props and the pick contract are unchanged, so MapPanel and Console need no change for it.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import SpriteText from "three-spritetext";
import { edgeRGB, HEX, HOLO } from "./lib/palette";

// ── switches: turn a feature off here if a laptop GPU struggles ─────────────────────────────
const GLOW = true;          // bloom post-processing (the hologram glow)
const AUTO_ORBIT = true;    // slow cinematic orbit until the operator touches the camera
const FLOW = true;          // energy and coolant flow particles (speed = measured value)
const FPS_CAP = 30;         // frames per second while something animates
const SHOW_ALARM_PLATES = true;  // a tripped machine and the root cause keep their plates: an alarm is never silent

// LOD-1 footprints (w along the line, d across, h tall) by asset kind.
const SIZE = [
  [/^press/, { w: 88, d: 64, h: 78 }],
  [/^furnace/, { w: 90, d: 70, h: 62 }],
  [/^cnc/, { w: 78, d: 58, h: 56 }],
  [/^conveyor/, { w: 104, d: 40, h: 24 }],
  [/^compressor/, { w: 72, d: 52, h: 48 }],
  [/^chiller/, { w: 74, d: 56, h: 52 }],
  [/scanner|^qa/, { w: 48, d: 40, h: 40 }],
];
const sizeOf = (n) => (SIZE.find(([re]) => re.test(n)) || [null, { w: 72, d: 52, h: 50 }])[1];
const kindOf = (n) => (/^press/.test(n) ? "press" : /^furnace/.test(n) ? "furnace" : /^cnc/.test(n) ? "cnc"
  : /^conveyor/.test(n) ? "conveyor" : /^compressor/.test(n) ? "compressor" : /^chiller/.test(n) ? "chiller"
  : /scanner|^qa/.test(n) ? "scanner" : /wrapper|labeler/.test(n) ? "packager" : "generic");

const hex = (s) => parseInt(s.slice(1), 16);
const C = {
  bg: 0x04060b, slab: 0x070a11, wall: 0x0b101b, window: 0x1a2a4a,
  body: 0x2b3342, bodyTripped: 0x171b24, wire: 0x2c3444, pipe: 0x0a3e3a,
  holo: hex(HOLO.line), holoBright: hex(HOLO.bright), grid: hex(HOLO.grid), flow: hex(HOLO.flow), coolant: hex(HOLO.coolant),
  red: hex(HEX.red), amber: hex(HEX.amber), teal: hex(HEX.teal), sel: hex(HOLO.bright), text: HEX.text,
};
const GAP = 50, PSU_W = 34, BUS_Y = 122, ROW_Z = 62, PIPE_Y = 3;

const edgeColor = (w) => new THREE.Color(`rgb(${edgeRGB(w).join(",")})`);
const lambert = (color, opts = {}) => new THREE.MeshLambertMaterial({ color, ...opts });
const box = (w, h, d, color, opts = {}) => new THREE.Mesh(new THREE.BoxGeometry(w, h, d), lambert(color, opts));
const cyl = (rt, rb, h, color, seg = 18, opts = {}) => new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), lambert(color, opts));

// Luminous wireframe edges for a mesh: the hologram outline. Drawn 1 px, so it stays crisp at any zoom.
const edgesOf = (mesh, color = C.holo, opacity = 0.55, angle = 22) => {
  const l = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, angle),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  l.position.copy(mesh.position); l.rotation.copy(mesh.rotation); l.scale.copy(mesh.scale);
  return l;
};

function disposeGroup(g) {
  g.traverse((o) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
  g.clear();
}

// One production line per rail (row A back, row B front, later rails in front of B); machines in
// /state order; the coolant trench runs between the first two lines at z = 0.
const rowZ = (i) => (i === 0 ? -ROW_Z : i === 1 ? ROW_Z : ROW_Z + (i - 1) * ROW_Z * 2);

function layout(plant) {
  const rails = Object.keys(plant?.rails || {});
  const devs = Object.entries(plant?.devices || {});
  if (!rails.length || !devs.length) return null;
  const pos = {}, railGeom = {};
  let maxX = 0;
  rails.forEach((r, i) => {
    const z = rowZ(i);
    let x = 0;
    const psuX = x; x += PSU_W + 34;
    const members = devs.filter(([, d]) => d.rail === r).map(([n]) => n);
    for (const n of members) {
      const s = sizeOf(n);
      pos[n] = { cx: x + s.w / 2, z, ...s, kind: kindOf(n) };
      x += s.w + GAP;
    }
    railGeom[r] = { z, psuX, psuCx: psuX + PSU_W / 2, x1: x - GAP + 14, members };
    maxX = Math.max(maxX, x - GAP);
  });
  const loopName = plant?.loop?.name || null;
  const cooled = devs.filter(([, d]) => d.cooled).map(([n]) => n);
  const zMin = rowZ(0), zMax = rowZ(Math.max(rails.length - 1, 1));
  const cells = Object.entries(plant?.cells || {}).map(([name, c]) => ({ name, plc: c.plc, machines: c.machines || [] }));
  return { rails, devs: devs.map(([n]) => n), pos, railGeom, loopName, cooled, pumpX: -14, W: maxX, zMin, zMax, cells };
}

// ── machine silhouettes ─────────────────────────────────────────────────────────────────────
// Each returns { group, meshes } with the group centred on (cx, 0, z). The +z face is the one the
// ISO camera sees, so doors, windows and fins sit there. The pick volume is added by the caller.
function silhouette(kind, p) {
  const g = new THREE.Group();
  const meshes = [];
  const add = (m, x = 0, y = 0, z = 0, rot) => { m.position.set(x, y, z); if (rot) m.rotation.set(...rot); g.add(m); meshes.push(m); return m; };
  const { w, d, h } = p;
  if (kind === "press") {
    add(box(w, h * 0.22, d, C.body), 0, h * 0.11);                                   // base plinth
    for (const sx of [-1, 1]) add(box(w * 0.16, h * 0.78, d * 0.6, C.body), sx * w * 0.36, h * 0.22 + h * 0.39);  // uprights
    add(box(w, h * 0.18, d * 0.66, C.body), 0, h * 0.91);                             // crown
    add(box(w * 0.34, h * 0.3, d * 0.42, C.body), 0, h * 0.56);                        // ram
    add(box(w * 0.58, h * 0.07, d * 0.62, C.body), 0, h * 0.26);                       // bolster
  } else if (kind === "cnc") {
    add(box(w, h, d, C.body), 0, h / 2);                                              // enclosure
    add(box(w * 0.56, h * 0.36, 1.5, C.window, { emissive: C.window, emissiveIntensity: 0.9 }), 0, h * 0.55, d / 2 + 0.8);  // window
    add(box(w * 0.22, h * 0.6, d * 0.3, C.body), w * 0.38 + w * 0.11, h * 0.3, -d * 0.15);  // control pendant column
  } else if (kind === "furnace") {
    const r = Math.min(w, d) * 0.42;
    add(cyl(r, r, h * 0.72, C.body, 28), 0, h * 0.36);                                // shell
    add(new THREE.Mesh(new THREE.SphereGeometry(r, 28, 14, 0, Math.PI * 2, 0, Math.PI / 2), lambert(C.body)), 0, h * 0.72);  // dome
    add(cyl(r * 0.18, r * 0.18, h * 0.5, C.wire, 12), r * 0.55, h * 0.72 + h * 0.25);  // flue
    const band = new THREE.Mesh(new THREE.TorusGeometry(r * 1.01, 1.4, 8, 40), new THREE.MeshLambertMaterial({ color: C.amber, emissive: C.amber, emissiveIntensity: 0.2 }));
    add(band, 0, h * 0.5, 0, [Math.PI / 2, 0, 0]);
    g.userData.heatBand = band;                                                       // brightness follows the coil temperature
  } else if (kind === "compressor") {
    add(box(w, h * 0.16, d, C.body), 0, h * 0.08);                                    // skid
    add(cyl(d * 0.3, d * 0.3, w * 0.74, C.body, 24), 0, h * 0.16 + d * 0.3, d * 0.1, [0, 0, Math.PI / 2]);  // receiver tank
    add(box(w * 0.28, h * 0.44, d * 0.4, C.body), -w * 0.3, h * 0.16 + h * 0.22, -d * 0.28);  // motor block
    add(cyl(2, 2, h * 0.5, C.wire, 8), w * 0.32, h * 0.16 + h * 0.25, -d * 0.3);       // gauge post
  } else if (kind === "chiller") {
    add(box(w, h * 0.7, d, C.body), 0, h * 0.35);                                     // condenser block
    for (let i = 0; i < 7; i++) add(box(1.4, h * 0.56, 1.6, C.wire), -w * 0.36 + i * (w * 0.72 / 6), h * 0.35, d / 2 + 0.9);  // fins
    add(box(w * 0.7, h * 0.12, d * 0.7, C.body), 0, h * 0.76);                        // fan deck
    add(new THREE.Mesh(new THREE.TorusGeometry(Math.min(w, d) * 0.26, 1.6, 8, 36), lambert(C.wire)), 0, h * 0.84, 0, [Math.PI / 2, 0, 0]);  // fan ring
  } else if (kind === "conveyor") {
    add(box(w, h * 0.34, d * 0.72, C.body), 0, h * 0.66);                             // belt frame
    for (const sx of [-0.42, 0.42]) add(box(3, h * 0.5, 3, C.wire), sx * w, h * 0.25, 0);  // legs
    const n = Math.max(3, Math.floor(w / 13));
    for (let i = 0; i < n; i++) add(cyl(d * 0.14, d * 0.14, d * 0.76, C.wire, 12), -w * 0.46 + (i + 0.5) * (w * 0.92 / n), h * 0.9, 0, [Math.PI / 2, 0, 0]);  // rollers
  } else if (kind === "scanner") {
    for (const sx of [-1, 1]) add(box(4, h, 4, C.body), sx * (w * 0.42), h / 2, 0);   // gantry posts
    add(box(w, 5, 6, C.body), 0, h - 2.5);                                            // cross beam
    add(box(w * 0.28, h * 0.22, d * 0.5, C.body), 0, h * 0.72);                        // scanner head
    add(box(w * 0.86, 0.6, d * 0.9, C.window, { emissive: C.holoBright, emissiveIntensity: 0.6, transparent: true, opacity: 0.35 }), 0, h * 0.3);  // scan plane
  } else if (kind === "packager") {
    add(box(w, h * 0.55, d, C.body), 0, h * 0.28);
    add(box(w * 0.5, h * 0.45, d * 0.6, C.body), 0, h * 0.55 + h * 0.2);
  } else {
    add(box(w, h, d, C.body), 0, h / 2);
  }
  return { group: g, meshes };
}

export default function Floor({ plant, graph, selected, onPick, view = { mode: "iso", n: 0 } }) {
  const wrapRef = useRef();
  const stateRef = useRef({});
  stateRef.current = { plant, graph, selected, onPick, view };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(C.bg);
    scene.fog = new THREE.Fog(C.bg, 980, 1900);
    scene.add(new THREE.AmbientLight(0x9fb4ff, 0.55));
    scene.add(new THREE.HemisphereLight(0x5f8cff, 0x05070c, 0.45));
    const sun = new THREE.DirectionalLight(0xd9e6ff, 0.75);
    sun.position.set(-220, 420, 300);
    scene.add(sun);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -2000, 4000);

    // the hologram glow: scene -> bloom -> output (sRGB)
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(800, 540), 0.75, 0.42, 0.52);
    if (GLOW) composer.addPass(bloom);
    composer.addPass(new OutputPass());
    const paint = () => (GLOW ? composer.render() : renderer.render(scene, camera));

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.rotateSpeed = 0.7;
    controls.minZoom = 0.5;
    controls.maxZoom = 5;
    controls.minPolarAngle = 0.05;
    controls.maxPolarAngle = 1.36;
    controls.cursorStyle = "grab";
    controls.autoRotate = AUTO_ORBIT && !reduced;
    controls.autoRotateSpeed = 0.28;   // one slow revolution in a few minutes; it stops for good at the first touch

    const staticG = new THREE.Group(), liveG = new THREE.Group(), selG = new THREE.Group(), flowG = new THREE.Group();
    scene.add(staticG, liveG, selG, flowG);

    const R = { geo: null, topoSig: "", bodies: {}, lamps: {}, rings: {}, sprites: {}, spriteTxt: {},
      busBars: {}, cabinets: {}, edgeSig: "", pulses: [], flows: [], halfH: 300, viewMode: "iso", userMoved: false,
      pick: [], foot: {}, selKey: null, viewKey: null, dirty: true, selRing: null, hoverKey: null, alarmKeys: new Set() };

    const setFrustum = () => {
      const cw = el.clientWidth || 800, ch = el.clientHeight || 540;
      renderer.setSize(cw, ch, false);
      composer.setSize(cw, ch);
      const aspect = cw / Math.max(ch, 1);
      camera.left = -R.halfH * aspect; camera.right = R.halfH * aspect;
      camera.top = R.halfH; camera.bottom = -R.halfH;
      camera.updateProjectionMatrix();
      R.dirty = true;
    };

    const corner = new THREE.Vector3();
    const home = (mode) => {
      R.viewMode = mode;
      const plan = mode === "plan";
      const g = R.geo;
      const b = g ? [-60, g.W + 40, 0, BUS_Y + 28, g.zMin - 70, g.zMax + 70] : [0, 600, 0, 150, -130, 130];
      const target = new THREE.Vector3((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2);
      controls.target.copy(target);
      camera.position.setFromSphericalCoords(900, plan ? 0.12 : Math.PI / 2 - 0.64, plan ? 0 : 0.62).add(target);
      camera.lookAt(target);
      camera.updateMatrixWorld();
      let hw = 0, hh = 0;
      for (const x of [b[0], b[1]]) for (const y of [b[2], b[3]]) for (const z of [b[4], b[5]]) {
        corner.set(x, y, z).applyMatrix4(camera.matrixWorldInverse);
        hw = Math.max(hw, Math.abs(corner.x)); hh = Math.max(hh, Math.abs(corner.y));
      }
      const aspect = (el.clientWidth || 800) / Math.max(el.clientHeight || 540, 1);
      R.halfH = Math.max(hh, hw / aspect) * 1.04;
      camera.zoom = 1;
      setFrustum();
      controls.update();
      R.userMoved = false;
      controls.autoRotate = AUTO_ORBIT && !reduced && !plan;   // a top-down schematic never spins
    };
    controls.addEventListener("start", () => { R.userMoved = true; controls.autoRotate = false; });
    controls.addEventListener("change", () => { R.dirty = true; });

    // Selection: holo corner brackets on the slab plus a soft ring, both in the chrome blue.
    const buildSel = (id) => {
      disposeGroup(selG); R.selRing = null;
      const f = id ? R.foot[id] : null;
      if (!f) return;
      const m = 9, w = f.w + 2 * m, d = f.d + 2 * m, L = Math.max(10, Math.min(w, d) * 0.32), T = 2.2;
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        const x = f.cx + sx * (w / 2), z = f.cz + sz * (d / 2);
        const a = new THREE.Mesh(new THREE.BoxGeometry(L, 1.2, T), new THREE.MeshBasicMaterial({ color: C.sel }));
        a.position.set(x - (sx * L) / 2, 1.2, z); selG.add(a);
        const b = new THREE.Mesh(new THREE.BoxGeometry(T, 1.2, L), new THREE.MeshBasicMaterial({ color: C.sel }));
        b.position.set(x, 1.2, z - (sz * L) / 2); selG.add(b);
      }
      const rr = Math.max(w, d) * 0.62;
      const ring = new THREE.Mesh(new THREE.RingGeometry(rr - 2.4, rr, 64),
        new THREE.MeshBasicMaterial({ color: C.sel, transparent: true, opacity: 0.55, side: THREE.DoubleSide, depthWrite: false }));
      ring.rotation.x = -Math.PI / 2; ring.position.set(f.cx, 0.9, f.cz); selG.add(ring);
      R.selRing = ring;
    };

    // Which plates show: the selected asset's, the hovered asset's, and (SHOW_ALARM_PLATES) those of
    // every tripped machine and of the root cause. Everything else stays hidden, so a calm hall reads calm.
    const plateKey = (id) => {
      const g = R.geo; if (!g || !id) return null;
      if (g.pos[id]) return id;
      if (g.railGeom[id]) return `rail:${id}`;
      if (id === g.loopName) return `loop:${id}`;
      const cab = Object.entries(R.cabinets).find(([, c]) => c.plc === id);
      return cab ? `cab:${cab[0]}` : null;
    };
    const applyPlates = () => {
      const want = new Set([plateKey(R.selKey), plateKey(R.hoverKey), ...(SHOW_ALARM_PLATES ? R.alarmKeys : [])].filter(Boolean));
      let changed = false;
      for (const [k, sp] of Object.entries(R.sprites)) { const v = want.has(k); if (sp.visible !== v) { sp.visible = v; changed = true; } }
      if (changed) R.dirty = true;
    };

    const pickable = (mesh, kind, id, foot) => {
      mesh.userData = { kind, id };
      R.pick.push(mesh);
      if (foot && !R.foot[id]) R.foot[id] = foot;
    };
    // an invisible pick volume over a whole silhouette, so any part of a machine is clickable
    const pickBox = (cx, cz, w, h, d, kind, id) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
      m.position.set(cx, h / 2, cz); staticG.add(m);
      pickable(m, kind, id, { cx, cz, w, d });
      return m;
    };

    // the lit floor grid: fine lines every 40 units, a brighter line every 200
    const grid = (x0, x1, z0, z1, y, step, color, opacity) => {
      const pts = [];
      for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) pts.push(x, y, z0, x, y, z1);
      for (let z = Math.ceil(z0 / step) * step; z <= z1; z += step) pts.push(x0, y, z, x1, y, z);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
    };
    // a soft landing ring under each machine: chrome blue at rest, the state color under a verdict
    const landing = (cx, cz, w, d) => {
      const rr = Math.max(w, d) * 0.56;
      const ring = new THREE.Mesh(new THREE.RingGeometry(rr - 1.6, rr, 56),
        new THREE.MeshBasicMaterial({ color: C.holo, transparent: true, opacity: 0.32, side: THREE.DoubleSide, depthWrite: false }));
      ring.rotation.x = -Math.PI / 2; ring.position.set(cx, 0.7, cz);
      return ring;
    };
    const plate = (size, x, y, z, align = "center") => {
      const s = new SpriteText("", size, C.text);
      s.fontFace = "Consolas, 'Cascadia Code', monospace"; s.textAlign = align;
      s.backgroundColor = "rgba(5, 10, 22, 0.78)"; s.borderColor = HOLO.plateEdge; s.borderWidth = 0.05; s.borderRadius = 0.25; s.padding = 0.35;
      s.position.set(x, y, z);
      s.visible = false;                 // applyPlates() shows it on click, on hover, or for an alarm
      return s;
    };
    // a flow: n particles marching along a straight run from a to b; speedFn() gives units per second
    const addFlow = (a, b, color, size, speedFn) => {
      if (!FLOW || reduced) return;
      const len = a.distanceTo(b);
      const n = Math.max(2, Math.floor(len / 46));
      const parts = [];
      for (let i = 0; i < n; i++) {
        const m = new THREE.Mesh(new THREE.BoxGeometry(size[0], size[1], size[2]),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, depthWrite: false }));
        m.position.lerpVectors(a, b, i / n); flowG.add(m); parts.push({ mesh: m, t: (i / n) * len });
      }
      R.flows.push({ a, b, len, parts, speedFn, speed: speedFn() });
    };

    const buildStatic = (plantNow) => {
      disposeGroup(staticG); disposeGroup(flowG);
      R.bodies = {}; R.lamps = {}; R.rings = {}; R.sprites = {}; R.spriteTxt = {}; R.busBars = {}; R.cabinets = {}; R.flows = [];
      R.pick = []; R.foot = {}; R.selKey = null;
      const geo = layout(plantNow);
      R.geo = geo;
      if (!geo) return;
      const { rails, pos, railGeom, loopName, cooled, pumpX, W, zMin, zMax } = geo;
      const depth = zMax - zMin, zMid = (zMin + zMax) / 2;
      const X0 = -130, X1 = W + 130, Z0 = zMin - 130, Z1 = zMax + 130;

      // room: slab, grid, glass walls with luminous edges, window strips
      const slab = box(X1 - X0, 8, Z1 - Z0, C.slab); slab.position.set((X0 + X1) / 2, -4, zMid); staticG.add(slab);
      staticG.add(grid(X0, X1, Z0, Z1, 0.4, 40, C.grid, 0.1));
      staticG.add(grid(X0, X1, Z0, Z1, 0.5, 200, C.holo, 0.16));
      for (const z of [zMin - 62, zMax + 62]) {
        const lane = box(X1 - X0 - 90, 1, 2.4, C.holo, { emissive: C.holo, emissiveIntensity: 0.25 }); lane.position.set(W / 2, 0.6, z); staticG.add(lane);
      }
      const backW = box(X1 - X0, 110, 6, C.wall, { transparent: true, opacity: 0.6 }); backW.position.set((X0 + X1) / 2, 55, Z0 + 4); staticG.add(backW); staticG.add(edgesOf(backW, C.holo, 0.35));
      const leftW = box(6, 110, Z1 - Z0, C.wall, { transparent: true, opacity: 0.6 }); leftW.position.set(X0 + 4, 55, zMid); staticG.add(leftW); staticG.add(edgesOf(leftW, C.holo, 0.35));
      for (let i = 0; i < Math.max(2, Math.round(W / 260)); i++) {
        const win = box(150, 30, 2, C.window, { emissive: C.window, emissiveIntensity: 1.4 });
        win.position.set(60 + i * 260, 64, Z0 + 8); staticG.add(win); staticG.add(edgesOf(win, C.holoBright, 0.5));
      }

      // rails: PSU cabinet, riser, overhead bus with a luminous core, drops, energy flow
      rails.forEach((r) => {
        const g = railGeom[r], z = g.z;
        const psu = box(PSU_W, 64, 40, C.body); psu.position.set(g.psuCx, 32, z); staticG.add(psu); staticG.add(edgesOf(psu));
        const door = box(PSU_W * 0.7, 40, 1, C.wall); door.position.set(g.psuCx, 30, z + 20.6); staticG.add(door); staticG.add(edgesOf(door, C.holo, 0.5));
        pickable(psu, "asset", r, { cx: g.psuCx, cz: z, w: PSU_W, d: 40 });
        const riser = box(3.5, BUS_Y - 64, 3.5, C.wire); riser.position.set(g.psuCx, 64 + (BUS_Y - 64) / 2, z); staticG.add(riser);
        const bus = box(g.x1 - g.psuCx + 14, 5, 5, C.wire); bus.position.set((g.psuCx + g.x1) / 2, BUS_Y, z); staticG.add(bus);
        R.busBars[r] = bus;
        staticG.add(edgesOf(bus, C.holo, 0.45));
        const lbl = plate(9, g.psuX + 100, BUS_Y + 15, z);
        staticG.add(lbl); R.sprites[`rail:${r}`] = lbl;
        for (const n of g.members) {
          const p = pos[n];
          const drop = box(3, BUS_Y - p.h, 3, C.wire); drop.position.set(p.cx, p.h + (BUS_Y - p.h) / 2, z); staticG.add(drop);
        }
        addFlow(new THREE.Vector3(g.psuCx + 8, BUS_Y, z), new THREE.Vector3(g.x1 + 6, BUS_Y, z), C.flow, [7, 1.8, 1.8],
          () => { const a = stateRef.current.plant?.rails?.[r]?.amps ?? 0; return 10 + Math.min(1, a / 160) * 80; });
      });

      // machines: silhouette + edges + landing ring + stack light + tag plate + pick volume
      for (const [n, p] of Object.entries(pos)) {
        const { group, meshes } = silhouette(p.kind, p);
        group.position.set(p.cx, 0, p.z); staticG.add(group);
        const edges = meshes.map((m) => { const e = edgesOf(m); group.add(e); return e; });
        const ring = landing(p.cx, p.z, p.w, p.d); staticG.add(ring); R.rings[n] = ring;
        R.bodies[n] = { meshes, edges, heatBand: group.userData.heatBand || null };
        pickBox(p.cx, p.z, p.w, p.h, p.d, "asset", n);
        const pole = box(1.6, 14, 1.6, C.wire); pole.position.set(p.cx + p.w / 2 - 6, p.h + 7, p.z - p.d / 2 + 6); staticG.add(pole);
        const lamp = new THREE.Mesh(new THREE.CylinderGeometry(3, 3, 7, 12), lambert(C.teal, { emissive: C.teal, emissiveIntensity: 1.2 }));
        lamp.position.set(p.cx + p.w / 2 - 6, p.h + 17, p.z - p.d / 2 + 6); staticG.add(lamp); R.lamps[n] = lamp;
        const s = plate(7.2, p.cx, p.h + 24, p.z);
        staticG.add(s); R.sprites[n] = s;
      }

      // coolant: trench pipe, stubs to the cooled machines, pump, coolant flow
      if (loopName) {
        const endX = Math.max(...cooled.map((n) => pos[n]?.cx || 0), pumpX + 20);
        const pipe = box(endX - pumpX + 26, 4, 4, C.pipe, { emissive: C.pipe, emissiveIntensity: 0.6 });
        pipe.position.set((pumpX + endX) / 2, PIPE_Y, 0); staticG.add(pipe); staticG.add(edgesOf(pipe, C.coolant, 0.45));
        pickable(pipe, "asset", loopName);
        for (const n of cooled) {
          const p = pos[n]; if (!p) continue;
          const inner = p.z > 0 ? p.z - p.d / 2 : p.z + p.d / 2;
          const stub = box(3.5, 3.5, Math.abs(inner), C.pipe, { emissive: C.pipe, emissiveIntensity: 0.5 });
          stub.position.set(p.cx, PIPE_Y, inner / 2); staticG.add(stub);
        }
        const pump = box(26, 20, 20, C.pipe, { emissive: C.pipe, emissiveIntensity: 0.7 });
        pump.position.set(pumpX - 6, 10, 0); staticG.add(pump); staticG.add(edgesOf(pump, C.coolant, 0.6));
        pickable(pump, "asset", loopName, { cx: pumpX - 6, cz: 0, w: 26, d: 20 });
        const lbl = plate(8.5, pumpX - 6, 40, 8);
        staticG.add(lbl); R.sprites[`loop:${loopName}`] = lbl;
        addFlow(new THREE.Vector3(pumpX + 8, PIPE_Y + 0.2, 0), new THREE.Vector3(endX + 8, PIPE_Y + 0.2, 0), C.coolant, [6, 1.4, 1.4],
          () => { const l = stateRef.current.plant?.loop; const f = (l?.flow ?? 0) / (l?.flow_nominal || 120); return 6 + Math.min(1.2, f) * 50; });
      }

      // PLC cabinets, one per cell, beside its first machine. The lamp is the live field link.
      for (const cell of geo.cells) {
        const first = cell.machines.map((m) => pos[m]).find(Boolean);
        if (!first) continue;
        const zc = first.z + (first.z < 0 ? 1 : -1) * (first.d / 2 + 14);
        const cx = first.cx - first.w / 2 - 4;
        const cab = box(20, 46, 14, C.body); cab.position.set(cx, 23, zc); staticG.add(cab); staticG.add(edgesOf(cab));
        if (cell.plc) pickable(cab, "plc", cell.plc, { cx, cz: zc, w: 20, d: 14 });
        const lamp = new THREE.Mesh(new THREE.CylinderGeometry(2.6, 2.6, 6, 10), lambert(C.wire, { emissive: C.wire, emissiveIntensity: 0.4 }));
        lamp.position.set(cx, 50, zc); staticG.add(lamp);
        const s = plate(6.4, cx, 64, zc);
        staticG.add(s); R.sprites[`cab:${cell.name}`] = s;
        R.cabinets[cell.name] = { lamp, sprite: s, plc: cell.plc };
      }
      if (!R.userMoved) home(R.viewMode);
    };

    // live causal overlay: conduit segments + pulses marching src -> dst (unchanged doctrine)
    const anchors = (name) => {
      const { pos, railGeom, loopName, pumpX } = R.geo || {};
      if (pos?.[name]) return { rail: [pos[name].cx, pos[name].h, pos[name].z], pipe: [pos[name].cx, PIPE_Y, pos[name].z > 0 ? pos[name].z - pos[name].d / 2 : pos[name].z + pos[name].d / 2] };
      if (railGeom?.[name]) return { rail: [railGeom[name].psuCx, BUS_Y, railGeom[name].z], pipe: null };
      if (name === loopName) return { rail: null, pipe: [pumpX - 6, PIPE_Y, 0] };
      return null;
    };
    const pathFor = (e) => {
      const a = anchors(e.src), b = anchors(e.dst);
      if (!a || !b) return null;
      if (e.signal === "coolant_temp") {
        if (!a.pipe || !b.pipe) return null;
        return [a.pipe, [a.pipe[0], PIPE_Y, 0], [b.pipe[0], PIPE_Y, 0], b.pipe];
      }
      if (!a.rail || !b.rail || a.rail[2] !== b.rail[2]) return null;
      const z = a.rail[2];
      return [a.rail, [a.rail[0], BUS_Y, z], [b.rail[0], BUS_Y, z], b.rail];
    };
    const buildOverlay = (edges) => {
      disposeGroup(liveG); R.pulses = [];
      for (const e of edges) {
        const pts = pathFor(e); if (!pts) continue;
        const w = e.render_weight ?? e.confidence ?? Math.abs(e.r || 0);
        const col = edgeColor(w);
        const segs = []; let total = 0;
        for (let i = 0; i < pts.length - 1; i++) {
          const [x1, y1, z1] = pts[i], [x2, y2, z2] = pts[i + 1];
          const len = Math.abs(x2 - x1) + Math.abs(y2 - y1) + Math.abs(z2 - z1);
          if (len < 0.5) continue;
          const seg = new THREE.Mesh(
            new THREE.BoxGeometry(Math.max(Math.abs(x2 - x1), 2.6), Math.max(Math.abs(y2 - y1), 2.6), Math.max(Math.abs(z2 - z1), 2.6)),
            new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.8 }));
          seg.position.set((x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2);
          liveG.add(seg);
          segs.push({ a: new THREE.Vector3(x1, y1, z1), b: new THREE.Vector3(x2, y2, z2), len, off: total });
          total += len;
        }
        for (let k = 0; k < 2; k++) {
          const pulse = new THREE.Mesh(new THREE.SphereGeometry(3.6, 12, 12), new THREE.MeshBasicMaterial({ color: col }));
          liveG.add(pulse);
          R.pulses.push({ mesh: pulse, segs, total, t: (k / 2) * total });
        }
      }
    };

    const setLamp = (m, color, i = 1.2) => { m.material.color.setHex(color); m.material.emissive.setHex(color); m.material.emissiveIntensity = i; };
    const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));

    const applyLive = () => {
      const { plant: pl, graph: g } = stateRef.current;
      if (!R.geo) return;
      const root = g?.root?.[0]?.pod;
      const victims = new Set((g?.blast_radius || []).map((b) => b.pod));
      const forecast = new Set((g?.incipient || []).map((f) => f.pod));
      R.alarmKeys = new Set();
      for (const n of Object.keys(R.bodies)) {
        const d = pl?.devices?.[n] || {};
        const { meshes, edges, heatBand } = R.bodies[n];
        const lamp = R.lamps[n], sp = R.sprites[n], ring = R.rings[n];
        const st = d.tripped ? "trip" : n === root ? "hot" : victims.has(n) || forecast.has(n) ? "strained" : "ok";
        const glow = st === "hot" ? C.red : st === "strained" ? C.amber : null;
        if (st === "trip" || st === "hot") R.alarmKeys.add(n);
        for (const m of meshes) {
          if (m.material.color.getHex() === C.window) continue;                 // a window keeps its own light
          m.material.color.setHex(st === "trip" ? C.bodyTripped : C.body);
          m.material.emissive.setHex(glow ?? 0x000000);
          m.material.emissiveIntensity = st === "hot" ? 0.32 : st === "strained" ? 0.2 : 0;
        }
        for (const e of edges) { e.material.color.setHex(glow ?? (st === "trip" ? C.wire : C.holo)); e.material.opacity = glow ? 0.95 : 0.55; }
        ring.material.color.setHex(glow ?? C.holo); ring.material.opacity = glow ? 0.7 : 0.32;
        setLamp(lamp, st === "hot" || st === "trip" ? C.red : st === "strained" ? C.amber : C.teal);
        if (heatBand && d.temp != null && d.trip_c) heatBand.material.emissiveIntensity = 0.15 + 1.6 * Math.max(0, Math.min(1, (d.temp - 25) / (d.trip_c - 25)));
        const line2 = [d.amps != null ? `${fmt(d.amps)} A` : null, d.cooled && d.temp != null ? `${Math.round(d.temp)} °C` : null,
          d.throughput != null ? `${Math.round(d.throughput)} %` : null].filter(Boolean).join("  ");
        const txt = `${n}${d.tripped ? "  ⌀ OPEN" : ""}\n${line2}`;
        if (R.spriteTxt[n] !== txt) { sp.text = txt; R.spriteTxt[n] = txt; }
        sp.color = st === "hot" || st === "trip" ? HEX.red : st === "strained" ? HEX.amber : C.text;
        sp.borderColor = st === "hot" || st === "trip" ? HEX.red : st === "strained" ? HEX.amber : HOLO.plateEdge;
      }
      for (const r of Object.keys(R.busBars)) {
        const st = r === root ? C.red : victims.has(r) ? C.amber : C.wire;
        if (r === root) R.alarmKeys.add(`rail:${r}`);
        R.busBars[r].material.color.setHex(st);
        R.busBars[r].material.emissive?.setHex?.(st === C.wire ? 0x000000 : st);
        R.busBars[r].material.emissiveIntensity = st === C.wire ? 0 : 0.5;
        const rl = pl?.rails?.[r];
        const txt = `rail ${r}\n${rl?.volts != null ? fmt(rl.volts) : "—"} V   ${rl?.amps != null ? fmt(rl.amps, 0) : "—"} A`;
        if (R.spriteTxt[`rail:${r}`] !== txt) { R.sprites[`rail:${r}`].text = txt; R.spriteTxt[`rail:${r}`] = txt; }
        R.sprites[`rail:${r}`].borderColor = st === C.red ? HEX.red : st === C.amber ? HEX.amber : HOLO.plateEdge;
      }
      for (const [cn, cab] of Object.entries(R.cabinets || {})) {
        const c = pl?.cells?.[cn] || {};
        const col = c.mode === "closed-loop" ? C.teal : c.mode === "fail-open" ? C.amber : C.wire;
        setLamp(cab.lamp, col, col === C.wire ? 0.3 : 1.2);
        const txt = `${c.plc || cn}\n${c.mode || "no link"}`;
        if (R.spriteTxt[`cab:${cn}`] !== txt) { cab.sprite.text = txt; R.spriteTxt[`cab:${cn}`] = txt; }
      }
      if (R.geo.loopName) {
        const key = `loop:${R.geo.loopName}`;
        const l = pl?.loop || {};
        const txt = `loop ${R.geo.loopName}\n${l.flow != null ? Math.round(l.flow) : "—"} L/min   ${l.t_supply != null ? fmt(l.t_supply) : "—"} °C`;
        if (R.spriteTxt[key] !== txt && R.sprites[key]) { R.sprites[key].text = txt; R.spriteTxt[key] = txt; }
      }
      for (const f of R.flows) f.speed = f.speedFn();
      applyPlates();
      const live = (g?.edges || []).filter((e) => e.state === "active" || e.state === "confirming");
      const sig = JSON.stringify(live.map((e) => [e.src, e.dst, e.signal, Math.round((e.render_weight ?? e.confidence ?? 0) * 8)]).sort());
      if (sig !== R.edgeSig) { R.edgeSig = sig; buildOverlay(live); }
    };

    // Picking: a left press that moves less than 4 px is a click.
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const hitAt = (e) => {
      const rect = renderer.domElement.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      return ray.intersectObjects(R.pick, false)[0]?.object.userData || null;
    };
    let pressing = false, downBtn = -1, downX = 0, downY = 0;
    const onDown = (e) => { pressing = true; downBtn = e.button; downX = e.clientX; downY = e.clientY; };
    const onMove = (e) => {
      if (pressing) return;
      const hit = e.target === renderer.domElement ? hitAt(e) : null;
      renderer.domElement.style.cursor = hit ? "pointer" : "grab";
      const key = hit?.id || null;
      if (key !== R.hoverKey) { R.hoverKey = key; applyPlates(); }   // the plate follows the pointer
    };
    const onUp = (e) => {
      const click = pressing && downBtn === 0 && Math.hypot(e.clientX - downX, e.clientY - downY) < 4 && e.target === renderer.domElement;
      pressing = false;
      const hit = click ? hitAt(e) : null;
      if (hit) stateRef.current.onPick?.(hit.kind, hit.id);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);

    const ro = new ResizeObserver(() => setFrustum());
    ro.observe(el);
    let visible = true;
    const io = new IntersectionObserver(([e]) => { visible = !!e?.isIntersecting; if (visible) R.dirty = true; });
    io.observe(el);

    let raf, lastPlant = null, lastGraph = null;
    let lastT = performance.now(), lastPaint = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      const dt = Math.min((now - lastT) / 1000, 0.1); lastT = now;
      if (!visible) return;
      const { plant: pl, graph: g, selected: selNow, view: viewNow } = stateRef.current;
      if (viewNow && viewNow.n !== R.viewKey) { R.viewKey = viewNow.n; home(viewNow.mode); }
      if (pl && pl !== lastPlant) {
        lastPlant = pl;
        const sig = JSON.stringify([Object.keys(pl.devices || {}), Object.entries(pl.devices || {}).map(([n, d]) => [n, d.rail, !!d.cooled]), Object.keys(pl.rails || {}), pl.loop?.name,
          Object.entries(pl.cells || {}).map(([n, c]) => [n, c.plc, c.machines])]);
        if (sig !== R.topoSig) { R.topoSig = sig; buildStatic(pl); }
        if (R.geo) applyLive();
        R.dirty = true;
      }
      if (g !== lastGraph) { lastGraph = g; if (R.geo) applyLive(); R.dirty = true; }
      if (R.geo && (selNow || null) !== R.selKey) { R.selKey = selNow || null; buildSel(R.selKey); applyPlates(); R.dirty = true; }
      // animation that answers data: flows at measured speeds, verdict pulses, the selection ring
      const animating = R.flows.length || R.pulses.length || controls.autoRotate;
      if (animating && now - lastPaint < 1000 / FPS_CAP) return;
      for (const f of R.flows) {
        for (const p of f.parts) {
          p.t = (p.t + dt * f.speed) % f.len;
          p.mesh.position.lerpVectors(f.a, f.b, p.t / f.len);
        }
      }
      for (const p of R.pulses) {
        p.t = (p.t + dt * 70) % Math.max(p.total, 1);
        for (const s of p.segs) {
          if (p.t >= s.off && p.t <= s.off + s.len) { p.mesh.position.lerpVectors(s.a, s.b, (p.t - s.off) / Math.max(s.len, 1e-6)); break; }
        }
      }
      if (R.selRing) R.selRing.material.opacity = 0.42 + 0.18 * Math.sin(now / 420);
      if (controls.autoRotate) controls.update();
      if (animating) R.dirty = true;
      if (R.dirty) { paint(); R.dirty = false; lastPaint = now; }
    };
    home(R.viewMode); tick();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      disposeGroup(staticG); disposeGroup(liveG); disposeGroup(selG); disposeGroup(flowG);
      composer.dispose?.();
      renderer.dispose();
      renderer.forceContextLoss();
      el.removeChild(renderer.domElement);
    };
  }, []);

  const empty = !plant?.devices || !Object.keys(plant.devices).length;
  return (
    <div ref={wrapRef} className="floor3d">
      {empty && <div className="floor3d-wait">waiting for plant telemetry…</div>}
    </div>
  );
}
