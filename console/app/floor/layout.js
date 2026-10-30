// The site plan of the plant sim, in metres (2026-10-18). One file for the floor prototype: the
// building, the place of every station, and the places the line tracking system names
// (plant/floor/tracking.py). x runs along the bays (east), z across them (south), y is up.
//
// Sources and project choices:
//   - Bays: fab bay 30 x 120 m, 12 m to the eaves (EOT crane bay); assembly bay 24 x 80 m, 8 m high;
//     office block 800 m2 on two floors (plant/model/catalog.py, project choices).
//   - Flow: the U-shaped frame line of plant/model/flow.py: side members west to east on the back row
//     (pierce-1, lmp-1, blast-1), the CED line at the east end, then riveting, the frame scan and the
//     out conveyor east to west on the front row. Cross members in the front west cell.
//   - Machine sizes: see SIZES below. Each is marked "src" (a datasheet in research_dimensions) or
//     "pc" (project choice until a figure is found).
//   - The engine line: the TS 5 pallet stops 3 m apart, as the sim moves a pallet 3 m per move.

export const SITE = {
  fab: { x0: 0, x1: 120, z0: 0, z1: 30, h: 12, label: "frame bay" },
  asm: { x0: 0, x1: 80, z0: -24, z1: 0, h: 8, label: "engine assembly bay" },
  office: { x0: 84, x1: 104, z0: -24, z1: -4, h: 7, label: "office block" },
  yard: { x0: 122, x1: 146, z0: 0, z1: 30, label: "utility yard" },
  columnStep: 7.5,                 // pc: column grid of the steel building along the bay
  craneRailY: 9.5,                 // pc: crane rail height under the 12 m eaves
  craneSpan: 28.5,                 // the BHEL 15 t crane specification span (catalog.EOT_15T)
  doors: [{ x: 30, z: 30, w: 6, h: 6 }, { x: 116, z: 0, w: 5, h: 5 }],
};

