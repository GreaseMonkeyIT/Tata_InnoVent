"use client";
// TRUE-3D plant floor (2G, LOG-038) — plain three.js, per the operator's reference render:
// an assembly-hall isometric. Two production lines (one per rail), room shell, lane-marked
// slab, LOD-1 boxy machines with stack lights, overhead bus bars with 90° drops, the coolant
// trench running BETWEEN the lines. Same doctrine as the SVG interim (Floor2D.jsx): the wiring
// is STATIC — the graph exists from the get-go; the live causal verdict only paints activity
// onto the fixed conduits (root red, victims amber, pulses marching src → dst). Nothing
// floats, nothing rearranges. Clay aesthetic: neutral blocks, meaning-colors only for status.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import SpriteText from "three-spritetext";
import { edgeRGB, HEX } from "./lib/palette";

// LOD-1 footprints (w along the line, d across, h tall) by asset kind — cosmetic only.
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

// Console palette (LOG-062): the hall sits in the black map well, with navy-gray neutral blocks.
// The coolant pipe is a deep shade of the deck teal. Status lamps are the only colored marks:
// teal normal, amber warning, red alarm (LOG-080). The selection brackets use the command blue edge.
const hex = (s) => parseInt(s.slice(1), 16);
const C = {
  bg: hex(HEX.void), slab: 0x0d1017, lane: 0x252c3a, wall: 0x141924, window: 0x22304a,
  body: 0x434b5a, bodyTripped: 0x23272f, wire: 0x373e4c, pipe: 0x0a4a44,
  red: hex(HEX.red), amber: hex(HEX.amber), teal: hex(HEX.teal), sel: hex(HEX.blueEdge), text: HEX.text,
};
const GAP = 50, PSU_W = 34, BUS_Y = 122, ROW_Z = 62, PIPE_Y = 3;

// Same contention ramp as Graph.jsx: gray, then amber, then red.
const edgeColor = (w) => new THREE.Color(`rgb(${edgeRGB(w).join(",")})`);

const box = (w, h, d, color, opts = {}) =>
  new THREE.Mesh(new THREE.BoxGeometry(w, h, d),
    new THREE.MeshLambertMaterial({ color, ...opts }));

