"""Record a plant run for the 3D floor (2026-10-18): station states, a set of measured values and the
line tracking events (tracking.py), as one JSON file that the floor prototype plays back.

  python plant/floor/replay.py --seed 3 --hours 4 --warmup-h 3 --out dashboard/public/floor/replay.json

The recording holds what a line's control system has, nothing more:
  - states: each station's own state or phase as its controller reports it (a change list)
  - values: measured tags, every SAMPLE_S seconds after the warm-up
  - events: reads and transfers of the tracked IDs (tracking.py), from the start of the run, so the
    player knows where every ID is at the first frame it shows
A few states are not plant tags today; this file derives them from the station model and names the
rule (STATE_RULES). Each is a state the station's controller has.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model.plant import UtilitiesPlant                    # noqa: E402
from floor.tracking import Tracker                        # noqa: E402

DT = 0.1
SAMPLE_S = 2.0

# Measured values for the plates and the moving parts (the tag names of plant.tags()).
VALUES = [
    "incomer-1.p_kw", "incomer-1.pf", "tr-1.load_pct", "tr-2.load_pct",
    "psu-a.voltage_v", "psu-b.voltage_v", "psu-c.voltage_v", "psu-d.voltage_v",
    "compressor-1.pressure_barg", "compressor-1.power_kw", "compressor-1.element_c",
    "cool-1.supply_c", "cool-1.return_c", "cool-1.flow_m3h", "chiller-1.power_kw", "chiller-1.lwt_c",
    "gas-1.header_kpa",
    "pierce-1.power_kw", "pierce-1.pressure_bar", "pierce-1.holes", "pierce-1.punch_hits",
    "lmp-1.power_kw", "lmp-1.ram_mm", "lmp-1.pressure_bar", "lmp-1.oil_c",
    "plasma-1.power_kw", "plasma-1.arc_a", "plasma-1.electrode_starts",
    "press-1.power_kw", "press-1.ram_mm", "press-1.pressure_bar", "press-1.oil_c",
    "press-2.power_kw", "press-2.flywheel_rpm", "press-2.strokes",
    "weld-1.weld_a", "weld-1.power_kw", "weld-2.weld_a", "weld-2.power_kw",
    "blast-1.power_kw", "blast-1.filter_dp_pa", "blast-1.abrasive_kg_min",
    "ced-1.power_kw", "ced-1.bath_c", "ced-1.rect_a", "ced-1.oven_c", "ced-1.degrease_c", "ced-1.phosphate_c",
    "ced-1.oven_gas_m3h", "ced-1.tank_gas_m3h",
    "rivet-1.power_kw", "rivet-1.fasteners", "rivet-1.last_install_kn",
    "qa-scanner-1.power_w", "conveyor-1.power_kw", "conveyor-1.speed_mps",
    "crane-1.power_kw", "crane-1.lift_t", "crane-2.power_kw", "crane-2.lift_t",
    "cool-1-tower.fan_on", "cool-1-tower.basin_c",
    "washer-1.power_kw", "washer-1.tank_c", "leak-1.last_decay_pa", "pallet-1.power_kw",
    "tight-1.power_w", "tight-1.last_torque_nm", "tight-2.power_w", "tight-2.last_torque_nm",
    "cold-1.power_kw", "cold-1.torque_nm", "hot-1.power_kw", "hot-1.load_pct", "hot-1.brake_kw",
]

# States that are not a plant tag yet: the rule for each (all are states the station controller has).
STATE_RULES = {
    "blast-1": "blast while a part is in the cabinet (blast_left > 0), else idle",
    "rivet-1": "swage while a tool swages, place while fasteners are left, else idle",
    "tight-1": "rundown while bolts are left on the engine, else idle",
    "tight-2": "rundown while bolts are left on the engine, else idle",
    "cold-1": "test while the test runs, else idle",
    "pallet-1": "jam, move or idle",
    "crane-1": "the crane's lift phase: idle, hoist, travel, lower, return",
    "crane-2": "the crane's lift phase: idle, hoist, travel, lower, return",
    "qa-scanner-1": "reboot after a dip, scan while a frame is measured, else idle",
}


def states(p):
    tg = p.tags()
    out = {}
    for k, v in tg.items():
        if isinstance(v, str):
            out[k.split(".")[0]] = v
    out["blast-1"] = "blast" if p.blast.busy else "idle"
    rv = p.rivet
    out["rivet-1"] = "swage" if any(s > 0.0 for s in rv.swage_left) else "place" if rv.busy else "idle"
    for t in p.tight:
        out[t.name] = "rundown" if t.busy else "idle"
    out["cold-1"] = "test" if p.cold.busy else "idle"
    out["pallet-1"] = "jam" if p.pallet.jam else "move" if p.pallet.move_left > 0.0 else "idle"
    for c in p.cranes:
        out[c.name] = c.phase
    sc = p.scanner
    out["qa-scanner-1"] = "reboot" if sc.state == "reboot" else "scan" if sc.busy else "idle"
    if p.ced.load is not None:
        out["ced-1"] = ("hoist>" if p.ced.load["moving"] else "") + out.get("ced-1", "idle")
    return out


def record(seed=3, hours=4.0, warmup_h=3.0, t_day0_h=4.0, day_of_year=None, progress=True):
    p = UtilitiesPlant(seed=seed, t_day0_h=t_day0_h, weather="site", day_of_year=day_of_year)
    tr = Tracker(p)
    n_warm = int(round(warmup_h * 3600.0 / DT))
    n_all = n_warm + int(round(hours * 3600.0 / DT))
    every = int(round(SAMPLE_S / DT))
    st_prev, st_changes, rows = {}, [], []
    tags_ok = None
    t_wall = time.time()
    for i in range(n_all):
        p.step(DT)
        tr.step(p.t)
        if i % 10 == 9:                      # states each second
            s = states(p)
            for k, v in s.items():
                if st_prev.get(k) != v:
                    st_changes.append([round(p.t, 1), k, v])
                    st_prev[k] = v
        if i >= n_warm and (i - n_warm) % every == every - 1:
            tg = p.tags()
            if tags_ok is None:
                tags_ok = [k for k in VALUES if k in tg]
            rows.append([_r(tg[k]) for k in tags_ok])
        if progress and i % 36000 == 35999:
            print(f"  sim {p.t / 3600.0:.1f} h, wall {time.time() - t_wall:.0f} s, events {len(tr.events)}",
                  flush=True)
    problems = tr.check()
    return {
        "meta": {"seed": seed, "t_day0_h": t_day0_h, "warmup_h": warmup_h, "hours": hours, "dt": DT,
                 "t_start": round(n_warm * DT, 1), "t_end": round(p.t, 1), "sample_s": SAMPLE_S,
                 "frames_done": p.fflow.frames_done, "engines_done": p.eflow.engines_done,
                 "late_lifts": tr.late_lifts, "unmatched_lifts": tr.unmatched_lifts, "check": problems, "state_rules": STATE_RULES,
                 "made": time.strftime("%Y-%m-%d %H:%M")},
        "states": st_changes,
        "values": {"tags": tags_ok or [], "t0": round(n_warm * DT + SAMPLE_S, 1), "dt": SAMPLE_S, "rows": rows},
        "events": tr.events,
        "items": tr.items,
    }


def _r(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--warmup-h", type=float, default=3.0)
    ap.add_argument("--t-day0-h", type=float, default=4.0)
    ap.add_argument("--day", type=int, default=None, help="day of the year for the site weather")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "dashboard", "public", "floor", "replay.json"))
    a = ap.parse_args()
    rec = record(a.seed, a.hours, a.warmup_h, a.t_day0_h, a.day)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rec, fh, separators=(",", ":"))
    m = rec["meta"]
    print(f"wrote {a.out}: {len(rec['events'])} events, {len(rec['states'])} state changes, "
          f"{len(rec['values']['rows'])} value rows; frames {m['frames_done']}, engines {m['engines_done']}, "
          f"late lifts {m['late_lifts']}, check {m['check'] or 'clean'}")


if __name__ == "__main__":
    main()
