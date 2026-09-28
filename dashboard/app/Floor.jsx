"use client";
// The plant floor as a 3D digital twin (three.js, plain scene graph).
//
// Doctrine kept from the LOG-038 floor: the wiring is STATIC, the live verdict only paints activity
// onto fixed conduits, and every colored mark is a measured state (teal normal, amber warning, red
// alarm). On top of that:
//   - machine silhouettes per kind (press, cnc, furnace, compressor, chiller, conveyor, scanner)
//     drawn as dark bodies with thin outlines of the outer shape in one blue chrome hue
//   - a lit floor grid, glass walls, a landing ring under every machine
//   - a tag plate per machine that shows on click, on hover, and for a warning or an alarm
//   - a perspective camera that fits the whole hall (LOG-104)
// Function over looks (LOG-105): no bloom, no auto-orbit, shared materials, one merged mesh per
// machine and per static material. MOTION (on) moves the flows and verdict pulses at FPS_CAP. With
// MOTION off, the map draws a frame only when something changed.
// The props and the pick contract are unchanged, so MapPanel and Console need no change for it.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import SpriteText from "three-spritetext";
import { edgeRGB, HEX, HOLO } from "./lib/palette";

// ── switches ──────────────────────────────────────────────────────────────────────────────
// MOTION false: the map draws a frame only when data, the selection, the pointer, or the camera
// changes. The causal path shows fixed arrowheads from cause to effect, and the flows are off.
// MOTION true: energy and coolant flows move at measured speeds, pulses march along the causal
// path, and the map repaints at FPS_CAP while any of them runs. It ignores the browser's reduced-motion
// setting (the operator's choice, LOG-105): the motion carries measured values, and Windows reports
// reduced motion whenever its "Animation effects" setting is off.
const MOTION = true;
const FPS_CAP = 30;
const STATE_PLATES = true;  // a machine or rail in warning or alarm keeps its plate: an abnormal state is never silent
const FOV = 38;             // vertical field of view in degrees: about a 35 mm lens, mild foreshortening
const ARROW_STEP = 34;      // world units between two arrowheads on a causal path

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
const boxG = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cylG = (rt, rb, h, seg = 18) => new THREE.CylinderGeometry(rt, rb, h, seg);

// Bake a position and a rotation into a geometry, so many parts can merge into one draw call.
const _m4 = new THREE.Matrix4(), _q = new THREE.Quaternion(), _e = new THREE.Euler(), _p = new THREE.Vector3(), _one = new THREE.Vector3(1, 1, 1);
const place = (g, x, y, z, rot) => {
  _e.set(...(rot || [0, 0, 0]));
  return g.applyMatrix4(_m4.compose(_p.set(x, y, z), _q.setFromEuler(_e), _one));
};

// Shared materials and geometries carry userData.shared. A rebuild keeps them, and unmount frees them.
function disposeGroup(g) {
  g.traverse((o) => {
    if (o.isInstancedMesh) o.dispose();
    if (o.geometry && !o.geometry.userData.shared) o.geometry.dispose();
    for (const m of [].concat(o.material || [])) if (!m.userData.shared) { m.map?.dispose?.(); m.dispose(); }
  });
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
  // the unit that cools the loop: the api names it in loop.chiller, the dev mock only marks its kind
  const chiller = plant?.loop?.chiller?.name || devs.find(([, d]) => d.kind === "chiller")?.[0] || null;
  const cooled = devs.filter(([, d]) => d.cooled).map(([n]) => n);
  const zMin = rowZ(0), zMax = rowZ(Math.max(rails.length - 1, 1));
  const cells = Object.entries(plant?.cells || {}).map(([name, c]) => ({ name, plc: c.plc, machines: c.machines || [] }));
  return { rails, devs: devs.map(([n]) => n), pos, railGeom, loopName, chiller: loopName && pos[chiller] ? chiller : null, cooled, pumpX: -14, W: maxX, zMin, zMax, cells };
}