function disposeGroup(g) {
  g.traverse((o) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
  g.clear();
}

// Deterministic layout: one production line per rail (row A back, row B front), machines in
// /state order, wide gaps; the coolant trench runs between the first two lines at z=0.
// 2H: a third rail (the psu-c spare feeder) and any later rail get their own rows in front of B.
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
      pos[n] = { cx: x + s.w / 2, z, ...s };
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

// The console map (LOG-062): a click on a machine, a PSU cabinet, the coolant pump, or a PLC
// cabinet calls onPick(kind, id), and blue corner brackets on the slab mark the selected asset.
// `view` is a camera preset request { mode: "iso" | "plan", n }. Each new n aims and frames the
// camera for that preset, so a second click on the active preset resets the view.
export default function Floor({ plant, graph, selected, onPick, view = { mode: "iso", n: 0 } }) {
  const wrapRef = useRef();
  const stateRef = useRef({});
  stateRef.current = { plant, graph, selected, onPick, view };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(C.bg);
    scene.add(new THREE.AmbientLight(0xffffff, 0.85));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(-220, 420, 300);
    scene.add(sun);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -2000, 4000);

    // A normal orbit camera (LOG-062, operator request): drag rotates around the plant, right-drag
    // pans, and the wheel zooms. A rotation never changes the zoom. The scene follows the cursor.
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.rotateSpeed = 0.7;
    controls.minZoom = 0.5;
    controls.maxZoom = 5;
    controls.minPolarAngle = 0.05;      // just short of straight down, so the up vector stays stable
    controls.maxPolarAngle = 1.36;      // about 12° above the slab: the camera never goes under the floor
    controls.cursorStyle = "grab";

    const staticG = new THREE.Group(), liveG = new THREE.Group(), selG = new THREE.Group();
    scene.add(staticG, liveG, selG);

    // mutable registries the frame loop reads. `dirty` = render-on-demand: the static hall
    // paints only when something changed (data poll, camera, pulses) — a steady floor costs
    // zero GPU, and an off-screen one costs nothing at all (visibility gate below).
    // `pick` holds the clickable meshes, and `foot` holds each asset's footprint for the brackets.
    // `halfH` is the frustum half-height in world units. `userMoved` is true after the operator
    // moves the camera, so a new plant layout never yanks a view the operator chose.
    const R = { geo: null, topoSig: "", bodies: {}, lamps: {}, sprites: {}, spriteTxt: {},
      busBars: {}, cabinets: {}, edgeSig: "", pulses: [], halfH: 300, viewMode: "iso", userMoved: false,
      pick: [], foot: {}, selKey: null, viewKey: null, dirty: true };

    // The frustum keeps its height in world units, so a panel resize never zooms the scene.
    const setFrustum = () => {
      const cw = el.clientWidth || 800, ch = el.clientHeight || 540;
      renderer.setSize(cw, ch, false);
      const aspect = cw / Math.max(ch, 1);
      camera.left = -R.halfH * aspect; camera.right = R.halfH * aspect;
      camera.top = R.halfH; camera.bottom = -R.halfH;
      camera.updateProjectionMatrix();
      R.dirty = true;
    };

    // Home view for a preset. ISO looks ~37° down at the hall. PLAN looks almost straight down,
    // squared with the lines, as a schematic. The camera aims at the middle of the machine area
    // (PSU cabinets, pump, lines, bus labels), and the frustum sizes once so that area fits.
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
    };
    controls.addEventListener("start", () => { R.userMoved = true; });
    controls.addEventListener("change", () => { R.dirty = true; });

    // Teal corner brackets on the slab around the selected asset (the VISR bracket motif). Teal,
    // not the command blue, because a selected root machine glows red and blue next to red shifts.
    const buildSel = (id) => {
      disposeGroup(selG);
      const f = id ? R.foot[id] : null;
      if (!f) return;
      const m = 9, w = f.w + 2 * m, d = f.d + 2 * m, L = Math.max(10, Math.min(w, d) * 0.32), T = 2.6;
      for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
        const x = f.cx + sx * (w / 2), z = f.cz + sz * (d / 2);
        const a = new THREE.Mesh(new THREE.BoxGeometry(L, 1.4, T), new THREE.MeshBasicMaterial({ color: C.sel }));
        a.position.set(x - (sx * L) / 2, 1.2, z); selG.add(a);
        const b = new THREE.Mesh(new THREE.BoxGeometry(T, 1.4, L), new THREE.MeshBasicMaterial({ color: C.sel }));
        b.position.set(x, 1.2, z - (sz * L) / 2); selG.add(b);
      }
    };

    const pickable = (mesh, kind, id, foot) => {
      mesh.userData = { kind, id };
      R.pick.push(mesh);
      if (foot && !R.foot[id]) R.foot[id] = foot;
    };

    const buildStatic = (plantNow) => {
      disposeGroup(staticG);
      R.bodies = {}; R.lamps = {}; R.sprites = {}; R.spriteTxt = {}; R.busBars = {}; R.cabinets = {};
      R.pick = []; R.foot = {}; R.selKey = null;    // footprints changed, so the brackets rebuild
      const geo = layout(plantNow);
      R.geo = geo;
      if (!geo) return;
      const { rails, pos, railGeom, loopName, cooled, pumpX, W, zMin, zMax } = geo;
      const depth = zMax - zMin, zMid = (zMin + zMax) / 2;

      // room: slab + lane lines + back/left walls with window strips (sized to every row)
      const slab = box(W + 260, 8, depth + 260, C.slab); slab.position.set(W / 2, -4, zMid); staticG.add(slab);
      for (const z of [zMin - 62, zMax + 62]) {
        const lane = box(W + 170, 1.2, 3, C.lane); lane.position.set(W / 2, 0.6, z); staticG.add(lane);
      }
      const backW = box(W + 260, 110, 8, C.wall); backW.position.set(W / 2, 55, zMin - 126); staticG.add(backW);
      const leftW = box(8, 110, depth + 252, C.wall); leftW.position.set(-126, 55, zMid); staticG.add(leftW);
      for (let i = 0; i < Math.max(2, Math.round(W / 260)); i++) {
        const win = box(150, 34, 3, C.window, { emissive: C.window, emissiveIntensity: 0.55 });
        win.position.set(60 + i * 260, 62, zMin - 121); staticG.add(win);
      }

      // rails: PSU cabinet + riser + overhead bus + drops, one line per rail
      rails.forEach((r) => {
        const g = railGeom[r], z = g.z;
        const psu = box(PSU_W, 64, 40, C.body); psu.position.set(g.psuCx, 32, z); staticG.add(psu);
        pickable(psu, "asset", r, { cx: g.psuCx, cz: z, w: PSU_W, d: 40 });
        const riser = box(3.5, BUS_Y - 64, 3.5, C.wire); riser.position.set(g.psuCx, 64 + (BUS_Y - 64) / 2, z); staticG.add(riser);
        const bus = box(g.x1 - g.psuCx + 14, 5, 5, C.wire); bus.position.set((g.psuCx + g.x1) / 2, BUS_Y, z); staticG.add(bus);
        R.busBars[r] = bus;
        const lbl = new SpriteText("", 9.5, C.text);
        lbl.fontFace = "Consolas, monospace"; lbl.position.set(g.psuX + 96, BUS_Y + 13, z);
        staticG.add(lbl); R.sprites[`rail:${r}`] = lbl;
        for (const n of g.members) {
          const p = pos[n];
          const drop = box(3, BUS_Y - p.h, 3, C.wire); drop.position.set(p.cx, p.h + (BUS_Y - p.h) / 2, z); staticG.add(drop);
        }
      });

      // machines: body + stack light + label sprite
      for (const [n, p] of Object.entries(pos)) {
        const body = box(p.w, p.h, p.d, C.body);
        body.position.set(p.cx, p.h / 2, p.z); staticG.add(body); R.bodies[n] = body;
        pickable(body, "asset", n, { cx: p.cx, cz: p.z, w: p.w, d: p.d });
        const pole = box(1.6, 14, 1.6, C.wire); pole.position.set(p.cx + p.w / 2 - 6, p.h + 7, p.z - p.d / 2 + 6); staticG.add(pole);
        const lamp = new THREE.Mesh(new THREE.CylinderGeometry(3, 3, 7, 12),
          new THREE.MeshLambertMaterial({ color: C.teal, emissive: C.teal, emissiveIntensity: 0.8 }));
        lamp.position.set(p.cx + p.w / 2 - 6, p.h + 17, p.z - p.d / 2 + 6); staticG.add(lamp); R.lamps[n] = lamp;
        const s = new SpriteText("", 7.5, C.text);
        s.fontFace = "Consolas, monospace"; s.textAlign = "center";
        s.position.set(p.cx, p.h + 16, p.z); staticG.add(s); R.sprites[n] = s;
      }

      // coolant: trench pipe between the lines + 90° stubs to cooled machines + pump
      if (loopName) {
        const endX = Math.max(...cooled.map((n) => pos[n]?.cx || 0), pumpX + 20);
        const pipe = box(endX - pumpX + 26, 4, 4, C.pipe, { emissive: C.pipe, emissiveIntensity: 0.35 });
        pipe.position.set((pumpX + endX) / 2, PIPE_Y, 0); staticG.add(pipe);
        pickable(pipe, "asset", loopName);
        for (const n of cooled) {
          const p = pos[n]; if (!p) continue;
          const inner = p.z > 0 ? p.z - p.d / 2 : p.z + p.d / 2;
          const stub = box(3.5, 3.5, Math.abs(inner), C.pipe, { emissive: C.pipe, emissiveIntensity: 0.25 });
          stub.position.set(p.cx, PIPE_Y, inner / 2); staticG.add(stub);
        }
        const pump = box(26, 20, 20, C.pipe, { emissive: C.pipe, emissiveIntensity: 0.4 });
        pump.position.set(pumpX - 6, 10, 0); staticG.add(pump);
        pickable(pump, "asset", loopName, { cx: pumpX - 6, cz: 0, w: 26, d: 20 });
        const lbl = new SpriteText("", 8.5, C.text);
        lbl.fontFace = "Consolas, monospace"; lbl.position.set(pumpX - 6, 38, 8);
        staticG.add(lbl); R.sprites[`loop:${loopName}`] = lbl;
      }

      // 2H: one PLC cabinet per cell, beside its first machine, on the aisle side. The lamp shows the
      // live field link (teal closed-loop, amber fail-open, grey no link). It never animates on a timer.
      for (const cell of geo.cells) {
        const first = cell.machines.map((m) => pos[m]).find(Boolean);
        if (!first) continue;
        const zc = first.z + (first.z < 0 ? 1 : -1) * (first.d / 2 + 14);
        const cab = box(20, 46, 14, C.body); cab.position.set(first.cx - first.w / 2 - 4, 23, zc); staticG.add(cab);
        if (cell.plc) pickable(cab, "plc", cell.plc, { cx: first.cx - first.w / 2 - 4, cz: zc, w: 20, d: 14 });
        const lamp = new THREE.Mesh(new THREE.CylinderGeometry(2.6, 2.6, 6, 10),
          new THREE.MeshLambertMaterial({ color: C.wire, emissive: C.wire, emissiveIntensity: 0.4 }));
        lamp.position.set(first.cx - first.w / 2 - 4, 50, zc); staticG.add(lamp);
        const s = new SpriteText("", 6.5, C.text);
        s.fontFace = "Consolas, monospace"; s.position.set(first.cx - first.w / 2 - 4, 62, zc); staticG.add(s);
        R.cabinets[cell.name] = { lamp, sprite: s };
      }
      if (!R.userMoved) home(R.viewMode);   // a new layout re-frames only a camera the operator has not moved
    };

    // live causal overlay: axis-aligned conduit segments + pulses marching src → dst
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
      if (!a.rail || !b.rail || a.rail[2] !== b.rail[2]) return null;   // rail edges live on one line
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
            new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.85 }));
          seg.position.set((x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2);
          liveG.add(seg);
          segs.push({ a: new THREE.Vector3(x1, y1, z1), b: new THREE.Vector3(x2, y2, z2), len, off: total });
          total += len;
        }
        for (let k = 0; k < 2; k++) {
          const pulse = new THREE.Mesh(new THREE.SphereGeometry(3.4, 10, 10), new THREE.MeshBasicMaterial({ color: col }));
          liveG.add(pulse);
          R.pulses.push({ mesh: pulse, segs, total, t: (k / 2) * total });
        }
      }
    };

    const setLamp = (m, color, i = 0.9) => { m.material.color.setHex(color); m.material.emissive.setHex(color); m.material.emissiveIntensity = i; };

    const applyLive = () => {
      const { plant: pl, graph: g } = stateRef.current;
      if (!R.geo) return;
      const root = g?.root?.[0]?.pod;
      const victims = new Set((g?.blast_radius || []).map((b) => b.pod));
      const forecast = new Set((g?.incipient || []).map((f) => f.pod));   // a limit is near: the warning color too
      for (const n of Object.keys(R.bodies)) {
        const d = pl?.devices?.[n] || {};
        const body = R.bodies[n], lamp = R.lamps[n], sp = R.sprites[n];
        const st = d.tripped ? "trip" : n === root ? "hot" : victims.has(n) || forecast.has(n) ? "strained" : "ok";
        body.material.color.setHex(st === "trip" ? C.bodyTripped : C.body);
        body.material.emissive.setHex(st === "hot" ? C.red : st === "strained" ? C.amber : 0x000000);
        body.material.emissiveIntensity = st === "hot" ? 0.35 : st === "strained" ? 0.22 : 0;
        setLamp(lamp, st === "hot" || st === "trip" ? C.red : st === "strained" ? C.amber : C.teal);
        const txt = `${n}${d.tripped ? "  ⌀ OPEN" : ""}\n${d.amps != null ? d.amps.toFixed(1) + " A" : ""}${d.cooled && d.temp != null ? " · " + Math.round(d.temp) + " °C" : ""}`;
        if (R.spriteTxt[n] !== txt) { sp.text = txt; R.spriteTxt[n] = txt; }
        sp.color = st === "hot" || st === "trip" ? HEX.red : st === "strained" ? HEX.amber : C.text;
      }
      for (const r of Object.keys(R.busBars)) {
        const st = r === root ? C.red : victims.has(r) ? C.amber : C.wire;
        R.busBars[r].material.color.setHex(st);
        R.busBars[r].material.emissive?.setHex?.(st === C.wire ? 0x000000 : st);
        R.busBars[r].material.emissiveIntensity = st === C.wire ? 0 : 0.3;
        const v = pl?.rails?.[r]?.volts;
        const txt = `RAIL ${r.toUpperCase()} · ${v != null ? v.toFixed(1) : "—"} V`;
        if (R.spriteTxt[`rail:${r}`] !== txt) { R.sprites[`rail:${r}`].text = txt; R.spriteTxt[`rail:${r}`] = txt; }
      }
      for (const [cn, cab] of Object.entries(R.cabinets || {})) {
        const c = pl?.cells?.[cn] || {};
        const col = c.mode === "closed-loop" ? C.teal : c.mode === "fail-open" ? C.amber : C.wire;
        setLamp(cab.lamp, col, col === C.wire ? 0.3 : 0.9);
        const txt = `${c.plc || cn}\n${c.mode || "no link"}`;
        if (R.spriteTxt[`cab:${cn}`] !== txt) { cab.sprite.text = txt; R.spriteTxt[`cab:${cn}`] = txt; }
      }
      if (R.geo.loopName) {
        const key = `loop:${R.geo.loopName}`;
        const txt = `${R.geo.loopName.toUpperCase()} · ${pl?.loop?.flow != null ? Math.round(pl.loop.flow) : "—"} L/min`;
        if (R.spriteTxt[key] !== txt && R.sprites[key]) { R.sprites[key].text = txt; R.spriteTxt[key] = txt; }
      }
      const live = (g?.edges || []).filter((e) => e.state === "active" || e.state === "confirming");
      const sig = JSON.stringify(live.map((e) => [e.src, e.dst, e.signal, Math.round((e.render_weight ?? e.confidence ?? 0) * 8)]).sort());
      if (sig !== R.edgeSig) { R.edgeSig = sig; buildOverlay(live); }
    };

    // Picking. OrbitControls owns drag, pan, and zoom. A left press that moves less than 4 px is a
    // click, and the raycaster picks the asset under the pointer.
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
      if (!pressing && e.target === renderer.domElement) renderer.domElement.style.cursor = hitAt(e) ? "pointer" : "grab";
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

    // visibility gate: scrolled out of view = no work at all (the middle-mouse glide over the
    // rest of the page never contends with this canvas)
    let visible = true;
    const io = new IntersectionObserver(([e]) => {
      visible = !!e?.isIntersecting;
      if (visible) R.dirty = true;        // repaint once on return
    });
    io.observe(el);

    let raf, lastPlant = null, lastGraph = null;
    // frame delta in seconds (three r184 deprecates THREE.Clock)
    let lastT = performance.now();
    const clock = { getDelta: () => { const now = performance.now(), dt = (now - lastT) / 1000; lastT = now; return dt; } };
    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (!visible) { clock.getDelta(); return; }
      const { plant: pl, graph: g, selected: selNow, view: viewNow } = stateRef.current;
      // a preset request (ISO or PLAN, including a second click on the active one) resets the camera
      if (viewNow && viewNow.n !== R.viewKey) { R.viewKey = viewNow.n; home(viewNow.mode); }
      if (pl && pl !== lastPlant) {       // live state applies per data poll, not per frame
        lastPlant = pl;
        const sig = JSON.stringify([Object.keys(pl.devices || {}), Object.entries(pl.devices || {}).map(([n, d]) => [n, d.rail, !!d.cooled]), Object.keys(pl.rails || {}), pl.loop?.name,
          Object.entries(pl.cells || {}).map(([n, c]) => [n, c.plc, c.machines])]);
        if (sig !== R.topoSig) { R.topoSig = sig; buildStatic(pl); }
        if (R.geo) applyLive();
        R.dirty = true;
      }
      if (g !== lastGraph) {
        lastGraph = g;
        if (R.geo) applyLive();
        R.dirty = true;
      }
      if (R.geo && (selNow || null) !== R.selKey) { R.selKey = selNow || null; buildSel(R.selKey); R.dirty = true; }
      const dt = clock.getDelta();
      if (R.pulses.length) {              // incidents animate; a steady floor idles
        for (const p of R.pulses) {
          p.t = (p.t + dt * 70) % Math.max(p.total, 1);
          for (const s of p.segs) {
            if (p.t >= s.off && p.t <= s.off + s.len) {
              p.mesh.position.lerpVectors(s.a, s.b, (p.t - s.off) / Math.max(s.len, 1e-6));
              break;
            }
          }
        }
        R.dirty = true;
      }
      if (R.dirty) { renderer.render(scene, camera); R.dirty = false; }
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
      disposeGroup(staticG); disposeGroup(liveG); disposeGroup(selG);
      renderer.dispose();
      renderer.forceContextLoss();   // free the WebGL context now, so a plane toggle never piles up contexts
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
