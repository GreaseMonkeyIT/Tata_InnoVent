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
import SpriteText from "three-spritetext";

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

const C = {
  bg: 0x181b1f, slab: 0x14171c, lane: 0x2a303a, wall: 0x1d2127, window: 0x2b3340,
  body: 0x454c58, bodyTripped: 0x272b31, wire: 0x39404a, pipe: 0x1f4d57,
  red: 0xf2495c, amber: 0xff9830, teal: 0x5dcaa5, cyan: 0x36c5e0, text: "#ccccdc",
};
const GAP = 50, PSU_W = 34, BUS_Y = 122, ROW_Z = 62, PIPE_Y = 3;

// Same contention ramp as Graph.jsx / Floor2D.
function edgeColor(w) {
  const t = Math.min(Math.max(w ?? 0, 0), 1);
  const lo = [150, 158, 170], hi = [255, 80, 20];
  return new THREE.Color(`rgb(${lo.map((v, i) => Math.round(v + (hi[i] - v) * t)).join(",")})`);
}

const box = (w, h, d, color, opts = {}) =>
  new THREE.Mesh(new THREE.BoxGeometry(w, h, d),
    new THREE.MeshLambertMaterial({ color, ...opts }));

function disposeGroup(g) {
  g.traverse((o) => { o.geometry?.dispose?.(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.map?.dispose?.(); m.dispose?.(); }); });
  g.clear();
}