// ── machine silhouettes ─────────────────────────────────────────────────────────────────────
// Each returns the parts of one machine, centred on (0, 0, 0): a geometry, its offset, an optional
// rotation, a role, and whether it draws an outline. "body" parts merge into one mesh that takes the
// state color. "window" and "scan" parts keep their own light. Only the parts of the outer shape get an
// outline (`o`), so fins, rollers, legs, and small fittings add no lines. The +z face is the one the
// 3D camera sees, so doors, windows and fins sit there. The pick volume is added by the caller.
function silhouette(kind, p) {
  const parts = [];
  const add = (g, x = 0, y = 0, z = 0, opt = {}) => parts.push({ g, x, y, z, rot: opt.rot || null, role: opt.role || "body", outline: !!opt.o });
  const O = { o: true };
  const { w, d, h } = p;
  if (kind === "press") {
    add(boxG(w, h * 0.22, d), 0, h * 0.11, 0, O);                                     // base plinth
    for (const sx of [-1, 1]) add(boxG(w * 0.16, h * 0.78, d * 0.6), sx * w * 0.36, h * 0.22 + h * 0.39, 0, O);  // uprights
    add(boxG(w, h * 0.18, d * 0.66), 0, h * 0.91, 0, O);                               // crown
    add(boxG(w * 0.34, h * 0.3, d * 0.42), 0, h * 0.56);                               // ram
    add(boxG(w * 0.58, h * 0.07, d * 0.62), 0, h * 0.26);                              // bolster
  } else if (kind === "cnc") {
    add(boxG(w, h, d), 0, h / 2, 0, O);                                                // enclosure
    add(boxG(w * 0.56, h * 0.36, 1.5), 0, h * 0.55, d / 2 + 0.8, { role: "window" });  // window
    add(boxG(w * 0.22, h * 0.6, d * 0.3), w * 0.38 + w * 0.11, h * 0.3, -d * 0.15);    // control pendant column
  } else if (kind === "furnace") {
    const r = Math.min(w, d) * 0.42;
    add(cylG(r, r, h * 0.72, 28), 0, h * 0.36, 0, O);                                  // shell
    add(new THREE.SphereGeometry(r, 28, 14, 0, Math.PI * 2, 0, Math.PI / 2), 0, h * 0.72);  // dome
    add(cylG(r * 0.18, r * 0.18, h * 0.5, 12), r * 0.55, h * 0.72 + h * 0.25);         // flue
    add(new THREE.TorusGeometry(r * 1.01, 1.4, 8, 40), 0, h * 0.5, 0, { rot: [Math.PI / 2, 0, 0] });  // coil band
  } else if (kind === "compressor") {
    add(boxG(w, h * 0.16, d), 0, h * 0.08, 0, O);                                      // skid
    add(cylG(d * 0.3, d * 0.3, w * 0.74, 24), 0, h * 0.16 + d * 0.3, d * 0.1, { rot: [0, 0, Math.PI / 2], o: true });  // receiver tank
    add(boxG(w * 0.28, h * 0.44, d * 0.4), -w * 0.3, h * 0.16 + h * 0.22, -d * 0.28, O);  // motor block
    add(cylG(2, 2, h * 0.5, 8), w * 0.32, h * 0.16 + h * 0.25, -d * 0.3);              // gauge post
  } else if (kind === "chiller") {
    add(boxG(w, h * 0.7, d), 0, h * 0.35, 0, O);                                       // condenser block
    for (let i = 0; i < 7; i++) add(boxG(1.4, h * 0.56, 1.6), -w * 0.36 + i * (w * 0.72 / 6), h * 0.35, d / 2 + 0.9);  // fins
    add(boxG(w * 0.7, h * 0.12, d * 0.7), 0, h * 0.76, 0, O);                          // fan deck
    add(new THREE.TorusGeometry(Math.min(w, d) * 0.26, 1.6, 8, 36), 0, h * 0.84, 0, { rot: [Math.PI / 2, 0, 0] });  // fan ring
  } else if (kind === "conveyor") {
    add(boxG(w, h * 0.34, d * 0.72), 0, h * 0.66, 0, O);                               // belt frame
    for (const sx of [-0.42, 0.42]) add(boxG(3, h * 0.5, 3), sx * w, h * 0.25);        // legs
    const n = Math.max(3, Math.floor(w / 13));
    for (let i = 0; i < n; i++) add(cylG(d * 0.14, d * 0.14, d * 0.76, 12), -w * 0.46 + (i + 0.5) * (w * 0.92 / n), h * 0.9, 0, { rot: [Math.PI / 2, 0, 0] });  // rollers
  } else if (kind === "scanner") {
    for (const sx of [-1, 1]) add(boxG(4, h, 4), sx * (w * 0.42), h / 2, 0, O);       // gantry posts
    add(boxG(w, 5, 6), 0, h - 2.5, 0, O);                                              // cross beam
    add(boxG(w * 0.28, h * 0.22, d * 0.5), 0, h * 0.72);                               // scanner head
    add(boxG(w * 0.86, 0.6, d * 0.9), 0, h * 0.3, 0, { role: "scan" });                // scan plane
  } else if (kind === "packager") {
    add(boxG(w, h * 0.55, d), 0, h * 0.28, 0, O);
    add(boxG(w * 0.5, h * 0.45, d * 0.6), 0, h * 0.55 + h * 0.2, 0, O);
  } else {
    add(boxG(w, h, d), 0, h / 2, 0, O);
  }
  return parts;
}

