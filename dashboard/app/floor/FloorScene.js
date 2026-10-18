// The three.js scene of the floor prototype (2026-10-18). Plain JS (no React): FloorLab.jsx owns
// one instance and gives it a snapshot (replay.js) on each frame.
//
// What the scene draws, and from what:
//   - the building and the stations at their layout places (layout.js), in one of two looks (themes.js)
//   - each station's stack light from its state class (states.js); the body takes a color only for
//     an abnormal state (ISA-101: color for the abnormal, gray for the normal)
//   - moving parts from the station's own values or state (models.js says which is which)
//   - tracked IDs (plant/floor/tracking.py): SOLID where a read point or a station controller puts the
//     ID; a GHOST (see-through) while the ID is between two read points, at a place inferred from the
//     transfer and the line speed
//   - cranes at the places of their transfer (the sim has no crane position: the plate says so)
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import SpriteText from "three-spritetext";
import { SITE, MACHINES, BY_NAME, PLACES, CED, TS5, LINES, BOUNDS } from "./layout";
import { THEMES, materialKit } from "./themes";
import { classify, derived } from "./states";
import { makeKit, buildMachine, stackLight } from "./models";

const FOV = 38;
const CED_TRANSFER_S = 45;          // catalog.CED_LINE transfer_s (the hoist move between tanks)
const CRANE_PH = { hoist: 30, travel: 40, lower: 30, return: 40 };   // building.EotCrane.PHASES
const HOOK_UP = 5.0, HOOK_DOWN = 1.2;

export class FloorScene {
  // hud: the console draws the plates, markers and the selection itself, in screen space over the
  // canvas (app/ui/FloorHud.jsx). The scene then draws no sprite plates and no floor brackets, keeps
  // the grid faint, and calls onFrame after each render so the overlay can follow the camera.
  constructor(el, { theme = "console", onPick, onHover, hud = false } = {}) {
    this.el = el; this.onPick = onPick; this.onHover = onHover; this.hud = hud;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 0.3, 2000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.maxPolarAngle = 1.45;
    this.controls.addEventListener("start", () => { this.userMovedAt = performance.now(); this._ease = null; });
    this.controls.target.set(65, 0, 3);
    this.camera.position.set(40, 95, 115);
    this.controls.update();                          // the camera looks at the target only after an update
    this.labelsAll = false; this.idsAll = false;
    this.sel = null; this.hover = null;
    this.ray = new THREE.Raycaster(); this.ndc = new THREE.Vector2();
    this._bindPointer();
    this.ro = new ResizeObserver(() => this._resize()); this.ro.observe(el);
    this.setTheme(theme);
    this._resize();
    this._last = performance.now();
    const loop = (now) => { this.raf = requestAnimationFrame(loop); this._frame(now); };
    this.raf = requestAnimationFrame(loop);
  }

  // ── theme and static scene ─────────────────────────────────────────────────────────────────
  setTheme(name) {
    this.themeName = name;
    this.theme = THEMES[name] || THEMES.console;
    if (this.scene) this._disposeScene();
    const th = this.theme;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(th.bg);
    this.renderer.shadowMap.enabled = !!th.shadows;
    this.mk = materialKit(th);
    this.K = makeKit(this.mk, th);
    this._lights();
    this._site();
    this._machines();
    this.itemMeshes = new Map();
    this.markers = {};
    this.ghostMats = new Map();
    this.selG = new THREE.Group(); this.scene.add(this.selG);
    if (this.snap) this.setSnapshot(this.snap);
  }

  _lights() {
    const th = this.theme, s = this.scene;
    if (th.sunlit) {
      s.add(new THREE.HemisphereLight(th.sunlit.sky, th.sunlit.groundSky, th.sunlit.hemi));
      const sun = new THREE.DirectionalLight(0xffffff, th.sunlit.sun);
      sun.position.set(30, 120, 70); sun.target.position.set(70, 0, 3);
      sun.castShadow = true;
      const c = sun.shadow.camera; c.left = -95; c.right = 95; c.top = 60; c.bottom = -60; c.near = 10; c.far = 300;
      sun.shadow.mapSize.set(4096, 4096); sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.03;
      s.add(sun, sun.target);
    } else {
      s.add(new THREE.AmbientLight(0x9fb4ff, 0.55));
      s.add(new THREE.HemisphereLight(0x5f8cff, 0x05070c, 0.45));
      const sun = new THREE.DirectionalLight(0xd9e6ff, 0.75); sun.position.set(-60, 160, 120); s.add(sun);
    }
  }

