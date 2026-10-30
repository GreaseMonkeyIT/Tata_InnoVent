// Machine models of the floor prototype (2026-10-18): simple solids at the sizes of layout.js, one
// builder per kind. Each builder returns { root, anim(ctx) }. `root` is centered on the footprint at
// floor level: x along the machine length L, z across its width W. `anim` moves the parts that a
// station controller reports (a ram position, an arc on, a hoist at a tank); it gets
//   ctx = { word, cls, v (values at this time), t (plant seconds), dt, extra }.
// Parts named in a comment "state animation" move by the station state only: the sim has no axis
// position for them, so the motion shows the state, not a measured position.
import * as THREE from "three";

const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
const cyl = (rt, rb, h, s = 16) => new THREE.CylinderGeometry(rt, rb, h, s);

export function makeKit(mk, theme) {
  const shadows = !!theme.shadows;
  // a mesh with its base at y (so `y` is the bottom face), optional outline in the console look
  const add = (parent, geo, mat, x, y, z, { outline = false, rot = null, name = null } = {}) => {
    const m = new THREE.Mesh(geo, mat);
    if (rot) m.rotation.set(...rot);
    m.updateMatrixWorld(true);
    const bb = new THREE.Box3().setFromObject(m);         // after the rotation: the base sits at y
    m.position.set(x, y - bb.min.y, z);
    m.castShadow = shadows; m.receiveShadow = shadows;
    if (outline && mk.edge) m.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo, 25), mk.edge));
    if (name) m.name = name;
    parent.add(m);
    return m;
  };
  const R = (r, o) => mk.role(r, o);
  return { add, R, box, cyl };
}

// ── builders ────────────────────────────────────────────────────────────────────────────────
function rollerBed(K, g, L, W, H, x0, n) {
  const { add, R } = K;
  for (const sz of [-1, 1]) add(g, box(L, 0.18, 0.12), R("frame"), x0 + L / 2, H - 0.2, sz * (W / 2 - 0.06), { outline: true });
  for (let i = 0; i <= Math.floor(L / 3); i++) for (const sz of [-1, 1]) add(g, box(0.12, H - 0.2, 0.12), R("frame"), x0 + Math.min(i * 3, L - 0.06), 0, sz * (W / 2 - 0.06));
  const rollers = [];
  for (let i = 0; i < n; i++) {
    const r = add(g, cyl(0.06, 0.06, W - 0.15, 10), R("roller"), x0 + (i + 0.5) * (L / n), H - 0.16, 0, { rot: [Math.PI / 2, 0, 0] });
    rollers.push(r);
  }
  return rollers;
}

const B = {};

B.punchline = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  const inL = 11, outL = 12, unitL = L - inL - outL;
  rollerBed(K, g, inL, 1.6, 0.9, -L / 2, 10);
  rollerBed(K, g, outL, 1.6, 0.9, L / 2 - outL, 11);
  const ux = -L / 2 + inL + unitL / 2;
  add(g, box(unitL, 0.9, W), R("frame"), ux, 0, 0, { outline: true });            // base
  add(g, box(unitL * 0.9, H - 1.6, 0.5), R("frame"), ux, 0.9, -W / 2 + 0.25, { outline: true });   // C-frame back
  add(g, box(unitL * 0.9, 0.7, W * 0.85), R("frame"), ux, H - 0.7, -0.1, { outline: true });        // C-frame top
  const head = add(g, box(0.7, 0.9, 0.7), R("moving"), ux, H - 1.6, 0.2, { name: "head" });          // punch ram
  add(g, box(1.2, 1.9, 0.8), R("panel"), ux + unitL / 2 + 0.8, 0, W / 2 + 0.6, { outline: true });  // CNC cabinet
  add(g, box(1.6, 1.0, 1.0), R("accent"), ux - 1.2, 0, -W / 2 - 0.7);                               // hydraulic unit
  const y0 = head.position.y;
  return { root: g, anim: ({ word, t }) => {
    // state animation: one hit every 1.2 s (catalog.PUNCH_450 hit_s), the 0.3 s stroke at its end
    const ph = (t % 1.2) / 1.2;
    head.position.y = word === "pierce" && ph > 0.75 ? y0 - 0.25 * Math.sin(((ph - 0.75) / 0.25) * Math.PI) : y0;
  } };
};