export default function Floor({ plant, graph, selected, onPick, view = { mode: "3d", n: 0 } }) {
  const wrapRef = useRef();
  const stateRef = useRef({});
  const kickRef = useRef(null);
  stateRef.current = { plant, graph, selected, onPick, view };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const moving = MOTION;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(C.bg);
    scene.add(new THREE.AmbientLight(0x9fb4ff, 0.55));
    scene.add(new THREE.HemisphereLight(0x5f8cff, 0x05070c, 0.45));
    const sun = new THREE.DirectionalLight(0xd9e6ff, 0.75);
    sun.position.set(-220, 420, 300);
    scene.add(sun);
    // A perspective camera: a near machine shows larger than a far one, as in a photograph. An
    // orthographic camera draws the far end of the hall at the same size, and the eye reads that as wider.
    const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 8000);
    const paint = () => renderer.render(scene, camera);   // straight to the canvas, so its MSAA applies

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.rotateSpeed = 0.7;           // the wheel moves the camera in and out; fit() sets the distance limits
    controls.minPolarAngle = 0.05;
    controls.maxPolarAngle = 1.36;
    controls.cursorStyle = "grab";

    // ── shared materials: a state change swaps the material of an object, it never edits one ──
    const SHARED = [];
    const keep = (x) => { x.userData.shared = true; SHARED.push(x); return x; };
    const solidMat = (color, opts) => keep(lambert(color, opts));
    const lineMat = (color, opacity) => keep(new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
    // a flat ring needs one pass: three.js draws a transparent double-sided material twice unless told not to
    const glassMat = (color, opacity) => keep(new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthWrite: false, forceSinglePass: true }));
    const BODY = { ok: solidMat(C.body), strained: solidMat(C.body, { emissive: C.amber, emissiveIntensity: 0.2 }),
      hot: solidMat(C.body, { emissive: C.red, emissiveIntensity: 0.32 }), trip: solidMat(C.bodyTripped) };
    const EDGE = { ok: lineMat(C.holo, 0.55), strained: lineMat(C.amber, 0.95), hot: lineMat(C.red, 0.95), trip: lineMat(C.wire, 0.55) };
    const RING = { ok: glassMat(C.holo, 0.32), strained: glassMat(C.amber, 0.7), hot: glassMat(C.red, 0.7), trip: glassMat(C.holo, 0.32) };
    const BUS = { ok: solidMat(C.wire), strained: solidMat(C.amber, { emissive: C.amber, emissiveIntensity: 0.5 }),
      hot: solidMat(C.red, { emissive: C.red, emissiveIntensity: 0.5 }) };
    const LAMP = { ok: solidMat(C.teal, { emissive: C.teal, emissiveIntensity: 1.2 }), strained: solidMat(C.amber, { emissive: C.amber, emissiveIntensity: 1.2 }),
      hot: solidMat(C.red, { emissive: C.red, emissiveIntensity: 1.2 }), off: solidMat(C.wire, { emissive: C.wire, emissiveIntensity: 0.3 }) };
    const M = {
      slab: solidMat(C.slab), wall: solidMat(C.wall, { transparent: true, opacity: 0.6 }), door: solidMat(C.wall), wire: solidMat(C.wire),
      window: solidMat(C.window, { emissive: C.window, emissiveIntensity: 1.4 }), cncWindow: solidMat(C.window, { emissive: C.window, emissiveIntensity: 0.9 }),
      scan: solidMat(C.window, { emissive: C.holoBright, emissiveIntensity: 0.6, transparent: true, opacity: 0.35 }),
      lane: solidMat(C.holo, { emissive: C.holo, emissiveIntensity: 0.25 }), pipe: solidMat(C.pipe, { emissive: C.pipe, emissiveIntensity: 0.6 }),
      gridFine: lineMat(C.grid, 0.1), gridMajor: lineMat(C.holo, 0.16),
      edge: lineMat(C.holo, 0.5), edgeDim: lineMat(C.holo, 0.35), edgeBright: lineMat(C.holoBright, 0.5), edgeCool: lineMat(C.coolant, 0.5),
      pick: keep(new THREE.MeshBasicMaterial()), sel: keep(new THREE.MeshBasicMaterial({ color: C.sel })), selRing: glassMat(C.sel, 0.55),
    };
    const G = { lamp: keep(new THREE.CylinderGeometry(3, 3, 7, 12)), cabLamp: keep(new THREE.CylinderGeometry(2.6, 2.6, 6, 10)),
      pulse: keep(new THREE.SphereGeometry(3.6, 12, 12)), arrow: keep(new THREE.ConeGeometry(4.4, 12, 12)) };

    const staticG = new THREE.Group(), liveG = new THREE.Group(), selG = new THREE.Group(), flowG = new THREE.Group();
    scene.add(staticG, liveG, selG, flowG);

    const R = { geo: null, topoSig: "", bodies: {}, lamps: {}, rings: {}, sprites: {}, spriteTxt: {},
      busBars: {}, cabinets: {}, edgeSig: "", pulses: [], flows: [], viewMode: "3d", userMoved: false,
      pick: [], foot: {}, selKey: null, viewKey: null, dirty: true, selRing: null, hoverKey: null, stateKeys: new Set() };

    // A frame runs only on demand: kick() asks for one. With MOTION, the frame asks for the next one
    // while a flow or a pulse runs.
    let raf = 0, inFrame = false;
    const kick = () => { R.dirty = true; if (!raf && !inFrame) raf = requestAnimationFrame(frame); };

    // No fog (operator, LOG-105): every machine keeps its full contrast at any distance. The clip planes
    // follow the camera. The near plane moves in with it, so a close zoom does not cut a machine open,
    // and the far plane stays past the far corner of the hall.
    const hallC = new THREE.Vector3();
    const followDepth = () => {
      const b = hallBox();
      const r = Math.hypot(b[1] - b[0], b[3] - b[2], b[5] - b[4]) / 2;      // half the hall diagonal
      const dist = camera.position.distanceTo(hallC.set((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2));
      camera.near = Math.max(0.5, camera.position.distanceTo(controls.target) / 200);
      camera.far = dist + r * 4;
      camera.updateProjectionMatrix();
    };

    // Move the camera along its line of sight until the whole hall box fits the view with a small
    // margin. Each box corner needs a distance of (its depth toward the camera) + (its offset / tan).
    const corner = new THREE.Vector3(), inv = new THREE.Quaternion(), look = new THREE.Vector3();
    const hallBox = () => {
      const g = R.geo;
      return g ? [-60, g.W + 40, 0, BUS_Y + 28, g.zMin - 70, g.zMax + 70] : [0, 600, 0, 150, -130, 130];
    };
    const fit = () => {
      const b = hallBox(), target = controls.target;
      look.subVectors(camera.position, target).normalize();
      camera.position.copy(target).add(look);
      camera.lookAt(target);
      inv.copy(camera.quaternion).invert();
      const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / 1.05, tanH = tanV * camera.aspect;
      let dist = 0;
      for (const x of [b[0], b[1]]) for (const y of [b[2], b[3]]) for (const z of [b[4], b[5]]) {
        corner.set(x, y, z).sub(target).applyQuaternion(inv);
        dist = Math.max(dist, corner.z + Math.abs(corner.y) / tanV, corner.z + Math.abs(corner.x) / tanH);
      }
      camera.position.copy(target).addScaledVector(look, dist);
      controls.minDistance = dist * 0.15; controls.maxDistance = dist * 2.5;
    };

    const setSize = () => {
      const cw = el.clientWidth || 800, ch = el.clientHeight || 540;
      renderer.setSize(cw, ch, false);
      camera.aspect = cw / Math.max(ch, 1);
      camera.updateProjectionMatrix();
      if (!R.userMoved) { fit(); controls.update(); followDepth(); }   // a panel resize keeps the whole hall in view
      kick();
    };

    const home = (mode) => {
      R.viewMode = mode;
      const plan = mode === "plan";
      const b = hallBox();
      controls.target.set((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2);
      camera.position.setFromSphericalCoords(1, plan ? 0.12 : Math.PI / 2 - 0.64, plan ? 0 : 0.62).add(controls.target);
      R.userMoved = false;
      setSize();
    };
    controls.addEventListener("start", () => { R.userMoved = true; });
    controls.addEventListener("change", () => { followDepth(); kick(); });

    // Selection: holo corner brackets on the slab plus a soft ring, both in the chrome blue.
    const buildSel = (id) => {
      disposeGroup(selG); R.selRing = null;
      const f = id ? R.foot[id] : null;
      if (!f) return;
      const m = 9, w = f.w + 2 * m, d = f.d + 2 * m, L = Math.max(10, Math.min(w, d) * 0.32), T = 2.2;
      const bars = [];
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        const x = f.cx + sx * (w / 2), z = f.cz + sz * (d / 2);
        bars.push(place(boxG(L, 1.2, T), x - (sx * L) / 2, 1.2, z), place(boxG(T, 1.2, L), x, 1.2, z - (sz * L) / 2));
      }
      selG.add(new THREE.Mesh(mergeGeometries(bars), M.sel));
      bars.forEach((g) => g.dispose());
      const rr = Math.max(w, d) * 0.62;
      const ring = new THREE.Mesh(new THREE.RingGeometry(rr - 2.4, rr, 64), M.selRing);
      ring.rotation.x = -Math.PI / 2; ring.position.set(f.cx, 0.9, f.cz); selG.add(ring);
      R.selRing = ring;
    };

    // Which plates show: the selected asset's, the hovered asset's, and (STATE_PLATES) those of every
    // machine and rail in warning or alarm. Everything else stays hidden, so a calm hall reads calm.
    const plateKey = (id) => {
      const g = R.geo; if (!g || !id) return null;
      if (g.pos[id]) return id;
      if (g.railGeom[id]) return `rail:${id}`;
      if (id === g.loopName) return `loop:${id}`;
      const cab = Object.entries(R.cabinets).find(([, c]) => c.plc === id);
      return cab ? `cab:${cab[0]}` : null;
    };
    const applyPlates = () => {
      const want = new Set([plateKey(R.selKey), plateKey(R.hoverKey), ...(STATE_PLATES ? R.stateKeys : [])].filter(Boolean));
      let changed = false;
      for (const [k, sp] of Object.entries(R.sprites)) { const v = want.has(k); if (sp.visible !== v) { sp.visible = v; changed = true; } }
      if (changed) R.dirty = true;
      return changed;
    };
    // SpriteText draws a new canvas on every property write, so write only a changed value.
    const setPlate = (sp, key, txt, color, border) => {
      if (R.spriteTxt[key] !== txt) { sp.text = txt; R.spriteTxt[key] = txt; }
      if (color && sp.color !== color) sp.color = color;
      if (border && sp.borderColor !== border) sp.borderColor = border;
    };

    const pickable = (mesh, kind, id, foot) => {
      mesh.userData = { kind, id };
      R.pick.push(mesh);
      if (foot && !R.foot[id]) R.foot[id] = foot;
    };
    // An invisible pick volume. The raycaster ignores `visible`, and the renderer skips the object.
    const proxy = (w, h, d, x, y, z, kind, id, foot) => {
      const m = new THREE.Mesh(boxG(w, h, d), M.pick);
      m.position.set(x, y, z); m.visible = false; staticG.add(m);
      pickable(m, kind, id, foot);
    };

    // The static batch: every mesh and line that never changes color. flush() merges each material's
    // geometries into one object, so the whole room costs one draw call per material.
    let batch = null;
    const put = (mat, geo) => { const list = batch.get(mat) || []; list.push(geo); batch.set(mat, list); return geo; };
    const solid = (g, mat, x, y, z, rot) => put(mat, place(g, x, y, z, rot));
    const outline = (geo, mat) => put(mat, new THREE.EdgesGeometry(geo, 22));
    const flush = () => {
      for (const [mat, geos] of batch) {
        const merged = mergeGeometries(geos);
        geos.forEach((g) => g.dispose());
        staticG.add(mat.isLineBasicMaterial ? new THREE.LineSegments(merged, mat) : new THREE.Mesh(merged, mat));
      }
      batch = null;
    };

    // the lit floor grid: fine lines every 40 units, a brighter line every 200
    const gridG = (x0, x1, z0, z1, y, step) => {
      const pts = [];
      for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) pts.push(x, y, z0, x, y, z1);
      for (let z = Math.ceil(z0 / step) * step; z <= z1; z += step) pts.push(x0, y, z, x1, y, z);
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      return geo;
    };
    // a soft landing ring under each machine: chrome blue at rest, the state color under a verdict
    const landing = (cx, cz, w, d) => {
      const rr = Math.max(w, d) * 0.56;
      const ring = new THREE.Mesh(new THREE.RingGeometry(rr - 1.6, rr, 56), RING.ok);
      ring.rotation.x = -Math.PI / 2; ring.position.set(cx, 0.7, cz);
      return ring;
    };
    const plate = (size, x, y, z, align = "center") => {
      const s = new SpriteText("", size, C.text);
      s.fontFace = "Consolas, 'Cascadia Code', monospace"; s.textAlign = align;
      s.backgroundColor = "rgba(5, 10, 22, 0.78)"; s.borderColor = HOLO.plateEdge; s.borderWidth = 0.05; s.borderRadius = 0.25; s.padding = 0.35;
      s.position.set(x, y, z);
      s.material.depthTest = false; s.renderOrder = 10;   // drawn last and on top: a pole or a lamp never hides a value
      s.visible = false;                 // applyPlates() shows it on click, on hover, or for a warning or an alarm
      return s;
    };
    // MOTION only: n particles marching along a straight run from a to b, one instanced draw call.
    // speedFn() gives units per second from a measured value.
    const _fm = new THREE.Matrix4(), _fp = new THREE.Vector3();
    const stepFlow = (f, dt) => {
      const gap = f.len / f.n;
      f.t = (f.t + dt * f.speed) % gap;                      // the particles are alike, so one phase moves them all
      for (let i = 0; i < f.n; i++) {
        _fp.lerpVectors(f.a, f.b, (f.t + i * gap) / f.len);
        f.mesh.setMatrixAt(i, _fm.makeTranslation(_fp.x, _fp.y, _fp.z));
      }
      f.mesh.instanceMatrix.needsUpdate = true;
    };
    const addFlow = (a, b, color, size, speedFn) => {
      if (!moving) return;
      const len = a.distanceTo(b), n = Math.max(2, Math.floor(len / 46));
      const mesh = new THREE.InstancedMesh(boxG(...size), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, depthWrite: false }), n);
      mesh.frustumCulled = false;                           // the instances move, so a cached bounding sphere would be wrong
      flowG.add(mesh);
      const f = { a, b, len, n, mesh, speedFn, speed: speedFn(), t: 0 };
      R.flows.push(f); stepFlow(f, 0);
    };

    const buildStatic = (plantNow) => {
      disposeGroup(staticG); disposeGroup(flowG);
      R.bodies = {}; R.lamps = {}; R.rings = {}; R.sprites = {}; R.spriteTxt = {}; R.busBars = {}; R.cabinets = {}; R.flows = [];
      R.pick = []; R.foot = {}; R.selKey = null;
      const geo = layout(plantNow);
      R.geo = geo;
      if (!geo) return;
      const { rails, pos, railGeom, loopName, cooled, pumpX, W, zMin, zMax } = geo;
      const zMid = (zMin + zMax) / 2;
      const X0 = -130, X1 = W + 130, Z0 = zMin - 130, Z1 = zMax + 130;
      batch = new Map();

      // room: slab, grid, glass walls with thin edges, window strips
      solid(boxG(X1 - X0, 8, Z1 - Z0), M.slab, (X0 + X1) / 2, -4, zMid);
      put(M.gridFine, gridG(X0, X1, Z0, Z1, 0.4, 40));
      put(M.gridMajor, gridG(X0, X1, Z0, Z1, 0.5, 200));
      for (const z of [zMin - 62, zMax + 62]) solid(boxG(X1 - X0 - 90, 1, 2.4), M.lane, W / 2, 0.6, z);
      outline(solid(boxG(X1 - X0, 110, 6), M.wall, (X0 + X1) / 2, 55, Z0 + 4), M.edgeDim);
      outline(solid(boxG(6, 110, Z1 - Z0), M.wall, X0 + 4, 55, zMid), M.edgeDim);
      for (let i = 0; i < Math.max(2, Math.round(W / 260)); i++) outline(solid(boxG(150, 30, 2), M.window, 60 + i * 260, 64, Z0 + 8), M.edgeBright);

      // rails: PSU cabinet, riser, overhead bus (it takes the state color), drops, energy flow
      rails.forEach((r) => {
        const g = railGeom[r], z = g.z;
        outline(solid(boxG(PSU_W, 64, 40), BODY.ok, g.psuCx, 32, z), M.edge);
        outline(solid(boxG(PSU_W * 0.7, 40, 1), M.door, g.psuCx, 30, z + 20.6), M.edge);
        proxy(PSU_W, 64, 40, g.psuCx, 32, z, "asset", r, { cx: g.psuCx, cz: z, w: PSU_W, d: 40 });
        solid(boxG(3.5, BUS_Y - 64, 3.5), M.wire, g.psuCx, 64 + (BUS_Y - 64) / 2, z);
        const busGeo = place(boxG(g.x1 - g.psuCx + 14, 5, 5), (g.psuCx + g.x1) / 2, BUS_Y, z);
        const bus = new THREE.Mesh(busGeo, BUS.ok); staticG.add(bus); R.busBars[r] = bus;
        outline(busGeo, M.edge);
        const lbl = plate(9, g.psuX + 100, BUS_Y + 15, z);
        staticG.add(lbl); R.sprites[`rail:${r}`] = lbl;
        for (const n of g.members) {
          const p = pos[n];
          solid(boxG(3, BUS_Y - p.h, 3), M.wire, p.cx, p.h + (BUS_Y - p.h) / 2, z);
        }
        addFlow(new THREE.Vector3(g.psuCx + 8, BUS_Y, z), new THREE.Vector3(g.x1 + 6, BUS_Y, z), C.flow, [7, 1.8, 1.8],
          () => { const a = stateRef.current.plant?.rails?.[r]?.amps ?? 0; return 10 + Math.min(1, a / 160) * 80; });
      });

      // machines: one merged body + one outline + landing ring + stack light + tag plate + pick volume
      for (const [n, p] of Object.entries(pos)) {
        const body = [], lines = [];
        for (const part of silhouette(p.kind, p)) {
          const g = place(part.g, p.cx + part.x, part.y, p.z + part.z, part.rot);
          if (part.role === "window") put(M.cncWindow, g);
          else if (part.role === "scan") put(M.scan, g);
          else { body.push(g); if (part.outline) lines.push(new THREE.EdgesGeometry(g, 22)); }
        }
        const bodyMesh = new THREE.Mesh(mergeGeometries(body), BODY.ok);
        const edgeLines = new THREE.LineSegments(mergeGeometries(lines), EDGE.ok);
        body.forEach((g) => g.dispose()); lines.forEach((g) => g.dispose());
        staticG.add(bodyMesh, edgeLines);
        const ring = landing(p.cx, p.z, p.w, p.d); staticG.add(ring); R.rings[n] = ring;
        R.bodies[n] = { body: bodyMesh, edges: edgeLines };
        proxy(p.w, p.h, p.d, p.cx, p.h / 2, p.z, "asset", n, { cx: p.cx, cz: p.z, w: p.w, d: p.d });
        solid(boxG(1.6, 14, 1.6), M.wire, p.cx + p.w / 2 - 6, p.h + 7, p.z - p.d / 2 + 6);
        const lamp = new THREE.Mesh(G.lamp, LAMP.ok);
        lamp.position.set(p.cx + p.w / 2 - 6, p.h + 17, p.z - p.d / 2 + 6); staticG.add(lamp); R.lamps[n] = lamp;
        const s = plate(7.2, p.cx, p.h + 24, p.z);
        staticG.add(s); R.sprites[n] = s;
      }

      // coolant: trench pipe, stubs to the cooled machines, the chiller's supply and return lines, pump,
      // coolant flow. The trench runs on to the chiller, because the chiller takes the heat out of the loop.
      if (loopName) {
        const ch = geo.chiller ? pos[geo.chiller] : null;
        const endX = Math.max(...cooled.map((n) => pos[n]?.cx || 0), ch ? ch.cx + 8 : 0, pumpX + 20);
        const pipeLen = endX - pumpX + 26;
        outline(solid(boxG(pipeLen, 4, 4), M.pipe, (pumpX + endX) / 2, PIPE_Y, 0), M.edgeCool);
        proxy(pipeLen, 4, 4, (pumpX + endX) / 2, PIPE_Y, 0, "asset", loopName);
        for (const n of cooled) {
          const p = pos[n]; if (!p) continue;
          const inner = p.z > 0 ? p.z - p.d / 2 : p.z + p.d / 2;
          solid(boxG(3.5, 3.5, Math.abs(inner)), M.pipe, p.cx, PIPE_Y, inner / 2);
        }
        if (ch) {                         // two lines, supply and return, so the chiller reads as the source, not a load
          const inner = ch.z > 0 ? ch.z - ch.d / 2 : ch.z + ch.d / 2;
          for (const dx of [-8, 8]) outline(solid(boxG(4.5, 4.5, Math.abs(inner)), M.pipe, ch.cx + dx, PIPE_Y, inner / 2), M.edgeCool);
        }
        outline(solid(boxG(26, 20, 20), M.pipe, pumpX - 6, 10, 0), M.edgeCool);
        proxy(26, 20, 20, pumpX - 6, 10, 0, "asset", loopName, { cx: pumpX - 6, cz: 0, w: 26, d: 20 });
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
        outline(solid(boxG(20, 46, 14), BODY.ok, cx, 23, zc), M.edge);
        if (cell.plc) proxy(20, 46, 14, cx, 23, zc, "plc", cell.plc, { cx, cz: zc, w: 20, d: 14 });
        const lamp = new THREE.Mesh(G.cabLamp, LAMP.off);
        lamp.position.set(cx, 50, zc); staticG.add(lamp);
        const s = plate(6.4, cx, 64, zc);
        staticG.add(s); R.sprites[`cab:${cell.name}`] = s;
        R.cabinets[cell.name] = { lamp, sprite: s, plc: cell.plc };
      }
      flush();
      if (!R.userMoved) home(R.viewMode);
    };

    // live causal overlay: one merged conduit per edge, with fixed arrowheads (or, with MOTION,
    // pulses marching src -> dst)
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
    const _up = new THREE.Vector3(0, 1, 0), _dir = new THREE.Vector3(), _aq = new THREE.Quaternion(), _am = new THREE.Matrix4();
    const buildOverlay = (edges) => {
      disposeGroup(liveG); R.pulses = [];
      for (const e of edges) {
        const pts = pathFor(e); if (!pts) continue;
        const w = e.render_weight ?? e.confidence ?? Math.abs(e.r || 0);
        const col = edgeColor(w);
        const boxes = [], segs = []; let total = 0;
        for (let i = 0; i < pts.length - 1; i++) {
          const [x1, y1, z1] = pts[i], [x2, y2, z2] = pts[i + 1];
          const len = Math.abs(x2 - x1) + Math.abs(y2 - y1) + Math.abs(z2 - z1);
          if (len < 0.5) continue;
          boxes.push(place(boxG(Math.max(Math.abs(x2 - x1), 2.6), Math.max(Math.abs(y2 - y1), 2.6), Math.max(Math.abs(z2 - z1), 2.6)),
            (x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2));
          segs.push({ a: new THREE.Vector3(x1, y1, z1), b: new THREE.Vector3(x2, y2, z2), len, off: total });
          total += len;
        }
        if (!boxes.length) continue;
        liveG.add(new THREE.Mesh(mergeGeometries(boxes), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.8 })));
        boxes.forEach((g) => g.dispose());
        if (moving) {
          const pm = new THREE.MeshBasicMaterial({ color: col });
          for (let k = 0; k < 2; k++) {
            const pulse = new THREE.Mesh(G.pulse, pm);
            liveG.add(pulse);
            R.pulses.push({ mesh: pulse, segs, total, t: (k / 2) * total });
          }
          continue;
        }
        // one arrowhead every ARROW_STEP along the path, pointing from cause to effect
        const marks = [];
        for (let t = ARROW_STEP / 2; t < total; t += ARROW_STEP) {
          const s = segs.find((x) => t >= x.off && t <= x.off + x.len);
          if (s) marks.push([s, (t - s.off) / Math.max(s.len, 1e-6)]);
        }
        if (!marks.length) continue;
        const arrows = new THREE.InstancedMesh(G.arrow, new THREE.MeshBasicMaterial({ color: col }), marks.length);
        marks.forEach(([s, u], i) => {
          _dir.subVectors(s.b, s.a).normalize();
          _p.lerpVectors(s.a, s.b, u);
          arrows.setMatrixAt(i, _am.compose(_p, _aq.setFromUnitVectors(_up, _dir), _one));
        });
        liveG.add(arrows);
      }
    };

    const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
    const inkOf = (st) => (st === "hot" || st === "trip" ? HEX.red : st === "strained" ? HEX.amber : null);

    const applyLive = () => {
      const { plant: pl, graph: g } = stateRef.current;
      if (!R.geo) return;
      const root = g?.root?.[0]?.pod;
      const victims = new Set((g?.blast_radius || []).map((b) => b.pod));
      const forecast = new Set((g?.incipient || []).map((f) => f.pod));
      R.stateKeys = new Set();
      for (const n of Object.keys(R.bodies)) {
        const d = pl?.devices?.[n] || {};
        const { body, edges } = R.bodies[n];
        const st = d.tripped ? "trip" : n === root ? "hot" : victims.has(n) || forecast.has(n) ? "strained" : "ok";
        if (st !== "ok") R.stateKeys.add(n);
        body.material = BODY[st]; edges.material = EDGE[st]; R.rings[n].material = RING[st];
        R.lamps[n].material = st === "trip" ? LAMP.hot : LAMP[st];
        const line2 = [d.amps != null ? `${fmt(d.amps)} A` : null, d.cooled && d.temp != null ? `${Math.round(d.temp)} °C` : null,
          d.throughput != null ? `${Math.round(d.throughput)} %` : null].filter(Boolean).join("  ");
        setPlate(R.sprites[n], n, `${n}${d.tripped ? "  ⌀ OPEN" : ""}\n${line2}`, inkOf(st) || C.text, inkOf(st) || HOLO.plateEdge);
      }
      for (const r of Object.keys(R.busBars)) {
        const st = r === root ? "hot" : victims.has(r) ? "strained" : "ok";
        if (st !== "ok") R.stateKeys.add(`rail:${r}`);
        R.busBars[r].material = BUS[st];
        const rl = pl?.rails?.[r];
        setPlate(R.sprites[`rail:${r}`], `rail:${r}`, `rail ${r}\n${rl?.volts != null ? fmt(rl.volts) : "—"} V   ${rl?.amps != null ? fmt(rl.amps, 0) : "—"} A`,
          null, inkOf(st) || HOLO.plateEdge);
      }
      for (const [cn, cab] of Object.entries(R.cabinets || {})) {
        const c = pl?.cells?.[cn] || {};
        cab.lamp.material = c.mode === "closed-loop" ? LAMP.ok : c.mode === "fail-open" ? LAMP.strained : LAMP.off;
        setPlate(cab.sprite, `cab:${cn}`, `${c.plc || cn}\n${c.mode || "no link"}`);
      }
      if (R.geo.loopName) {
        const key = `loop:${R.geo.loopName}`;
        const l = pl?.loop || {};
        if (R.sprites[key]) setPlate(R.sprites[key], key, `loop ${R.geo.loopName}\n${l.flow != null ? Math.round(l.flow) : "—"} L/min   ${l.t_supply != null ? fmt(l.t_supply) : "—"} °C`);
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
      if (key !== R.hoverKey) { R.hoverKey = key; if (applyPlates()) kick(); }   // the plate follows the pointer
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

    const ro = new ResizeObserver(() => setSize());
    ro.observe(el);
    let visible = true;
    const io = new IntersectionObserver(([e]) => { visible = !!e?.isIntersecting; if (visible) kick(); });
    io.observe(el);

    // Take in the props that changed since the last frame (the kickRef effect below asks for a frame).
    let lastPlant = null, lastGraph = null, lastPaint = 0;
    const sync = () => {
      const { plant: pl, graph: g, selected: selNow, view: viewNow } = stateRef.current;
      if (viewNow && viewNow.n !== R.viewKey) { R.viewKey = viewNow.n; home(viewNow.mode); }
      if (pl && pl !== lastPlant) {
        lastPlant = pl;
        const sig = JSON.stringify([Object.keys(pl.devices || {}), Object.entries(pl.devices || {}).map(([n, d]) => [n, d.rail, !!d.cooled]), Object.keys(pl.rails || {}), pl.loop?.name, pl.loop?.chiller?.name,
          Object.entries(pl.cells || {}).map(([n, c]) => [n, c.plc, c.machines])]);
        if (sig !== R.topoSig) { R.topoSig = sig; buildStatic(pl); }
        if (R.geo) applyLive();
        R.dirty = true;
      }
      if (g !== lastGraph) { lastGraph = g; if (R.geo) applyLive(); R.dirty = true; }
      if (R.geo && (selNow || null) !== R.selKey) { R.selKey = selNow || null; buildSel(R.selKey); applyPlates(); R.dirty = true; }
    };
    const frame = (now) => {
      raf = 0;
      if (!visible) return;                       // the IntersectionObserver kicks again when the map shows
      inFrame = true;
      sync();
      const animating = moving && (R.flows.length > 0 || R.pulses.length > 0 || !!R.selRing);
      if (animating && now - lastPaint >= 1000 / FPS_CAP) {
        const dt = Math.min((now - lastPaint) / 1000, 0.1);
        for (const f of R.flows) stepFlow(f, dt);
        for (const p of R.pulses) {
          p.t = (p.t + dt * 70) % Math.max(p.total, 1);
          const s = p.segs.find((x) => p.t >= x.off && p.t <= x.off + x.len);
          if (s) p.mesh.position.lerpVectors(s.a, s.b, (p.t - s.off) / Math.max(s.len, 1e-6));
        }
        if (R.selRing) R.selRing.material.opacity = 0.42 + 0.18 * Math.sin(now / 420);
        R.dirty = true;
      }
      if (R.dirty) { paint(); R.dirty = false; lastPaint = now; }
      inFrame = false;
      if (animating) raf = requestAnimationFrame(frame);
    };
    kickRef.current = kick;
    home(R.viewMode);

    return () => {
      kickRef.current = null;
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      disposeGroup(staticG); disposeGroup(liveG); disposeGroup(selG); disposeGroup(flowG);
      SHARED.forEach((x) => x.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      el.removeChild(renderer.domElement);
    };
  }, []);

  // A frame for every prop change. Between changes the map draws nothing.
  useEffect(() => { kickRef.current?.(); }, [plant, graph, selected, view]);

  const empty = !plant?.devices || !Object.keys(plant.devices).length;
  return (
    <div ref={wrapRef} className="floor3d">
      {empty && <div className="floor3d-wait">waiting for plant telemetry…</div>}
    </div>
  );
}
