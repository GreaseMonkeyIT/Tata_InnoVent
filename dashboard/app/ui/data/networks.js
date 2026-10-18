// The floor overlays (LOG-121): the utility networks and the material flow of the plant sim, drawn
// on the floor in the manner of the Dyson Sphere Program power view. The other stations fade, a line
// runs from each source to each user, and each member shows one value.
//
// Sources (the plant sim is the ground truth):
//   power  plant/model/plant.py, the wiring docstring and the loads of each feeder: incomer-1 ->
//          TR-1 -> LV-1 -> psu-a (frame line, cranes) and psu-b (utilities, blast-1, the CED line),
//          incomer-1 -> TR-2 -> LV-2 -> psu-c (engine line) and psu-d (bay services, office).
//          Both LV boards stand in the "mcc" panel row of the floor.
//   air    plant.py: compressor-1 feeds the air header; the plasma shield air (118 slpm while
//          cutting) is the only air user in the sim.
//   water  plant.py: cool-1 (tower, plate heat exchanger, loop pump) cools compressor-1 (oil and air
//          coolers), the oil coolers of press-1 and lmp-1, and hot-1 (cell heat exchanger).
//          chiller-1 holds the CED bath of ced-1.
//   gas    plant.py: gas-1 feeds the CED tank burners and the cure oven of ced-1.
//   flow   plant/model/flow.py: the routes of the frame line and the engine line.
// The sim has no cable, pipe or conveyor routes, so the lines are straight (a schematic).
import { fmt } from "./model";

const FEEDERS = {
  "psu-a": ["pierce-1", "lmp-1", "plasma-1", "press-1", "press-2", "weld-1", "weld-2", "rivet-1", "qa-scanner-1", "conveyor-1", "crane-1", "crane-2"],
  "psu-b": ["compressor-1", "cool-1", "blast-1", "ced-1", "chiller-1"],
  "psu-c": ["pallet-1", "washer-1", "tight-1", "tight-2", "cold-1", "hot-1"],
};
const FLOW = [
  // side members
  ["pierce-1", "lmp-1"], ["lmp-1", "blast-1"], ["blast-1", "ced-1"],
  // cross members: brackets, bends and gussets, either weld cell
  ["plasma-1", "press-1"], ["plasma-1", "press-2"], ["press-1", "weld-1"], ["press-1", "weld-2"], ["press-2", "weld-1"], ["press-2", "weld-2"],
  ["weld-1", "blast-1"], ["weld-2", "blast-1"],
  // frame assembly
  ["ced-1", "rivet-1"], ["rivet-1", "qa-scanner-1"], ["qa-scanner-1", "conveyor-1"],
  // engine line (pallet-1 carries the engine through the stations)
  ["washer-1", "cool-station"], ["cool-station", "leak-1"], ["leak-1", "tight-1"], ["tight-1", "tight-2"], ["tight-2", "cold-1"], ["cold-1", "hot-1"],
];

export const OVERLAYS = [["power", "Power"], ["air", "Air"], ["water", "Water"], ["gas", "Gas"], ["flow", "Flow"]];

const val = (v, tag, unit) => (v == null ? "—" : `${fmt(v, tag)} ${unit}`);
const kw = (m, s) => (m.stations[s]?.kw == null ? "" : `${fmt(m.stations[s].kw)} kW`);