B.longpress = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  add(g, box(L, 1.4, W), R("frame"), 0, 0, 0, { outline: true });                       // bed
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) add(g, box(1.0, H - 2.4, 0.8), R("frame"), sx * (L / 2 - 0.6), 1.4, sz * (W / 2 - 0.4), { outline: true });
  add(g, box(L, 1.0, W), R("frame"), 0, H - 1.0, 0, { outline: true });                 // crown
  const ram = add(g, box(L - 2.4, 0.9, W * 0.55), R("moving"), 0, H - 2.2, 0, { name: "ram", outline: true });
  for (let i = -2; i <= 2; i++) add(g, cyl(0.28, 0.28, 0.9, 12), R("accent"), i * (L / 6), H - 1.9, 0);   // cylinders
  add(g, box(2.4, 2.1, 1.2), R("panel"), L / 2 + 1.6, 0, W / 2 - 0.6, { outline: true });                 // control
  add(g, box(3.0, 1.6, 1.6), R("accent"), -L / 2 - 2.0, 0, -W / 2 + 0.8);                                  // power pack
  const top = ram.position.y, low = 1.6 + 0.45;
  return { root: g, anim: ({ v, extra }) => {
    // ram position: the press's own ram transducer (lmp-1.ram_mm), scaled to its largest value
    const mm = v["lmp-1.ram_mm"], mx = extra?.ramMax?.["lmp-1"] || 1;
    if (mm != null) ram.position.y = top - Math.min(1, Math.max(0, mm / mx)) * (top - low - 0.35);
  } };
};

B.pressbrake = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  for (const sx of [-1, 1]) add(g, box(0.35, H, W), R("frame"), sx * (L / 2 - 0.18), 0, 0, { outline: true });   // side frames
  add(g, box(L - 0.7, 0.9, 0.35), R("frame"), 0, 0, 0.2, { outline: true });                                      // lower beam
  const ram = add(g, box(L - 0.7, 0.9, 0.35), R("moving"), 0, H - 1.5, 0.2, { name: "ram", outline: true });      // upper beam
  add(g, box(L - 0.9, 0.12, 0.6), R("accent"), 0, 0.95, -0.5);                                                    // back gauge
  add(g, box(0.6, 1.6, 0.5), R("panel"), L / 2 + 0.6, 0, W / 2);
  const top = ram.position.y;
  return { root: g, anim: ({ v, extra }) => {
    const mm = v["press-1.ram_mm"], mx = extra?.ramMax?.["press-1"] || 1;
    if (mm != null) ram.position.y = top - Math.min(1, Math.max(0, mm / mx)) * (top - 1.4);
  } };
};

B.mechpress = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  add(g, box(L, 0.9, W), R("frame"), 0, 0, 0, { outline: true });
  add(g, box(L, H - 1.6, 0.7), R("frame"), 0, 0.9, -W / 2 + 0.35, { outline: true });     // gap frame back
  add(g, box(L, 0.8, W * 0.8), R("frame"), 0, H - 0.8, -0.2, { outline: true });           // crown
  const fly = add(g, cyl(0.7, 0.7, 0.3, 24), R("moving"), L / 2 + 0.2, H - 1.1, -0.3, { rot: [0, 0, Math.PI / 2], name: "flywheel" });
  const slide = add(g, box(L * 0.5, 0.6, W * 0.45), R("moving"), 0, H - 1.6, 0.25, { name: "slide" });
  const y0 = slide.position.y;
  return { root: g, anim: ({ word, v, t, dt }) => {
    fly.rotation.x += ((v["press-2.flywheel_rpm"] ?? 0) / 60) * 2 * Math.PI * Math.min(dt, 0.1) * 0.05;   // slowed 20x to stay readable
    slide.position.y = word === "stroke" ? y0 - 0.35 * (0.5 - 0.5 * Math.cos(t * 2 * Math.PI)) : y0;          // state animation
  } };
};