// Machine footprints (length along x before rotation, width, height). "src" marks a size from the
// dimension research (references/research_2026-10-18/research_dimensions.md); every other size is a
// project choice until a source is found (that research lists the gaps).
export const MACHINES = [
  // frame line, back row (west to east)
  { name: "pierce-1", kind: "punchline", x: 28, z: 6, L: 28, W: 3.2, H: 3.6, label: "side member punching line" },
  { name: "lmp-1", kind: "longpress", x: 63, z: 6, L: 13, W: 3.6, H: 7.5, label: "5,000 t long member press" },
  // src: Rösler RRB 16/5: 1.5 x 0.5 m passage, transport height 1.2 m; overall size and the conveyor
  // lengths not found (pc: 11 m tables each side for 9 m members)
  { name: "blast-1", kind: "blastline", x: 94, z: 6, L: 28, W: 3.2, H: 5.0, label: "roller shot blast" },
  // frame line, east end: the CED dip line and its cure oven
  { name: "ced-1", kind: "dipline", x: 101, z: 22, L: 34, W: 13, H: 5.5, label: "CED dip line" },
  // frame line, front row (east to west)
  { name: "rivet-1", kind: "rivetjig", x: 70, z: 22, L: 12, W: 3.0, H: 3.2, label: "frame riveting" },
  { name: "qa-scanner-1", kind: "framescan", x: 54, z: 22, L: 12, W: 4.0, H: 3.6, label: "frame scan" },
  { name: "conveyor-1", kind: "rollers", x: 40, z: 22, L: 12, W: 1.6, H: 0.8, label: "out conveyor" },
  // cross member cell, front west
  // src: Messer MetalMaster Xcel 6025 (6.0 x 2.5 m cutting area) 10.65 x 5.025 overall; gantry 2.1 m
  //      (Swedcut LM 3500); nearest models for a bracket plate table
  { name: "plasma-1", kind: "plasma", x: 7.6, z: 17.6, L: 10.65, W: 5.0, H: 2.1, label: "plasma cutting" },
  // src: LVD PPEB 320/40, about 5.8 x 3.6 x 3.8 overall (listing), 4.0 m bending length (brochure)
  { name: "press-1", kind: "pressbrake", x: 17.4, z: 16.6, L: 5.8, W: 3.6, H: 3.8, label: "press brake" },
  // src: Aida NC1-2000, 1.80 wide x 2.56 deep x 3.80 high (listing)
  { name: "press-2", kind: "mechpress", x: 23.4, z: 16.6, L: 1.8, W: 2.56, H: 3.8, label: "punching press" },
  { name: "weld-1", kind: "weldcell", x: 7, z: 25.5, L: 6, W: 5, H: 2.2, label: "robot weld cell 1" },
  { name: "weld-2", kind: "weldcell", x: 15, z: 25.5, L: 6, W: 5, H: 2.2, label: "robot weld cell 2" },
  // cranes (their place moves; x here is the park position)
  { name: "crane-1", kind: "crane", x: 30, z: 15, L: 2.5, W: 28.5, H: 1.4, label: "EOT crane 1, 15 t" },
  { name: "crane-2", kind: "crane", x: 90, z: 15, L: 2.5, W: 28.5, H: 1.4, label: "EOT crane 2, 15 t" },
  // engine assembly line (the TS 5 stops 3 m apart, west to east)
  { name: "washer-1", kind: "washer", x: 6, z: -12, L: 4.5, W: 2.6, H: 2.6, label: "block washer" },
  { name: "cool-station", kind: "coolstation", x: 12, z: -12, L: 3, W: 1.8, H: 2.2, label: "part cooling" },
  { name: "leak-1", kind: "leaktest", x: 17, z: -12, L: 2.4, W: 1.8, H: 2.3, label: "leak test" },
  { name: "pallet-1", kind: "ts5", x: 30.5, z: -12, L: 21, W: 4.2, H: 0.8, label: "TS 5 pallet line" },
  { name: "tight-1", kind: "nutrunner", x: 25, z: -14.2, L: 1.4, W: 1.2, H: 2.6, label: "tightening 1" },
  { name: "tight-2", kind: "nutrunner", x: 28, z: -14.2, L: 1.4, W: 1.2, H: 2.6, label: "tightening 2" },
  { name: "cold-1", kind: "coldtest", x: 31, z: -15.5, L: 2.8, W: 2.2, H: 1.8, label: "cold test" },
  { name: "hot-1", kind: "hotcell", x: 34, z: -18.5, L: 7, W: 7, H: 4.2, label: "hot test cell" },
  // utility yard
  { name: "tr-1", kind: "transformer", x: 125, z: 4, L: 2.2, W: 1.8, H: 2.4, label: "TR-1 800 kVA" },
  { name: "tr-2", kind: "transformer", x: 129, z: 4, L: 1.8, W: 1.5, H: 2.0, label: "TR-2 315 kVA" },
  { name: "incomer-1", kind: "rmu", x: 133, z: 4, L: 2.0, W: 1.0, H: 1.9, label: "11 kV incomer" },
  { name: "mcc", kind: "panels", x: 127, z: 11, L: 8, W: 0.8, H: 2.2, label: "LV boards, APFC" },
  // src: Atlas Copco GA 37 Pack 1.31 x 0.89 x 1.79 (nearest: the air-cooled leaflet data)
  { name: "compressor-1", kind: "compressor", x: 140, z: 5, L: 1.31, W: 0.89, H: 1.79, label: "air compressor" },
  // src: EVAPCO AT 14-99 2.737 x 1.232 x 3.213 with two fans; the plate heat exchanger beside it (pc)
  { name: "cool-1", kind: "tower", x: 140, z: 15, L: 2.74, W: 1.23, H: 3.21, label: "cooling tower + PHE" },
  // src: Daikin EWAD190AJYNN 2.240 x 2.235 x 2.340, four 710 mm fans
  { name: "chiller-1", kind: "chiller", x: 127, z: 21, L: 2.24, W: 2.24, H: 2.34, label: "CED bath chiller" },
  { name: "gas-1", kind: "gasskid", x: 140, z: 25, L: 3.0, W: 1.5, H: 1.6, label: "gas regulating skid" },
];
export const BY_NAME = Object.fromEntries(MACHINES.map((m) => [m.name, m]));

