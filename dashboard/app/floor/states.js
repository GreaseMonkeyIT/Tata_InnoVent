// Station states for the 3D floor (2026-10-18). Each station reports its own state words (its
// controller's phase: plant tags `state`, `phase`, `stage`, or the rules of plant/floor/replay.py).
// This table maps each word to one of five classes and to the words the plate shows. The classes
// follow the indicator light colors of IEC 60204-1 (10.3.2): green normal operation, yellow an
// abnormal condition, blue an action the operator must take, red a fault, white neutral.
//   active  the station does its work (green)         idle   ready, waits for a part (white)
//   setup   a person must act: load, change, rig (blue)
//   warn    abnormal, still running (yellow)           fault  stopped by a fault or a trip (red)

const T = (cls, text) => ({ cls, text });

// word -> class, text; per station kind. A word not in its table falls back to COMMON.
const COMMON = {
  idle: T("idle", "idle"), wait: T("idle", "waiting"), run: T("idle", "ready"),
  tripped: T("fault", "tripped"), fault: T("fault", "fault"), jam: T("fault", "jam"),
  reboot: T("fault", "rebooting"), locked: T("fault", "locked"),
};

const BY_STATION = {
  "pierce-1": { pierce: T("active", "piercing"), change: T("setup", "punch change") },
  "lmp-1": { approach: T("active", "ram down"), bend: T("active", "forming"), hold: T("active", "hold"),
    decompress: T("active", "decompress"), return: T("active", "ram up") },
  "press-1": { approach: T("active", "ram down"), bend: T("active", "bending"), hold: T("active", "hold"),
    decompress: T("active", "decompress"), return: T("active", "ram up") },
  "press-2": { stroke: T("active", "stroke"), braking: T("active", "braking") },
  "plasma-1": { load: T("setup", "load plate"), traverse: T("active", "traverse"), cut: T("active", "cutting"),
    change: T("setup", "consumable change"), gas_fault: T("fault", "gas pressure low") },
  "weld-1": { change: T("setup", "part change"), weld: T("active", "arc on"), move: T("active", "robot move") },
  "weld-2": { change: T("setup", "part change"), weld: T("active", "arc on"), move: T("active", "robot move") },
  "blast-1": { blast: T("active", "blasting") },
  "rivet-1": { place: T("setup", "place tool"), swage: T("active", "swaging") },
  "qa-scanner-1": { scan: T("active", "measuring") },
  "conveyor-1": { moving: T("active", "moving") },
  "crane-1": { hoist: T("active", "hoist"), travel: T("active", "travel"), lower: T("active", "lower"), return: T("active", "return") },
  "crane-2": { hoist: T("active", "hoist"), travel: T("active", "travel"), lower: T("active", "lower"), return: T("active", "return") },
  "washer-1": { spray: T("active", "spray"), dry: T("active", "dry") },
  "pallet-1": { move: T("active", "pallet moving") },
  "tight-1": { rundown: T("active", "rundown") },
  "tight-2": { rundown: T("active", "rundown") },
  "cold-1": { test: T("active", "cold test") },
  "hot-1": { rig: T("setup", "rig engine"), test: T("active", "hot test") },
  "compressor-1": { loaded: T("active", "loaded"), unloaded: T("idle", "unloaded"), stopped: T("idle", "stopped") },
};

// ced-1 reports its stage ("degrease", ..., "uf", "idle"); "hoist>" before a stage while the hoist
// carries the load to that tank.
const CED_TEXT = { degrease: "degrease", rinse1: "rinse 1", phosphate: "phosphate", rinse2: "rinse 2", ced: "CED coat", uf: "UF rinse" };
function cedState(word) {
  if (!word || word === "idle") return T("idle", "idle");
  if (word.startsWith("hoist>")) return T("active", `hoist to ${CED_TEXT[word.slice(6)] || word.slice(6)}`);
  return T("active", CED_TEXT[word] || word);
}

export function classify(station, word) {
  if (station === "ced-1") return cedState(word);
  const own = BY_STATION[station]?.[word];
  if (own) return own;
  if (COMMON[word]) return COMMON[word];
  if (word == null) return T("idle", "no state");
  return T("idle", String(word));
}

// Stations with no state word of their own: a class from a measured value.
export function derived(station, v) {
  if (station === "leak-1") return T("idle", v["leak-1.last_decay_pa"] != null ? `last ${v["leak-1.last_decay_pa"].toFixed(1)} Pa` : "ready");
  if (station === "cool-1") return v["cool-1-tower.fan_on"] ? T("active", "fan on") : T("idle", "fan off");
  if (station === "cool-station") return T("idle", "cooling");
  if (station === "chiller-1") return (v["chiller-1.power_kw"] ?? 0) > 1 ? T("active", "cooling") : T("idle", "standby");
  return null;
}