B.plasma = (K, m, mk, theme) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W } = m;
  add(g, box(L, 0.75, W - 0.6), R("frame"), 0, 0, 0, { outline: true });                 // downdraft table
  for (let i = 0; i < 14; i++) add(g, box(0.04, 0.12, W - 0.8), R("accent"), -L / 2 + 0.3 + i * (L - 0.6) / 13, 0.75, 0);   // slats
  for (const sz of [-1, 1]) add(g, box(L + 0.6, 0.25, 0.25), R("rail"), 0, 0.75, sz * (W / 2 - 0.1));   // gantry rails
  const gantry = new THREE.Group(); g.add(gantry);
  add(gantry, box(0.5, 0.35, W), R("moving"), 0, 1.55, 0, { outline: true });             // bridge
  for (const sz of [-1, 1]) add(gantry, box(0.5, 0.6, 0.35), R("moving"), 0, 1.0, sz * (W / 2 - 0.1));
  const torch = add(gantry, box(0.25, 0.6, 0.25), R("accent"), 0, 1.0, 0, { name: "torch" });
  const arc = add(gantry, new THREE.SphereGeometry(0.09, 10, 8), mk.glow(theme.arc, 2.5), 0, 0.86, 0);
  add(g, box(1.24, 1.25, 0.84), R("panel"), -L / 2 - 1.0, 0, W / 2 - 0.5, { outline: true }); // XPR300 power supply 1.238 x 0.842 x 1.248
  return { root: g, anim: ({ word, t }) => {
    // state animation: the sim has no torch axes (a table CNC would give X and Y), so the path is a
    // fixed pattern while the state is cut or traverse
    const active = word === "cut" || word === "traverse";
    if (active) {
      const s = word === "cut" ? t * 0.05 : t * 0.4;
      gantry.position.x = Math.sin(s) * (L / 2 - 0.8);
      torch.position.z = Math.cos(s * 1.7) * (W / 2 - 0.8);
      arc.position.z = torch.position.z;
    }
    arc.visible = word === "cut";
    if (arc.visible) arc.scale.setScalar(0.8 + 0.4 * Math.random());
  } };
};