// CED line inside its area: six tanks across the area (long axis along z for 9 m parts), the hang
// station at the east end, the oven at the west end. Stage names as in frame_line.CedLine.
export const CED = (() => {
  const m = BY_NAME["ced-1"];
  const x1 = m.x + m.L / 2;
  const tanks = ["degrease", "rinse1", "phosphate", "rinse2", "ced", "uf"].map((s, i) => ({ stage: s, x: x1 - 7 - i * 2.8 }));
  return { tanks, tankW: 2.2, tankL: 10.5, tankH: 2.4, hang: { x: x1 - 2.5 }, oven: { x: m.x - m.L / 2 + 5, L: 9, W: 12, H: 4.5 },
    z: m.z, hoistY: 5.2 };
})();

// The TS 5 stops (3 m apart: plant/model/assembly.PalletConveyor.move_s) and the return lane.
export const TS5 = { z: -12, ret: -9.6, stops: { load: 22, "tight-1": 25, "tight-2": 28, "cold-1": 31, "hot-1": 34, unload: 37 } };

// Places named by the tracker: where an ID shows. `slot` spreads several IDs in one buffer.
const P = (x, z, slot = [0, 1.2], extra = {}) => ({ x, z, slot, ...extra });
export const PLACES = {
  "pierce-1:in": P(6, 6, [0, 0.9], { rack: true }),
  "pierce-1:out": P(47.5, 6, [0, 0.9], { rack: true }),
  "lmp-1:out": P(73.5, 6),
  "blast-1:infeed": P(84, 6),
  "ced-1:in": P(113, 8.5, [0, 0.9], { rack: true }),
  "ced-1:out": P(81, 22, [0, 1.2], { rack: true }),
  "rivet-1:out": P(62, 22, [0, 1.2]),
  "conveyor-1:in": P(47, 22),
  "plasma-1:in": P(8, 13.2, [1.6, 0]),
  "press-1:in": P(17.4, 13.2, [1.6, 0]),
  "weld:in": P(11, 21, [1.6, 0]),
  "weld:out": P(19.5, 21, [0.6, 0]),
  "blast-1:in": P(80, 11, [1.6, 0]),
  "washer-1:in": P(2.5, -12, [0, 1.4]),
  "washer-1:load": P(4.2, -12),
  "cool-station": P(12, -12, [0.9, 0]),
  "leak-1": P(17, -12),
  "pallet-1:load": P(TS5.stops.load, TS5.z, [-1.2, 0]),   // engines on pallets queue behind the load stop
};
// Station places: an ID "in" a station shows at the station's work position.
for (const m of MACHINES) if (!PLACES[m.name]) PLACES[m.name] = P(m.x, m.z);
PLACES["blast-1"] = P(BY_NAME["blast-1"].x, 6);
PLACES["lmp-1"] = P(BY_NAME["lmp-1"].x, 6);
for (const [s, x] of Object.entries(TS5.stops)) PLACES[`pallet-1:${s}`] = P(x, TS5.z);
for (const t of CED.tanks) PLACES[`ced-1:${t.stage}`] = P(t.x, CED.z, [0, 0], { tank: t.stage });
PLACES["ced-1:degrease"].hangFrom = CED.hang.x;
PLACES["ced-1:oven"] = P(CED.oven.x, CED.z, [1.4, 0], { oven: true });

// The line positions used for moving IDs (the honest part: each one is a position a line knows).
export const LINES = {
  "pierce-1": { x0: BY_NAME["pierce-1"].x - 13, x1: BY_NAME["pierce-1"].x + 13, z: 6 },   // CNC feed axis
  "blast-1": { x0: 81, x1: 107, z: 6 },                   // roller conveyor through the cabinet
  "conveyor-1": { x0: 46, x1: 34, z: 22 },                // the out conveyor runs west
};

export const BOUNDS = { x0: -6, x1: 150, z0: -28, z1: 34 };