// Deterministic layout: one production line per rail (row A back, row B front), machines in
// /state order, wide gaps; the coolant trench runs between the lines at z=0.
function layout(plant) {
  const rails = Object.keys(plant?.rails || {});
  const devs = Object.entries(plant?.devices || {});
  if (!rails.length || !devs.length) return null;
  const pos = {}, railGeom = {};
  let maxX = 0;
  rails.forEach((r, i) => {
    const z = (i === 0 ? -1 : 1) * ROW_Z;
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
  return { rails, devs: devs.map(([n]) => n), pos, railGeom, loopName, cooled, pumpX: -14, W: maxX };
}

export default function Floor({ plant, graph }) {
  const wrapRef = useRef();
  const stateRef = useRef({});
  stateRef.current = { plant, graph };

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

    const staticG = new THREE.Group(), liveG = new THREE.Group();
    scene.add(staticG, liveG);

    // mutable registries the frame loop reads. `dirty` = render-on-demand: the static hall
    // paints only when something changed (data poll, camera, pulses) — a steady floor costs
    // zero GPU, and an off-screen one costs nothing at all (visibility gate below).
    const R = { geo: null, topoSig: "", bodies: {}, lamps: {}, sprites: {}, spriteTxt: {},
      busBars: {}, edgeSig: "", pulses: [], center: new THREE.Vector3(), azim: 0.62, zoomK: 1,
      dirty: true };

    const fit = () => {
      const cw = el.clientWidth || 800, ch = el.clientHeight || 540;
      renderer.setSize(cw, ch, false);
      const aspect = cw / Math.max(ch, 1);
      const halfW = (R.geo ? R.geo.W / 2 : 400) + 130;
      const halfH = Math.max(278, halfW / aspect);
      camera.left = -halfH * aspect; camera.right = halfH * aspect;
      camera.top = halfH; camera.bottom = -halfH;
      camera.updateProjectionMatrix();
    };
    const aimCamera = () => {
      const r = 900, elev = 0.64;                       // ~37° down, iso-ish
      camera.position.set(
        R.center.x + r * Math.cos(elev) * Math.sin(R.azim),
        R.center.y + r * Math.sin(elev),
        R.center.z + r * Math.cos(elev) * Math.cos(R.azim));
      camera.zoom = R.zoomK;
      camera.lookAt(R.center);
      camera.updateProjectionMatrix();
      R.dirty = true;
    };

    const buildStatic = (plantNow) => {
      disposeGroup(staticG);
      R.bodies = {}; R.lamps = {}; R.sprites = {}; R.spriteTxt = {}; R.busBars = {};
      const geo = layout(plantNow);
      R.geo = geo;
      if (!geo) return;
      const { rails, pos, railGeom, loopName, cooled, pumpX, W } = geo;
      R.center.set(W / 2, 50, 0);

      // room: slab + lane lines + back/left walls with window strips
      const slab = box(W + 260, 8, ROW_Z * 2 + 260, C.slab); slab.position.set(W / 2, -4, 0); staticG.add(slab);
      for (const z of [-ROW_Z - 62, ROW_Z + 62]) {
        const lane = box(W + 170, 1.2, 3, C.lane); lane.position.set(W / 2, 0.6, z); staticG.add(lane);
      }
      const backW = box(W + 260, 110, 8, C.wall); backW.position.set(W / 2, 55, -ROW_Z - 126); staticG.add(backW);
      const leftW = box(8, 110, ROW_Z * 2 + 252, C.wall); leftW.position.set(-126, 55, 0); staticG.add(leftW);
      for (let i = 0; i < Math.max(2, Math.round(W / 260)); i++) {
        const win = box(150, 34, 3, C.window, { emissive: C.window, emissiveIntensity: 0.55 });
        win.position.set(60 + i * 260, 62, -ROW_Z - 121); staticG.add(win);
      }

      // rails: PSU cabinet + riser + overhead bus + drops, one line per rail
      rails.forEach((r) => {
        const g = railGeom[r], z = g.z;
        const psu = box(PSU_W, 64, 40, C.body); psu.position.set(g.psuCx, 32, z); staticG.add(psu);
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
        for (const n of cooled) {
          const p = pos[n]; if (!p) continue;
          const inner = p.z > 0 ? p.z - p.d / 2 : p.z + p.d / 2;
          const stub = box(3.5, 3.5, Math.abs(inner), C.pipe, { emissive: C.pipe, emissiveIntensity: 0.25 });
          stub.position.set(p.cx, PIPE_Y, inner / 2); staticG.add(stub);
        }
        const pump = box(26, 20, 20, C.pipe, { emissive: C.pipe, emissiveIntensity: 0.4 });
        pump.position.set(pumpX - 6, 10, 0); staticG.add(pump);
        const lbl = new SpriteText("", 8.5, C.text);
        lbl.fontFace = "Consolas, monospace"; lbl.position.set(pumpX - 6, 38, 8);
        staticG.add(lbl); R.sprites[`loop:${loopName}`] = lbl;
      }
      fit(); aimCamera();
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
      for (const n of Object.keys(R.bodies)) {
        const d = pl?.devices?.[n] || {};
        const body = R.bodies[n], lamp = R.lamps[n], sp = R.sprites[n];
        const st = d.tripped ? "trip" : n === root ? "hot" : victims.has(n) ? "strained" : "ok";
        body.material.color.setHex(st === "trip" ? C.bodyTripped : C.body);
        body.material.emissive.setHex(st === "hot" ? C.red : st === "strained" ? C.amber : 0x000000);
        body.material.emissiveIntensity = st === "hot" ? 0.35 : st === "strained" ? 0.22 : 0;
        setLamp(lamp, st === "hot" || st === "trip" ? C.red : st === "strained" ? C.amber : C.teal);
        const txt = `${n}${d.tripped ? "  ⌀ OPEN" : ""}\n${d.amps != null ? d.amps.toFixed(1) + " A" : ""}${d.cooled && d.temp != null ? " · " + Math.round(d.temp) + " °C" : ""}`;
        if (R.spriteTxt[n] !== txt) { sp.text = txt; R.spriteTxt[n] = txt; }
        sp.color = st === "hot" || st === "trip" ? "#f2495c" : st === "strained" ? "#ff9830" : C.text;
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
      if (R.geo.loopName) {
        const key = `loop:${R.geo.loopName}`;
        const txt = `${R.geo.loopName.toUpperCase()} · ${pl?.loop?.flow != null ? Math.round(pl.loop.flow) : "—"} L/min`;
        if (R.spriteTxt[key] !== txt && R.sprites[key]) { R.sprites[key].text = txt; R.spriteTxt[key] = txt; }
      }
      const live = (g?.edges || []).filter((e) => e.state === "active" || e.state === "confirming");
      const sig = JSON.stringify(live.map((e) => [e.src, e.dst, e.signal, Math.round((e.render_weight ?? e.confidence ?? 0) * 8)]).sort());
      if (sig !== R.edgeSig) { R.edgeSig = sig; buildOverlay(live); }
    };

    // gentle orbit (drag yaw) + wheel zoom — no OrbitControls dependency
    let dragging = false, lastX = 0;
    const onDown = (e) => { dragging = true; lastX = e.clientX; };
    const onMove = (e) => { if (dragging) { R.azim = Math.min(1.15, Math.max(0.1, R.azim + (e.clientX - lastX) * 0.004)); lastX = e.clientX; aimCamera(); } };
    const onUp = () => { dragging = false; };
    const onWheel = (e) => { e.preventDefault(); R.zoomK = Math.min(2.4, Math.max(0.65, R.zoomK * (1 - e.deltaY * 0.001))); aimCamera(); };
    renderer.domElement.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("wheel", onWheel, { passive: false });

    const ro = new ResizeObserver(() => { fit(); aimCamera(); });
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
    const clock = new THREE.Clock();
    const tick = () => {
      raf = requestAnimationFrame(tick);
      if (!visible) { clock.getDelta(); return; }
      const { plant: pl, graph: g } = stateRef.current;
      if (pl && pl !== lastPlant) {       // live state applies per data poll, not per frame
        lastPlant = pl;
        const sig = JSON.stringify([Object.keys(pl.devices || {}), Object.entries(pl.devices || {}).map(([n, d]) => [n, d.rail, !!d.cooled]), Object.keys(pl.rails || {}), pl.loop?.name]);
        if (sig !== R.topoSig) { R.topoSig = sig; buildStatic(pl); }
        if (R.geo) applyLive();
        R.dirty = true;
      }
      if (g !== lastGraph) {
        lastGraph = g;
        if (R.geo) applyLive();
        R.dirty = true;
      }
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
    fit(); aimCamera(); tick();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("wheel", onWheel);
      disposeGroup(staticG); disposeGroup(liveG);
      renderer.dispose();
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