B.weldcell = (K, m, mk, theme) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  // fence: posts and panels (open toward +z for the operator's loading side)
  const fence = mk.color(theme.fence[0], { transparent: true, opacity: theme.fence[1] });
  for (const [x, z, w, d] of [[0, -W / 2, L, 0.05], [-L / 2, 0, 0.05, W], [L / 2, 0, 0.05, W]]) add(g, box(w, H, d), fence, x, 0, z);
  for (const [x, z] of [[-L / 2, -W / 2], [L / 2, -W / 2], [-L / 2, W / 2], [L / 2, W / 2]]) add(g, box(0.08, H, 0.08), R("guard"), x, 0, z);
  // two-station positioner (H frame), turns 180 degrees for a part change
  const pos = new THREE.Group(); pos.position.set(0, 0, 0.9); g.add(pos);
  add(pos, box(0.5, 1.1, 0.5), R("frame"), 0, 0, 0, { outline: true });
  const turn = new THREE.Group(); turn.position.y = 1.1; pos.add(turn);
  add(turn, box(4.2, 0.12, 0.3), R("moving"), 0, 0, 0);
  for (const sx of [-1, 1]) add(turn, box(0.3, 0.6, 1.4), R("moving"), sx * 2.0, -0.3, 0);
  // robot: base, turret, upper arm, forearm, torch
  const robot = new THREE.Group(); robot.position.set(0, 0, -1.2); g.add(robot);
  add(robot, cyl(0.35, 0.4, 0.5, 18), R("robot"), 0, 0, 0, { outline: true });
  const j1 = new THREE.Group(); j1.position.y = 0.5; robot.add(j1);
  add(j1, box(0.5, 0.45, 0.5), R("robot"), 0, 0, 0, { outline: true });
  const j2 = new THREE.Group(); j2.position.y = 0.45; j1.add(j2);
  add(j2, box(0.22, 1.0, 0.22), R("robot"), 0, 0, 0, { outline: true });
  const j3 = new THREE.Group(); j3.position.y = 1.0; j2.add(j3);
  add(j3, box(0.18, 0.18, 0.95), R("robot"), 0, -0.09, 0.45, { outline: true });
  const tip = new THREE.Group(); tip.position.set(0, 0, 0.95); j3.add(tip);
  add(tip, cyl(0.04, 0.05, 0.32, 8), R("accent"), 0, -0.32, 0);
  const arc = add(tip, new THREE.SphereGeometry(0.07, 10, 8), mk.glow(theme.arc, 3), 0, -0.4, 0);
  add(g, box(0.8, 0.7, 0.6), R("frame"), L / 2 + 0.6, 0, -W / 2 + 0.6);                     // trolley
  add(g, box(0.71, 0.51, 0.3), R("panel"), L / 2 + 0.6, 0.7, -W / 2 + 0.6, { outline: true });  // TPS 500i 0.706 x 0.300 x 0.510
  add(g, cyl(0.35, 0.35, 0.8, 16), R("accent"), L / 2 + 0.6, 0, -W / 2 + 1.6);              // wire drum
  let turnTo = 0;
  return { root: g, anim: ({ word, v, t, name }) => {
    // state animation: the sim has no joint angles (a robot controller gives them over OPC UA)
    if (word === "weld") { j1.rotation.y = Math.sin(t * 0.25) * 0.5; j2.rotation.x = 0.55; j3.rotation.x = 0.35; }
    else if (word === "move") { j1.rotation.y = Math.sin(t * 1.2) * 0.9; j2.rotation.x = 0.3 + 0.2 * Math.sin(t); j3.rotation.x = 0.2; }
    else { j1.rotation.y *= 0.9; j2.rotation.x = 0.05; j3.rotation.x = -0.3; }
    if (word === "change") turnTo = Math.PI;
    if (word === "weld") turnTo = 0;
    turn.rotation.y += (turnTo - turn.rotation.y) * 0.05;
    const amps = v[`${name}.weld_a`] ?? 0;
    arc.visible = word === "weld" && amps > 1;
    if (arc.visible) arc.scale.setScalar(0.7 + 0.6 * Math.random());
  } };
};

B.blastline = (K, m) => {
  const { add, R, } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  const inL = 11, outL = 11, cabL = L - inL - outL;
  rollerBed(K, g, inL, 1.8, 1.2, -L / 2, 10);                                            // RRB transport height 1.2 m
  rollerBed(K, g, outL, 1.8, 1.2, L / 2 - outL, 10);
  add(g, box(cabL, H, W), R("frame"), 0, 0, 0, { outline: true });                        // cabinet
  const wheels = [];
  for (const [x, y, z, rx] of [[-cabL / 4, H * 0.75, W / 2 + 0.2, Math.PI / 2], [cabL / 4, H * 0.75, -W / 2 - 0.2, Math.PI / 2],
    [-cabL / 4, H + 0.2, 0, 0], [cabL / 4, H + 0.2, 0, 0]]) {
    wheels.push(add(g, cyl(0.45, 0.45, 0.4, 18), R("moving"), x, y - 0.2, z, { rot: [rx, 0, 0] }));
  }
  add(g, box(2.0, 6.5, 2.0), R("panel"), 0, 0, -W / 2 - 2.6, { outline: true });          // dust collector
  add(g, box(0.8, H + 1.6, 0.8), R("accent"), cabL / 2 + 0.6, 0, -W / 2 - 0.6);            // bucket elevator
  return { root: g, anim: ({ v, dt }) => {
    const on = (v["blast-1.abrasive_kg_min"] ?? 0) > 0;
    for (const w of wheels) w.rotation.y += on ? dt * 6 : dt * 1.2;      // the turbines idle between parts
  } };
};