  _grid(x0, x1, z0, z1, step, color, opacity, y) {
    const pts = [];
    for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) pts.push(x, y, z0, x, y, z1);
    for (let z = Math.ceil(z0 / step) * step; z <= z1; z += step) pts.push(x0, y, z, x1, y, z);
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  }

  _site() {
    const th = this.theme, K = this.K, s = this.scene, R = K.R;
    const site = new THREE.Group(); s.add(site); this.site = site;
    const { x0, x1, z0, z1 } = BOUNDS;
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0 + 120, z1 - z0 + 120), this.mk.color(th.ground));
    ground.rotation.x = -Math.PI / 2; ground.position.set((x0 + x1) / 2, -0.01, (z0 + z1) / 2); ground.receiveShadow = !!th.shadows;
    site.add(ground);
    // under the console HUD the grid is fainter, so it does not compete with the stalks and the
    // dashed fault path (research_hud_references.md section 6)
    const gk = this.hud ? 0.5 : 1;
    site.add(this._grid(x0 - 40, x1 + 40, z0 - 40, z1 + 40, th.gridFine.step, th.gridFine.color, th.gridFine.opacity * gk, 0.005));
    site.add(this._grid(x0 - 40, x1 + 40, z0 - 40, z1 + 40, th.gridMajor.step, th.gridMajor.color, th.gridMajor.opacity * gk, 0.01));
    const wallMat = this.mk.role("wall", th.wallOpacity < 1 ? { transparent: true, opacity: th.wallOpacity } : {});
    const bay = (b, h) => {
      // slab, low walls (the roof and the upper walls are left out so the floor stays visible), columns
      const slab = K.add(site, new THREE.BoxGeometry(b.x1 - b.x0, 0.05, b.z1 - b.z0), this.mk.color(th.slab), (b.x0 + b.x1) / 2, 0, (b.z0 + b.z1) / 2);
      slab.receiveShadow = !!th.shadows; slab.castShadow = false;
      for (const [x, z, w, d] of [[(b.x0 + b.x1) / 2, b.z0, b.x1 - b.x0, 0.25], [(b.x0 + b.x1) / 2, b.z1, b.x1 - b.x0, 0.25],
        [b.x0, (b.z0 + b.z1) / 2, 0.25, b.z1 - b.z0], [b.x1, (b.z0 + b.z1) / 2, 0.25, b.z1 - b.z0]]) K.add(site, new THREE.BoxGeometry(w, 1.0, d), wallMat, x, 0, z, { outline: true });
      for (let x = b.x0; x <= b.x1 + 0.01; x += SITE.columnStep) for (const z of [b.z0, b.z1]) K.add(site, new THREE.BoxGeometry(0.45, h, 0.45), R("column"), x, 0, z);
    };
    bay(SITE.fab, SITE.fab.h); bay(SITE.asm, SITE.asm.h);
    // crane runway beams along the fab bay
    for (const z of [SITE.fab.z0 + (30 - SITE.craneSpan) / 2, SITE.fab.z1 - (30 - SITE.craneSpan) / 2]) K.add(site, new THREE.BoxGeometry(SITE.fab.x1 - SITE.fab.x0, 0.6, 0.4), R("rail"), 60, SITE.craneRailY - 0.6, z, { outline: true });
    // aisle lines in the fab bay
    for (const z of [11, 14]) K.add(site, new THREE.BoxGeometry(108, 0.02, 0.12), R("floorline"), 60, 0.03, z);
    // office block and yard fence
    const o = SITE.office;
    K.add(site, new THREE.BoxGeometry(o.x1 - o.x0, o.h, o.z1 - o.z0), this.mk.role("wall", { transparent: true, opacity: th.officeOpacity }), (o.x0 + o.x1) / 2, 0, (o.z0 + o.z1) / 2, { outline: true });
    const y = SITE.yard;
    for (let x = y.x0; x <= y.x1; x += 3) for (const z of [y.z0, y.z1]) K.add(site, new THREE.BoxGeometry(0.08, 1.8, 0.08), R("rail"), x, 0, z);
    for (let z = y.z0; z <= y.z1; z += 3) K.add(site, new THREE.BoxGeometry(0.08, 1.8, 0.08), R("rail"), y.x1, 0, z);
    // storage racks of the side member buffers (cantilever arms under the 9 m members)
    for (const P of Object.values(PLACES)) if (P.rack) {
      const n = 4, x0 = P.x - 4.5;
      for (let i = 0; i < n; i++) {
        const x = x0 + i * 3;
        K.add(site, new THREE.BoxGeometry(0.25, 1.8, 0.25), R("rack"), x, 0, P.z - 0.9);
        K.add(site, new THREE.BoxGeometry(0.15, 0.12, 3.4), R("rack"), x, 0.25, P.z + 0.7);
      }
    }
    // area labels on the ground
    const area = (txt, x, z) => { const sp = this._text(txt, 2.4, th.areaInk, null); sp.position.set(x, 0.3, z); site.add(sp); };
    area("FRAME BAY", 60, 12.5); area("ENGINE ASSEMBLY BAY", 58, -21); area("UTILITY YARD", 134, 29); area("OFFICE", 94, -14);
  }

  // A text sprite. `screen` sizes it as a share of the view height (it keeps its size at any
  // distance: plates); otherwise in metres (area names on the floor).
  _text(txt, size, color, bg, font, screen = false) {
    const sp = new SpriteText(txt, size, color);
    sp.fontFace = font || this.theme.plate.font; sp.fontWeight = "600";
    if (bg) { sp.backgroundColor = bg; sp.padding = 0.4 * size; sp.borderRadius = 0.3 * size; }   // in the units of the text height
    if (screen) sp.material.sizeAttenuation = false;
    sp.material.depthTest = false; sp.renderOrder = 10;
    return sp;
  }

  _machines() {
    const th = this.theme, K = this.K;
    this.mach = {};
    this.pick = [];
    const ctxStatic = { CED, TS5 };
    for (const m of MACHINES) {
      const built = buildMachine(K, m, this.mk, th, ctxStatic);
      const g = built.root;
      if (m.kind === "crane") g.position.set(m.x, SITE.craneRailY, 15);
      else g.position.set(m.x, 0, m.z);
      this.scene.add(g);
      // stack light at the back right corner (not on cranes and passive equipment)
      let lamp = null;
      if (!["crane", "transformer", "rmu", "panels", "gasskid", "ts5"].includes(m.kind)) {
        lamp = stackLight(K, this.mk, th);
        const lx = m.kind === "dipline" ? CED.hang.x - m.x + 2.5 : m.L / 2 - 0.3;
        const lz = m.kind === "dipline" ? -CED.tankL / 2 - 1.0 : -m.W / 2 + 0.3;
        lamp.root.position.set(lx, m.kind === "dipline" ? 2.2 : Math.min(m.H, 3.2), lz);
        g.add(lamp.root);
      }
      // plate and pick volume
      const plate = this._text(m.name, 0.011, th.plate.fg, th.plate.bg, null, true);
      plate.visible = false;
      const py = m.kind === "crane" ? SITE.craneRailY + 2.2 : m.H + 1.8;
      plate.position.set(m.kind === "crane" ? m.x : m.x, py, m.kind === "crane" ? 15 : m.z);
      this.scene.add(plate);
      const proxy = new THREE.Mesh(new THREE.BoxGeometry(m.L + 0.4, m.kind === "crane" ? 1.6 : m.H + 0.4, m.W + 0.4), new THREE.MeshBasicMaterial());
      proxy.visible = false; proxy.userData = { kind: "machine", id: m.name };
      if (m.kind === "crane") { proxy.position.set(0, 0.7, 0); g.add(proxy); }
      else { proxy.position.set(0, (m.H + 0.4) / 2, 0); g.add(proxy); }
      this.pick.push(proxy);
      // the abnormal tint: the station's body meshes keep their material until a fault or a warning
      const bodies = [];
      g.traverse((o) => { if (o.isMesh && o !== proxy && !o.userData.noTint) bodies.push([o, o.material]); });
      this.mach[m.name] = { m, built, lamp, plate, bodies, tint: null };
    }
    this.cedHoist = this.mach["ced-1"].built;
  }

  // ── items ──────────────────────────────────────────────────────────────────────────────────
  _partColor(it) {
    const p = this.theme.part, f = it.flags || {};
    if (it.type === "engine") return p.engine;
    if (f.coated || it.type === "frame") return p.painted;
    if (f.blasted) return p.blasted;
    return p.raw;
  }

  _channel(group, L, mat, z, formed) {
    const K = this.K;
    if (!formed) { K.add(group, new THREE.BoxGeometry(L, 0.03, 0.48), mat, 0, 0, z); return; }
    K.add(group, new THREE.BoxGeometry(L, 0.03, 0.3), mat, 0, 0, z);
    for (const dz of [-0.14, 0.14]) K.add(group, new THREE.BoxGeometry(L, 0.09, 0.025), mat, 0, 0.03, z + dz);
  }

  _itemMesh(it, mat) {
    const g = new THREE.Group(), K = this.K;
    const L = 9.0;                               // catalog.SIDE_MEMBER length
    if (it.type === "side member") this._channel(g, L, mat, 0, !!it.flags?.formed);
    else if (it.type === "kit") {
      K.add(g, new THREE.BoxGeometry(1.3, 0.12, 0.9), this.mk.role("rack"), 0, 0, 0);
      for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) K.add(g, new THREE.BoxGeometry(1.0, 0.18, 0.2), mat, 0, 0.12 + j * 0.19, -0.25 + i * 0.25);
    } else if (it.type === "cross member") K.add(g, new THREE.BoxGeometry(0.9, 0.2, 0.2), mat, 0, 0, 0);
    else if (it.type === "CED load") {
      K.add(g, new THREE.BoxGeometry(L + 1, 0.16, 0.16), this.mk.color(this.theme.part.carrier), 0, 2.2, 0);
      for (const dx of [-3.5, 3.5]) K.add(g, new THREE.BoxGeometry(0.06, 1.8, 0.06), this.mk.role("cable"), dx, 0.42, 0);
      for (const z of [-0.45, 0.45]) this._channel(g, L, mat, z, true);
      K.add(g, new THREE.BoxGeometry(1.2, 0.4, 0.5), mat, 0, 0.1, 0);
    } else if (it.type === "frame") {
      for (const z of [-0.432, 0.432]) this._channel(g, L, mat, z, true);   // 864 mm between the side members (SAE truck frame width)
      for (let i = 0; i < 6; i++) K.add(g, new THREE.BoxGeometry(0.2, 0.18, 0.84), mat, -L / 2 + 0.6 + i * (L - 1.2) / 5, 0.01, 0);
    } else if (it.type === "engine") {
      K.add(g, new THREE.BoxGeometry(1.04, 0.08, 0.845), this.mk.role("pallet"), 0, 0, 0);   // the largest TS 5 pallet
      K.add(g, new THREE.BoxGeometry(1.1, 0.75, 0.75), mat, 0, 0.08, 0);
      K.add(g, new THREE.BoxGeometry(1.0, 0.25, 0.55), mat, 0, 0.83, 0);
    } else K.add(g, new THREE.BoxGeometry(0.6, 0.6, 0.6), mat, 0, 0, 0);
    return g;
  }

  _ghostMat(color) {
    if (!this.ghostMats.has(color)) {
      const m = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.32, depthWrite: false });
      m.userData.shared = true; this.ghostMats.set(color, m);
    }
    return this.ghostMats.get(color);
  }

  // the place of an item at time t: { x, y, z, rotY, ghost, onCrane }
  _placeOf(it, t, slotIndex) {
    const P = PLACES[it.place];
    const snap = this.snap;
    if (it.place?.startsWith("crane-")) return { onCrane: it.place };
    if (it.place === "pierce-1") {
      // the CNC feed: the member's place along the line follows its own hole counter
      const v = snap.values, tot = it.info?.holes_total || 1;
      const frac = Math.min(1, Math.max(0, ((v["pierce-1.holes"] ?? it.info?.holes_at ?? 0) - (it.info?.holes_at ?? 0)) / tot));
      const ln = LINES["pierce-1"];
      return { x: ln.x0 + 4.5 + frac * (ln.x1 - ln.x0 - 9), y: 0.95, z: ln.z, rotY: 0 };
    }
    if (it.place === "pallet-1:transfer") {
      const from = it.from?.split(":")[1] || "load", to = (it.info?.to || "").split(":")[1] || "unload";
      const xa = TS5.stops[from] ?? TS5.stops.load, xb = TS5.stops[to] ?? TS5.stops.unload;
      const k = Math.min(1, Math.max(0, (t - it.since) / (it.info?.move_s || 30)));
      return { x: xa + (xb - xa) * k, y: 0.8, z: TS5.z, rotY: 0, ghost: true };
    }
    if (it.place?.startsWith("pallet-1:")) return { x: P.x + (P.slot?.[0] || 0) * slotIndex, y: 0.8, z: TS5.z, rotY: 0 };
    if (it.place?.startsWith("ced-1:hoist>")) {
      const stage = it.place.slice(12);
      const to = CED.tanks.find((x) => x.stage === stage);
      const fromStage = it.from?.startsWith("ced-1:") ? it.from.slice(6) : null;
      const fromT = CED.tanks.find((x) => x.stage === fromStage);
      const xa = fromT ? fromT.x : CED.hang.x, xb = to ? to.x : CED.hang.x;
      const k = Math.min(1, Math.max(0, (t - it.since) / CED_TRANSFER_S));
      return { x: xa + (xb - xa) * k, y: CED.tankH + 0.6, z: CED.z, rotY: Math.PI / 2, hoist: true };
    }
    if (it.place?.startsWith("ced-1:") && P?.tank) return { x: P.x, y: 0.3, z: CED.z, rotY: Math.PI / 2, hoist: true };
    if (it.place === "ced-1:degrease" || it.place === "ced-1:oven") {
      return { x: P.x + (slotIndex % 3) * 1.4 - 1.4, y: 0.4, z: CED.z, rotY: Math.PI / 2 };
    }
    if (it.place === "blast-1") return { x: BY_NAME["blast-1"].x, y: 1.25, z: 6, rotY: 0 };
    if (it.place === "blast-1:infeed") return { x: P.x, y: 1.25, z: P.z, rotY: 0 };
    if (it.place === "conveyor-1") return { x: BY_NAME["conveyor-1"].x, y: 0.85, z: 22, rotY: 0 };
    if (it.place === "lmp-1") return { x: BY_NAME["lmp-1"].x, y: 1.45, z: 6, rotY: 0 };
    if (it.place === "rivet-1") return { x: BY_NAME["rivet-1"].x, y: 0.75, z: 22, rotY: 0 };
    if (it.place === "qa-scanner-1") return { x: BY_NAME["qa-scanner-1"].x, y: 0.65, z: 22, rotY: 0 };
    if (it.place === "plasma-1") return { x: BY_NAME["plasma-1"].x, y: 0.8, z: BY_NAME["plasma-1"].z, rotY: 0 };
    if (it.place === "press-1" || it.place === "press-2") { const m = BY_NAME[it.place]; return { x: m.x, y: 0.0, z: m.z + 2.0, rotY: 0 }; }
    if (it.place === "weld-1" || it.place === "weld-2") { const m = BY_NAME[it.place]; return { x: m.x - 2.0, y: 1.25, z: m.z + 0.9, rotY: 0 }; }
    if (P) {
      const [sx, sz] = P.slot || [0, 1.2];
      return { x: P.x + sx * slotIndex, y: P.rack ? 0.35 + 0.0 : 0.0, z: P.z + sz * slotIndex, rotY: 0, rack: P.rack };
    }
    return { x: 0, y: 0, z: 0, rotY: 0 };
  }

  // ── cranes ─────────────────────────────────────────────────────────────────────────────────
  _placeXZ(name) {
    if (!name) return null;
    const P = PLACES[name];
    if (P) return { x: P.x, z: P.z };
    if (name.startsWith("ced-1:")) return { x: CED.hang.x, z: CED.z };
    return null;
  }

  _cranePose(c, cname, t) {
    const park = BY_NAME[cname];
    const st = this._cr || (this._cr = {});
    const mem = st[cname] || (st[cname] = { x: park.x, z: 15 });
    const src = this._placeXZ(c.src), dst = this._placeXZ(c.dst);
    let x = mem.x, z = mem.z, hy = HOOK_UP;
    const k = (dur) => Math.min(1, Math.max(0, (t - c.since) / dur));
    if (c.item && src && dst) {
      if (c.word === "hoist") { x = src.x; z = src.z; hy = HOOK_DOWN + (HOOK_UP - HOOK_DOWN) * k(CRANE_PH.hoist); }
      else if (c.word === "travel") { const q = k(CRANE_PH.travel); x = src.x + (dst.x - src.x) * q; z = src.z + (dst.z - src.z) * q; }
      else if (c.word === "lower") { x = dst.x; z = dst.z; hy = HOOK_UP - (HOOK_UP - HOOK_DOWN) * k(CRANE_PH.lower); }
      else { x = dst.x; z = dst.z; }
      mem.x = x; mem.z = z;
    } else if (c.word === "lower" || c.word === "hoist") {
      hy = c.word === "lower" ? HOOK_UP - (HOOK_UP - HOOK_DOWN) * k(CRANE_PH.lower) : HOOK_DOWN + (HOOK_UP - HOOK_DOWN) * k(CRANE_PH.hoist);
    }
    return { x, z: Math.min(29, Math.max(1, z)), hy };
  }

  // ── snapshot ───────────────────────────────────────────────────────────────────────────────
  setSnapshot(snap) { this.snap = snap; }

  _applySnapshot(dt) {
    const snap = this.snap; if (!snap) return;
    const th = this.theme, t = snap.t, v = snap.values;
    // stations
    for (const [name, M] of Object.entries(this.mach)) {
      const s = snap.states[name];
      // a state set by the verdict layer (the console's mock verdict) comes before the station's own word
      const c = snap.override?.[name] || (s?.word != null ? classify(name, s.word) : derived(name, v)) || classify(name, null);
      M.cls = c.cls; M.text = c.text; M.word = s?.word ?? null; M.since = s?.since;
      M.lamp?.set(c.cls);
      // the body takes a color only when abnormal: the verdict's root (red) and affected (orange)
      // stations, a fault or a warning; with a focus set, the stations outside it fade
      const role = this.roles?.[name];
      const faded = this.focusSet?.size && !this.focusSet.has(name) && M.m.kind !== "crane";
      const tint = role === "root" || c.cls === "fault" ? "fault" : role === "affected" || c.cls === "warn" ? "warn" : faded ? "fade" : null;
      if (tint !== M.tint) {
        M.tint = tint;
        const tm = tint === "fade" ? this.mk.color(th.fade, { transparent: true, opacity: this.hud ? 0.35 : 0.45 })
          : tint ? this.mk.color(th.status[tint], { emissive: th.status[tint], emissiveIntensity: 0.25 }) : null;
        for (const [o, mat] of M.bodies) if (!mat.transparent) o.material = tm || mat;
      }
      const extra = { ramMax: this.ramMax || {}, scanFrac: name === "qa-scanner-1" && M.word === "scan" ? Math.min(1, (t - M.since) / 300) : null };
      M.built.anim({ word: M.word, cls: c.cls, v, t, dt, name, extra });
      const showPlate = !this.hud && (this.labelsAll || this.sel === name || this.hover === name || c.cls === "fault" || c.cls === "warn" || !!role);
      M.plate.visible = showPlate;
      if (showPlate) {
        const txt = role === "root" ? `${name}  ·  root cause` : `${name}  ·  ${c.text}`;
        if (M.plateTxt !== txt) { M.plate.text = txt; M.plateTxt = txt; }
        const ink = c.cls === "fault" ? "#" + th.status.fault.toString(16).padStart(6, "0") : th.plate.fg;
        if (M.plate.color !== ink) M.plate.color = ink;
      }
    }
    // cranes: the bridge at the transfer's places (no crane position in the sim)
    for (const cname of ["crane-1", "crane-2"]) {
      const c = snap.cranes[cname], M = this.mach[cname]; if (!c || !M) continue;
      const pose = this._cranePose(c, cname, t);
      M.built.root.position.x = pose.x;
      M.built.crab.position.z = pose.z - 15;
      const ropeLen = SITE.craneRailY + 1.4 - pose.hy - 0.5;
      M.built.rope.scale.y = Math.max(0.1, ropeLen); M.built.rope.position.y = pose.hy + 0.5 - SITE.craneRailY + ropeLen / 2;
      M.built.hook.position.y = pose.hy - SITE.craneRailY;
      M.plate.position.set(pose.x, SITE.craneRailY + 2.4, pose.z);
      this._crane = this._crane || {}; this._crane[cname] = pose;
    }
    // items
    const seen = new Set();
    const byPlace = new Map();
    const items = [...snap.items].sort((a, b) => a.since - b.since);
    for (const it of items) {
      const k = byPlace.get(it.place) || 0; byPlace.set(it.place, k + 1);
      const pos = this._placeOf(it, t, k);
      const color = this._partColor(it);
      const key = `${it.type}:${it.flags?.formed ? 1 : 0}:${color}:${pos.ghost ? "g" : "s"}`;
      let rec = this.itemMeshes.get(it.id);
      if (!rec || rec.key !== key) {
        if (rec) { this.scene.remove(rec.g); rec.g.traverse((o) => { if (o.isMesh) o.geometry.dispose(); }); }
        const mat = pos.ghost ? this._ghostMat(color) : this.mk.color(color);
        const g = this._itemMesh(it, mat);
        g.traverse((o) => { if (o.isMesh) o.userData = { kind: "item", id: it.id }; });
        const label = this._text(it.id, 0.0095, th.plate.fg, th.plate.bg, null, true); label.visible = false; label.position.y = 1.6; g.add(label);
        this.scene.add(g);
        rec = { g, key, label }; this.itemMeshes.set(it.id, rec);
      }
      if (pos.onCrane) {
        const cp = this._crane?.[pos.onCrane] || { x: 0, z: 0, hy: HOOK_UP };
        rec.g.position.set(cp.x, Math.max(0.1, cp.hy - 0.9), cp.z);
        rec.g.rotation.y = 0;
      } else {
        rec.g.position.set(pos.x, pos.y, pos.z);
        rec.g.rotation.y = pos.rotY || 0;
      }
      rec.label.visible = !this.hud && (this.idsAll || this.sel === it.id || this.hover === it.id);
      seen.add(it.id);
    }
    for (const [id, rec] of this.itemMeshes) if (!seen.has(id)) { this.scene.remove(rec.g); rec.g.traverse((o) => { if (o.isMesh) o.geometry.dispose(); }); this.itemMeshes.delete(id); }
    // the CED hoist follows the load in the wet section, else it waits at the hang station
    const wet = items.find((it) => it.place?.startsWith("ced-1:") && (it.place.includes("hoist>") || PLACES[it.place]?.tank));
    const hx = wet ? this._placeOf(wet, t, 0).x : CED.hang.x;
    const H = this.mach["ced-1"]; H.built.hoist.position.x = hx - BY_NAME["ced-1"].x;
    this._selection();
  }

  _selection() {
    const id = this.sel;
    while (this.selG.children.length) { const c = this.selG.children.pop(); c.geometry?.dispose(); }
    if (!id || this.hud) return;
    let x, z, w, d;
    const M = this.mach[id];
    if (M) {
      if (M.m.kind === "crane") { const p = this._crane?.[id]; if (!p) return; x = p.x; z = 15; w = 3; d = 29; }
      else { x = M.m.x; z = M.m.z; w = M.m.L; d = M.m.W; }
    } else {
      const r = this.itemMeshes.get(id); if (!r) return;
      const b = new THREE.Box3().setFromObject(r.g); x = (b.min.x + b.max.x) / 2; z = (b.min.z + b.max.z) / 2; w = b.max.x - b.min.x; d = b.max.z - b.min.z;
    }
    const mat = this.mk.color(this.theme.sel, { emissive: this.theme.sel, emissiveIntensity: 0.6 });
    const m = 0.6, L = Math.max(0.6, Math.min(w, d) * 0.3), T = 0.15;
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      const cx = x + sx * (w / 2 + m), cz = z + sz * (d / 2 + m);
      const a = new THREE.Mesh(new THREE.BoxGeometry(L, 0.08, T), mat); a.position.set(cx - (sx * L) / 2, 0.06, cz);
      const b = new THREE.Mesh(new THREE.BoxGeometry(T, 0.08, L), mat); b.position.set(cx, 0.06, cz - (sz * L) / 2);
      this.selG.add(a, b);
    }
  }

  // ── interaction, frames, size ──────────────────────────────────────────────────────────────
  _hit(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.ndc, this.camera);
    const targets = [...this.pick];
    for (const rec of this.itemMeshes.values()) rec.g.traverse((o) => { if (o.isMesh) targets.push(o); });
    const hits = this.ray.intersectObjects(targets, false);
    const item = hits.find((h) => h.object.userData?.kind === "item");
    return (item || hits[0])?.object.userData || null;
  }

  _bindPointer() {
    const el = this.renderer.domElement;
    let down = null;
    this._onDown = (e) => { down = { x: e.clientX, y: e.clientY, b: e.button }; };
    this._onUp = (e) => {
      if (down && down.b === 0 && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 4) {
        const h = this._hit(e); this.sel = h?.id || null; this.onPick?.(h);
      }
      down = null;
    };
    this._onMove = (e) => {
      if (down) return;
      const h = this._hit(e); const id = h?.id || null;
      el.style.cursor = id ? "pointer" : "grab";
      if (id !== this.hover) { this.hover = id; this.onHover?.(h); }
    };
    el.addEventListener("pointerdown", this._onDown);
    el.addEventListener("pointerup", this._onUp);
    el.addEventListener("pointermove", this._onMove);
  }

  select(id) { this.sel = id; }

  // ── console hooks (LOG-120) ──────────────────────────────────────────────────────────────────
  setRoles(map) { this.roles = map || {}; }
  setFocusSet(ids) { this.focusSet = new Set(ids || []); }

  // A marker over each station that needs attention: the glyph shape on its color, a fixed size on
  // the screen (Factorio-style status icons). level: alarm (square), warn (triangle), recover (ring).
  setMarkers(list) {
    this.markers ||= {};
    const want = new Map((list || []).map((x) => [x.station, x.level]));
    for (const [st, rec] of Object.entries(this.markers)) {
      if (want.get(st) !== rec.level) { this.scene.remove(rec.sp); rec.sp.material.map?.dispose(); rec.sp.material.dispose(); delete this.markers[st]; }
    }
    for (const [st, level] of want) {
      if (this.markers[st]) continue;
      const m = BY_NAME[st] || (st === "tr-1" ? BY_NAME["tr-1"] : null); if (!m) continue;
      const sym = level === "alarm" ? "■" : level === "warn" ? "▲" : "○";
      const bg = level === "alarm" ? "rgba(226, 32, 40, 0.95)" : level === "warn" ? "rgba(236, 134, 41, 0.95)" : "rgba(111, 111, 111, 0.9)";
      const sp = this._text(sym, 0.016, level === "warn" ? "#1f1f1f" : "#ffffff", bg, null, true);
      sp.borderWidth = 0.002; sp.borderColor = level === "warn" ? "#1f1f1f" : "rgba(0,0,0,0)";
      sp.center.set(0.5, -1.4);                       // drawn above the station's plate
      const y = m.kind === "crane" ? SITE.craneRailY + 3.6 : Math.min(m.H, 4) + 4.2;
      sp.position.set(m.x, y, m.kind === "crane" ? 15 : m.z);
      this.scene.add(sp);
      this.markers[st] = { sp, level };
    }
  }

  // Ease the camera to frame a set of stations (follow alarms, "jump to"). Keeps the view direction.
  frame(ids, { force = false } = {}) {
    const ms = (ids || []).map((n) => BY_NAME[n]).filter(Boolean);
    if (!ms.length) return false;
    if (!force && performance.now() - (this.userMovedAt || -1e9) < 30000) return false;   // the operator moved the camera
    let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
    for (const m of ms) { x0 = Math.min(x0, m.x - m.L / 2); x1 = Math.max(x1, m.x + m.L / 2); z0 = Math.min(z0, m.z - m.W / 2); z1 = Math.max(z1, m.z + m.W / 2); }
    const c = new THREE.Vector3((x0 + x1) / 2, 0, (z0 + z1) / 2);
    const size = Math.max(x1 - x0, z1 - z0, 12);
    const dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize();
    const to = c.clone().addScaledVector(dir, size * 1.5 + 18);
    this._ease = { p0: this.camera.position.clone(), t0: this.controls.target.clone(), p1: to, t1: c, start: performance.now(), dur: 1200 };
    return true;
  }
  // The screen place of a station or a tracked ID, for the console HUD: the top centre (where a
  // marker stalk ends), the floor centre (an end of a fault path) and the screen box of the
  // bounding volume (the selection ticks), in CSS pixels of the canvas. null if unknown.
  anchorOf(id) {
    let b;
    const M = this.mach?.[id];
    if (M) {
      const m = M.m;
      if (m.kind === "crane") { const p = this._crane?.[id] || { x: m.x, z: 15 }; b = [p.x - 1.4, SITE.craneRailY - 0.6, p.z - 1.4, p.x + 1.4, SITE.craneRailY + 1.4, p.z + 1.4]; }
      else b = [m.x - m.L / 2, 0, m.z - m.W / 2, m.x + m.L / 2, m.H, m.z + m.W / 2];
    } else {
      const r = this.itemMeshes?.get(id); if (!r) return null;
      const box = new THREE.Box3();
      r.g.traverse((o) => { if (o.isMesh) box.expandByObject(o); });
      if (box.isEmpty()) return null;
      b = [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z];
    }
    const [x0, y0, z0, x1, y1, z1] = b;
    let sx0 = Infinity, sy0 = Infinity, sx1 = -Infinity, sy1 = -Infinity, front = true;
    for (const x of [x0, x1]) for (const y of [y0, y1]) for (const z of [z0, z1]) {
      const p = this._px(x, y, z); front &&= p.front;
      sx0 = Math.min(sx0, p.x); sy0 = Math.min(sy0, p.y); sx1 = Math.max(sx1, p.x); sy1 = Math.max(sy1, p.y);
    }
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
    return { top: this._px(cx, y1, cz), base: this._px(cx, Math.min(y1, 0.3), cz), box: { x0: sx0, y0: sy0, x1: sx1, y1: sy1 }, front };
  }

  _px(x, y, z) {
    const v = (this._v ||= new THREE.Vector3()).set(x, y, z).project(this.camera);
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1;
    return { x: ((v.x + 1) / 2) * w, y: ((1 - v.y) / 2) * h, front: v.z > -1 && v.z < 1 };
  }

  setLabels(all, ids) { this.labelsAll = !!all; this.idsAll = !!ids; }
  setRamMax(r) { this.ramMax = r; }

  view(kind) {
    const presets = {
      site: [[40, 95, 115], [65, 0, 3]],
      frame: [[60, 42, 62], [60, 0, 15]],
      ced: [[112, 30, 44], [100, 1, 21]],
      cross: [[4, 18, 42], [14, 1, 20]],
      engine: [[24, 20, -42], [26, 0, -12]],
      plan: [[66, 190, 3.5], [66, 0, 3]],
    };
    const [p, q] = presets[kind] || presets.site;
    this.camera.position.set(...p); this.controls.target.set(...q); this.controls.update();
  }

  _resize() {
    const w = this.el.clientWidth || 800, h = this.el.clientHeight || 500;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / Math.max(1, h); this.camera.updateProjectionMatrix();
  }

  _frame(now) {
    const dt = Math.min(0.1, (now - this._last) / 1000) * (this.timeScale || 1);
    this._last = now;
    try {
      if (this._ease) {
        const e = this._ease, k = Math.min(1, (now - e.start) / e.dur), s = k * k * (3 - 2 * k);
        this.camera.position.lerpVectors(e.p0, e.p1, s); this.controls.target.lerpVectors(e.t0, e.t1, s); this.controls.update();
        if (k >= 1) this._ease = null;
      }
      this._applySnapshot(dt);
      this.renderer.render(this.scene, this.camera);
      this.frames = (this.frames || 0) + 1;
      this.onFrame?.(this);
    } catch (e) {
      if (!this.lastError) console.error(e);
      this.lastError = e;
    }
  }

  _disposeScene() {
    this.scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      for (const m of [].concat(o.material || [])) if (!m.userData?.shared) { m.map?.dispose?.(); m.dispose(); }
    });
    this.mk?.dispose();
    for (const m of this.ghostMats?.values() || []) m.dispose();
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    const el = this.renderer.domElement;
    el.removeEventListener("pointerdown", this._onDown); el.removeEventListener("pointerup", this._onUp); el.removeEventListener("pointermove", this._onMove);
    this.controls.dispose();
    this._disposeScene();
    this.renderer.dispose();
    this.el.removeChild(el);
  }
}