// The overlay of one kind for the view-model m: members (stations that stay solid), links
// [{from, to}] and labels [{at, name, text}].
export function buildOverlay(kind, m) {
  const v = m.values;
  if (kind === "power") {
    const users = Object.values(FEEDERS).flat();
    const volts = Object.keys(FEEDERS).map((f) => `${f} ${val(v[`${f}.voltage_v`], `${f}.voltage_v`, "V")}`).join(", ");
    return {
      note: "The incomer, TR-1 and TR-2, the LV boards, then a thin line to each station on its feeder.",
      members: ["incomer-1", "tr-1", "tr-2", "mcc", ...users],
      links: [...[["incomer-1", "tr-1"], ["incomer-1", "tr-2"], ["tr-1", "mcc"], ["tr-2", "mcc"]].map(([from, to]) => ({ from, to })),
        ...users.map((s) => ({ from: "mcc", to: s, thin: true }))],
      labels: [
        { at: "incomer-1", name: "incomer-1", text: `${val(v["incomer-1.p_kw"], "incomer-1.p_kw", "kW")}, PF ${fmt(v["incomer-1.pf"], "x.pf")}` },
        { at: "tr-1", name: "TR-1", text: `${val(v["tr-1.load_pct"], "tr-1.load_pct", "%")} load` },
        { at: "tr-2", name: "TR-2", text: `${val(v["tr-2.load_pct"], "tr-2.load_pct", "%")} load` },
        { at: "mcc", name: "LV boards", text: volts },
        ...users.map((s) => ({ at: s, name: s, text: kw(m, s) })),
      ],
    };
  }
  if (kind === "air") {
    return {
      note: "compressor-1 feeds the air header. In the plant sim, plasma-1 (shield air) is the only air user.",
      members: ["compressor-1", "plasma-1"],
      links: [{ from: "compressor-1", to: "plasma-1", text: "air header" }],
      labels: [
        { at: "compressor-1", name: "compressor-1", text: `${val(v["compressor-1.pressure_barg"], "compressor-1.pressure_barg", "barg")}, ${m.stations["compressor-1"]?.text || ""}` },
        { at: "plasma-1", name: "plasma-1", text: `shield air, ${m.stations["plasma-1"]?.text || ""}` },
      ],
    };
  }
  if (kind === "water") {
    const users = ["compressor-1", "press-1", "lmp-1", "hot-1"];
    return {
      note: "The cool-1 loop cools four branches. chiller-1 holds the CED bath.",
      members: ["cool-1", ...users, "chiller-1", "ced-1"],
      links: [...users.map((s) => ({ from: "cool-1", to: s })), { from: "chiller-1", to: "ced-1", text: "bath loop" }],
      labels: [
        { at: "cool-1", name: "cool-1", text: `supply ${val(v["cool-1.supply_c"], "cool-1.supply_c", "°C")}, return ${val(v["cool-1.return_c"], "cool-1.return_c", "°C")}, ${val(v["cool-1.flow_m3h"], "cool-1.flow_m3h", "m³/h")}` },
        { at: "compressor-1", name: "compressor-1", text: "oil and air coolers" },
        { at: "press-1", name: "press-1", text: `oil ${val(v["press-1.oil_c"], "press-1.oil_c", "°C")}` },
        { at: "lmp-1", name: "lmp-1", text: `oil ${val(v["lmp-1.oil_c"], "lmp-1.oil_c", "°C")}` },
        { at: "hot-1", name: "hot-1", text: "cell heat exchanger" },
        { at: "chiller-1", name: "chiller-1", text: `leaving ${val(v["chiller-1.lwt_c"], "chiller-1.lwt_c", "°C")}` },
        { at: "ced-1", name: "ced-1", text: `bath ${val(v["ced-1.bath_c"], "ced-1.bath_c", "°C")}` },
      ],
    };
  }
  if (kind === "gas") {
    return {
      note: "gas-1 feeds the CED tank burners and the cure oven.",
      members: ["gas-1", "ced-1"],
      links: [{ from: "gas-1", to: "ced-1" }],
      labels: [
        { at: "gas-1", name: "gas-1", text: `header ${val(v["gas-1.header_kpa"], "gas-1.header_kpa", "kPa")}` },
        { at: "ced-1", name: "ced-1", text: `tanks ${val(v["ced-1.tank_gas_m3h"], "ced-1.tank_gas_m3h", "m³/h")}, oven ${val(v["ced-1.oven_gas_m3h"], "ced-1.oven_gas_m3h", "m³/h")}` },
      ],
    };
  }
  if (kind === "flow") {
    const members = [...new Set(FLOW.flat())];
    const n = (s) => m.at?.[s] || 0;
    return {
      note: "The routes of the frame line and the engine line. The number is the tracked IDs at the station and its buffers.",
      members,
      links: FLOW.map(([from, to]) => ({ from, to })),
      labels: members.map((s) => ({ at: s, name: s, text: String(n(s)) })),
    };
  }
  return null;
}