B.dipline = (K, m, mk, theme, ctxStatic) => {
  const { add, R } = K; const g = new THREE.Group();
  const { CED } = ctxStatic;
  const zc = 0;
  for (const t of CED.tanks) {
    const x = t.x - m.x, w = CED.tankW, l = CED.tankL, h = CED.tankH, s = 0.12;   // open-top tank: four walls
    for (const [dx, dz, bw, bl] of [[-w / 2 + s / 2, 0, s, l], [w / 2 - s / 2, 0, s, l], [0, -l / 2 + s / 2, w, s], [0, l / 2 - s / 2, w, s]])
      add(g, box(bw, h, bl), R("tank"), x + dx, 0, zc + dz, { outline: true });
    add(g, box(CED.tankW - 0.2, 0.02, CED.tankL - 0.2), R(t.stage === "ced" ? "liquid" : "water", { transparent: true, opacity: 0.85 }), x, CED.tankH - 0.25, zc);
  }
  // hoist rails along x on posts, and the hoist (bridge + hook)
  const xa = CED.oven.x - m.x + CED.oven.L / 2, xb = CED.hang.x - m.x + 1.5;
  for (const sz of [-1, 1]) {
    add(g, box(xb - xa + 2, 0.4, 0.4), R("rail"), (xa + xb) / 2, CED.hoistY + 0.6, sz * (CED.tankL / 2 + 0.6));
    for (let x = xa; x <= xb + 0.01; x += (xb - xa) / 4) add(g, box(0.3, CED.hoistY + 0.6, 0.3), R("rail"), x, 0, sz * (CED.tankL / 2 + 0.6));
  }
  const hoist = new THREE.Group(); g.add(hoist);
  add(hoist, box(1.2, 0.8, CED.tankL + 1.6), R("crane"), 0, CED.hoistY + 1.0, 0, { outline: true });
  const bar = add(hoist, box(0.3, 0.3, CED.tankL - 1.0), R("accent"), 0, CED.hoistY - 0.2, 0, { name: "bar" });
  // hang and unload station, the cure oven
  add(g, box(2.4, 0.2, CED.tankL), R("frame"), CED.hang.x - m.x, 0, zc);
  const ov = CED.oven;
  add(g, box(ov.L, ov.H, ov.W), R("hot"), ov.x - m.x, 0, zc, { outline: true });
  add(g, box(1.5, 1.2, 2.0), R("accent"), ov.x - m.x, ov.H, zc + 3);                      // burner box
  add(g, box(2.0, 2.2, 1.4), R("panel"), CED.hang.x - m.x + 1.5, 0, -CED.tankL / 2 - 1.6, { outline: true });  // rectifier
  return { root: g, hoist, bar, anim: () => {} };
};

B.rivetjig = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  add(g, box(L, 0.7, 1.4), R("frame"), 0, 0, 0, { outline: true });                       // jig base
  for (let i = 0; i < 7; i++) for (const sz of [-1, 1]) add(g, box(0.2, 0.6, 0.2), R("accent"), -L / 2 + 0.6 + i * (L - 1.2) / 6, 0.7, sz * 0.55);
  for (const sx of [-1, 1]) add(g, box(0.2, H, 0.2), R("rail"), sx * (L / 2 + 0.3), 0, -W / 2);  // tool rail posts
  add(g, box(L + 0.8, 0.2, 0.2), R("rail"), 0, H, -W / 2);
  const tools = [0, 1].map((k) => add(g, box(0.35, 0.5, 0.6), R("moving"), 0, 1.4, 0, { name: `tool${k}` }));
  add(g, box(1.2, 1.2, 0.9), R("panel"), L / 2 + 1.2, 0, W / 2 - 0.4, { outline: true }); // hydraulic unit
  return { root: g, anim: ({ word, v }) => {
    // tools along the frame by the fastener count of this frame (120 per frame: catalog.RIVET)
    const n = (v["rivet-1.fasteners"] ?? 0) % 120;
    tools.forEach((tl, k) => { tl.position.x = -L / 2 + 0.8 + ((n / 2 + k * 30) % 60) / 60 * (L - 1.6); tl.position.y = word === "swage" ? 1.15 : 1.4; });
  } };
};

B.framescan = (K, m) => {
  const { add, R } = K; const g = new THREE.Group();
  const { L, W, H } = m;
  add(g, box(L, 0.6, 1.6), R("frame"), 0, 0, 0, { outline: true });
  for (const sz of [-1, 1]) add(g, box(L, 0.15, 0.2), R("rail"), 0, 0.05, sz * (W / 2 - 0.1));
  const portal = new THREE.Group(); g.add(portal);
  for (const sz of [-1, 1]) add(portal, box(0.3, H, 0.3), R("moving"), 0, 0, sz * (W / 2 - 0.1), { outline: true });
  add(portal, box(0.4, 0.4, W), R("moving"), 0, H - 0.4, 0, { outline: true });
  add(portal, box(0.3, 0.3, 0.3), R("accent"), 0, H - 0.75, 0);
  return { root: g, anim: ({ word, extra }) => {
    // the portal sweeps the frame during a scan (SCAN_S 300 s, flow.py); progress from the scan start
    const p = word === "scan" && extra?.scanFrac != null ? extra.scanFrac : 0;
    portal.position.x = -L / 2 + 0.5 + p * (L - 1.0);
  } };
};

B.rollers = (K, m) => {
  const { L, W, H } = m; const g = new THREE.Group();
  const rollers = rollerBed(K, g, L, W, H, -L / 2, 24);
  return { root: g, anim: ({ word, dt }) => { if (word === "moving") for (const r of rollers) r.rotation.y += dt * 4; } };
};

B.crane = (K, m) => {
  // built in world orientation by the scene: girders along z (the span), end trucks on the rails
  const { add, R } = K; const g = new THREE.Group();
  const span = m.W;
  for (const sx of [-1, 1]) add(g, box(0.5, 1.4, span), R("crane"), sx * 0.9, 0, 0, { outline: true });   // girders
  for (const sz of [-1, 1]) add(g, box(3.2, 0.7, 0.8), R("crane"), 0, 0.2, sz * (span / 2), { outline: true }); // end trucks
  const crab = new THREE.Group(); g.add(crab);
  add(crab, box(2.6, 0.9, 2.2), R("moving"), 0, 1.4, 0, { outline: true });
  const rope = add(crab, box(0.06, 1, 0.06), R("cable"), 0, 0, 0);
  const hook = add(crab, box(0.5, 0.5, 0.5), R("accent"), 0, 0, 0);
  return { root: g, crab, rope, hook, anim: () => {} };
};

B.washer = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  add(g, box(L, H, W), R("frame"), 0, 0, 0, { outline: true });
  const door = add(g, box(1.6, 1.6, 0.08), R("glass", { transparent: true, opacity: 0.6 }), -L / 2 + 1.2, 0.5, W / 2 + 0.05);
  add(g, box(1.2, 1.4, 1.0), R("panel"), L / 2 + 0.7, 0, 0);
  return { root: g, anim: () => { door.visible = true; } };
};
B.coolstation = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) add(g, box(0.1, H, 0.1), R("frame"), x * L / 2, 0, z * W / 2);
  add(g, box(L, 0.1, W), R("frame"), 0, H, 0, { outline: true });
  const fans = [-0.7, 0.7].map((x) => add(g, cyl(0.45, 0.45, 0.12, 16), R("moving"), x, H - 0.3, -W / 2 + 0.1, { rot: [Math.PI / 2, 0, 0] }));
  return { root: g, anim: ({ dt }) => { for (const f of fans) f.rotation.y += dt * 5; } };
};
B.leaktest = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  add(g, box(L, 0.9, W), R("frame"), 0, 0, 0, { outline: true });
  for (const sx of [-1, 1]) add(g, box(0.15, H - 0.9, 0.15), R("frame"), sx * (L / 2 - 0.1), 0.9, -W / 2 + 0.1);
  add(g, box(L, 0.3, 0.6), R("moving"), 0, H - 0.3, -W / 2 + 0.3);
  return { root: g, anim: () => {} };
};
B.ts5 = (K, m, mk, theme, ctxStatic) => {
  const { add, R } = K; const g = new THREE.Group();
  const { TS5 } = ctxStatic;
  const x0 = TS5.stops.load - m.x - 1.5, x1 = TS5.stops.unload - m.x + 1.5;
  for (const z of [TS5.z - m.z, TS5.ret - m.z]) for (const dz of [-0.45, 0.45]) add(g, box(x1 - x0, 0.12, 0.12), R("rail"), (x0 + x1) / 2, 0.68, z + dz);
  for (const z of [TS5.z - m.z, TS5.ret - m.z]) for (let x = x0; x <= x1; x += 2) for (const dz of [-0.45, 0.45]) add(g, box(0.08, 0.68, 0.08), R("frame"), x, 0, z + dz);
  for (const x of Object.values(TS5.stops)) add(g, box(0.12, 0.25, 0.12), R("guard"), x - m.x - 0.5, 0.68, TS5.z - m.z);   // stop gates
  for (const dx of [x0 - 0.5, x1 + 0.5]) add(g, box(0.8, 0.12, TS5.z - TS5.ret + 0.9), R("rail"), dx, 0.68, (TS5.z + TS5.ret) / 2 - m.z);  // lift and transfer units
  return { root: g, anim: () => {} };
};
B.nutrunner = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { H } = m;
  add(g, box(0.3, H, 0.3), R("frame"), 0, 0, -0.4, { outline: true });
  add(g, box(0.15, 0.15, 1.3), R("frame"), 0, H - 0.2, 0.2);
  const sp = add(g, cyl(0.12, 0.12, 0.7, 12), R("moving"), 0, H - 1.0, 0.8);
  return { root: g, anim: ({ word, dt }) => { if (word === "rundown") sp.rotation.y += dt * 12; } };
};
B.coldtest = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W } = m;
  add(g, box(L, 0.5, W), R("frame"), 0, 0, 0, { outline: true });
  add(g, cyl(0.35, 0.35, 0.9, 16), R("moving"), -L / 2 + 0.5, 0.5, 0, { rot: [0, 0, Math.PI / 2] });
  const shaft = add(g, cyl(0.07, 0.07, 1.0, 10), R("accent"), -L / 2 + 1.3, 0.85, 0, { rot: [0, 0, Math.PI / 2] });
  return { root: g, anim: ({ word, dt }) => { if (word === "test") shaft.rotation.x += dt * 8; } };
};
B.hotcell = (K, m, mk, theme) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  const wall = mk.color(theme.cellWall[0], { transparent: true, opacity: theme.cellWall[1] });
  for (const [x, z, w, d] of [[0, -W / 2, L, 0.15], [-L / 2, 0, 0.15, W], [L / 2, 0, 0.15, W]]) add(g, box(w, H, d), wall, x, 0, z, { outline: true });
  add(g, box(L * 0.3, H, 0.15), wall, -L / 2 + L * 0.15, 0, W / 2, { outline: true });
  add(g, box(L * 0.3, H, 0.15), wall, L / 2 - L * 0.15, 0, W / 2, { outline: true });
  add(g, box(1.6, 1.3, 1.1), R("frame"), 1.5, 0, -1.2, { outline: true });                // dynamometer
  const shaft = add(g, cyl(0.08, 0.08, 1.2, 10), R("accent"), 0.2, 0.75, -1.2, { rot: [0, 0, Math.PI / 2] });
  const bar = add(g, box(0.25, 1, 0.25), mk.glow(theme.status.active, 0.8), L / 2 - 0.6, 0, W / 2 + 0.6);   // load bar
  return { root: g, anim: ({ word, v, dt }) => {
    if (word === "test") shaft.rotation.x += dt * 10;
    const lp = Math.max(0.02, (v["hot-1.load_pct"] ?? 0) / 100);
    bar.scale.y = lp * 2.4; bar.position.y = (lp * 2.4) / 2;
  } };
};
B.transformer = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  add(g, box(L * 0.6, H * 0.7, W * 0.7), R("frame"), 0, 0.2, 0, { outline: true });
  for (let i = 0; i < 6; i++) for (const sz of [-1, 1]) add(g, box(0.05, H * 0.55, 0.35), R("accent"), -L * 0.25 + i * L * 0.1, 0.3, sz * (W * 0.42));
  for (let i = -1; i <= 1; i++) add(g, cyl(0.06, 0.08, 0.5, 10), R("panel"), i * 0.3, 0.2 + H * 0.7, 0);
  return { root: g, anim: () => {} };
};
B.rmu = (K, m) => { const g = new THREE.Group(); K.add(g, box(m.L, m.H, m.W), K.R("panel"), 0, 0, 0, { outline: true }); return { root: g, anim: () => {} }; };
B.panels = (K, m) => {
  const g = new THREE.Group();
  for (let i = 0; i < 8; i++) K.add(g, box(m.L / 8 - 0.05, m.H, m.W), K.R("panel"), -m.L / 2 + (i + 0.5) * m.L / 8, 0, 0, { outline: true });
  return { root: g, anim: () => {} };
};
B.compressor = (K, m) => { const g = new THREE.Group(); K.add(g, box(m.L, m.H, m.W), K.R("frame"), 0, 0, 0, { outline: true }); return { root: g, anim: () => {} }; };
B.tower = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  add(g, box(L, H - 0.6, W), R("tank"), 0, 0, 0, { outline: true });
  const fans = [-0.68, 0.68].map((x) => { add(g, cyl(0.55, 0.55, 0.6, 20), R("frame"), x, H - 0.6, 0, { outline: true }); return add(g, box(1.0, 0.05, 0.18), R("moving"), x, H - 0.15, 0); });   // two fans (AT 14-99)
  add(g, box(1.0, 1.6, 0.5), R("frame"), -L / 2 - 1.0, 0, 0, { outline: true });          // plate heat exchanger
  return { root: g, anim: ({ v, dt }) => { if (v["cool-1-tower.fan_on"]) for (const f of fans) f.rotation.y += dt * 6; } };
};
B.chiller = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W, H } = m;
  add(g, box(L, H - 0.3, W), R("panel"), 0, 0, 0, { outline: true });
  const fans = [[-1, -1], [1, -1], [-1, 1], [1, 1]].map(([i, k]) => add(g, box(0.68, 0.05, 0.12), R("moving"), i * L / 4, H - 0.25, k * W / 4));   // four 710 mm fans
  return { root: g, anim: ({ v, dt }) => { if ((v["chiller-1.power_kw"] ?? 0) > 1) for (const f of fans) f.rotation.y += dt * 6; } };
};
B.gasskid = (K, m) => {
  const { add, R } = K; const g = new THREE.Group(); const { L, W } = m;
  add(g, box(L, 0.2, W), R("frame"), 0, 0, 0);
  for (const z of [-0.35, 0.35]) add(g, cyl(0.08, 0.08, L, 10), mK(K, "guard"), 0, 0.7, z, { rot: [0, 0, Math.PI / 2] });
  for (const x of [-0.8, 0, 0.8]) add(g, box(0.3, 0.5, 0.3), R("accent"), x, 0.55, -0.35);
  return { root: g, anim: () => {} };
};
const mK = (K, role) => K.R(role);
B.generic = (K, m) => { const g = new THREE.Group(); K.add(g, box(m.L, m.H, m.W), K.R("frame"), 0, 0, 0, { outline: true }); return { root: g, anim: () => {} }; };

export function buildMachine(K, m, mk, theme, ctxStatic) {
  const fn = B[m.kind] || B.generic;
  return fn(K, m, mk, theme, ctxStatic);
}

// A stack light: five segments in the IEC 60204-1 order from the top (red, yellow, blue, green,
// white); the segment of the state class lights. Display choice: drawn 2.5 times its real size
// (a 70 mm tower is not visible across a 120 m bay).
export const LAMP_ORDER = ["fault", "warn", "setup", "active", "idle"];
export function stackLight(K, mk, theme) {
  const g = new THREE.Group();
  K.add(g, box(0.1, 1.2, 0.1), K.R("accent"), 0, 0, 0);
  const segs = {};
  LAMP_ORDER.forEach((cls, i) => {
    const off = mk.color(theme.lampOff, { transparent: true, opacity: 0.55 });   // unlit: neutral gray
    const mesh = K.add(g, cyl(0.25, 0.25, 0.36, 16), off, 0, 1.2 + (LAMP_ORDER.length - 1 - i) * 0.38, 0);
    segs[cls] = { mesh, off, on: mk.lamp(cls) };
  });
  return { root: g, set: (cls) => { for (const [c, s] of Object.entries(segs)) s.mesh.material = c === cls ? s.on : s.off; } };
}
