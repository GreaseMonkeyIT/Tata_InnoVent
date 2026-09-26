"""2F.2 tag logic — the SCADA view of the plant, pure and unit-testable.

The tag DB lives here as code: every tag carries its ISA-style name, engineering unit, PLC
address, and scaling, per plc/REGISTER_MAP.md (the one contract). tagserver.py owns the I/O
(Modbus poll, historian writes, HTTP); this module owns decode, derived tags, quality, and
the Prometheus exposition — so the whole mapping is testable without a PLC.

Two tag kinds, said honestly in the tag record:
  measured  — read from a PLC register/coil this poll.
  derived   — computed from measured tags + the static plant topology (a machine sees its
              rail's voltage; heat = k · I). Standard SCADA calculated-tag practice; the
              constants are calibration data, not live physics.

Quality: GOOD (fresh read) · STALE (last good read older than stale_s) · BAD (never read /
read failing). /metrics only ever exports GOOD or STALE values — a BAD tag is absent, never
invented; the engine correctly sees a gap instead of a lie.
"""
from __future__ import annotations

import math
import time

NS = "plant"
MW = 1024            # OpenPLC %MW0 offset in holding space (PLC_MW_BASE; REGISTER_MAP.md)
N_REGS = 32          # MW0..31 read block

# Static plant topology — mirrors plant/sim/main.py DEVICES (order = the register contract).
# heat_frac (share of electrical power into the loop water) and trip_c (the OpenPLC trip) are
# SCADA calibration data, the same values as the sim (LOG-100).
MACHINES = [
    # name           rail     cooled  heat_frac  trip_c
    ("press-1",      "psu-a", True,   0.5,       80.0),
    ("press-2",      "psu-a", True,   0.5,       80.0),
    ("cnc-1",        "psu-a", True,   0.4,       80.0),
    ("qa-scanner-1", "psu-a", False,  None,      None),
    ("conveyor-1",   "psu-b", False,  None,      None),
    ("compressor-1", "psu-b", False,  0.8,       None),   # water-cooled: heat, no temperature
    ("furnace-1",    "psu-b", True,   0.3,       55.0),
    ("chiller-1",    "psu-b", False,  None,      None),
]
COOLED = [m[0] for m in MACHINES if m[2]]          # press-1, press-2, cnc-1, furnace-1
HEATED = [m[0] for m in MACHINES if m[3]]          # every machine that heats the loop water
TRIP_LIMITS = {m[0]: m[4] for m in MACHINES if m[4] is not None}
TRIP_C = TRIP_LIMITS["press-1"]                    # the press trip, kept for older readers
RAILS = ["psu-a", "psu-b"]
LOOP = "cool-1"
CHILLER = "chiller-1"
COMPRESSOR = "compressor-1"
# Loop calibration for the calculated cooling shortfall (the same formula as the sim).
PF_NOMINAL = 0.85
LOOP_T_SETPOINT = 28.0
FLOW_DESIGN_LPM = 120.0
CP_KJ_PER_KG_K = 4.186


def heat_w(frac: float, volts: float, amps: float) -> float:
    """A SCADA calculated tag: the heat a machine puts into the loop water, in W."""
    return frac * math.sqrt(3.0) * volts * amps * PF_NOMINAL


def shortfall_w(heats: dict, flow: float, t_supply: float) -> float:
    """The cooling the machines lose against design, in W (plant/sim/main.py cooling_shortfall):
    flow below design keeps q(1/s - 1) at each cooled machine, and supply water above the
    setpoint carries cp * design flow * the excess back to all of them."""
    share = max(flow / FLOW_DESIGN_LPM, 0.05)
    q = sum(heats.values()) / 1000.0
    kw_per_k = CP_KJ_PER_KG_K * FLOW_DESIGN_LPM / 60.0
    return 1000.0 * max(0.0, q * (1.0 / share - 1.0) + kw_per_k * max(0.0, t_supply - LOOP_T_SETPOINT))

_tagname = lambda asset, sig: f"PLANT.{asset.upper().replace('-', '_')}.{sig}"


def tag_table() -> list[dict]:
    """The full tag DB: one row per tag with address, unit, scale, kind."""
    t: list[dict] = []

    def add(asset, sig, unit, kind, addr, scale=None):
        t.append({"tag": _tagname(asset, sig), "asset": asset, "signal": sig, "unit": unit,
                  "kind": kind, "address": addr, "scale": scale})

    for i, name in enumerate(COOLED):                       # MW0..3 temps x10
        add(name, "TEMP", "degC", "measured", f"%MW{i}", 10)
    add(LOOP, "FLOW", "L/min", "measured", "%MW4", 10)
    add(LOOP, "PUMP_HEALTH", "ratio", "measured", "%MW5", 100)
    for i, rail in enumerate(RAILS):                        # MW6..7 rail volts x10
        add(rail, "VOLTS", "V", "measured", f"%MW{6 + i}", 10)
    for i, (name, *_rest) in enumerate(MACHINES):           # MW8..15 amps x10
        add(name, "AMPS", "A", "measured", f"%MW{8 + i}", 10)
    for i, (name, *_rest) in enumerate(MACHINES):           # MW24..31 throughput x10
        add(name, "THROUGHPUT", "pct", "measured", f"%MW{24 + i}", 10)
    for i, name in enumerate(COOLED):                       # coils 0..3 trips
        add(name, "TRIP", "bool", "measured", f"%QX0.{i}", None)
    add(LOOP, "SUPPLY_TEMP", "degC", "measured", "%MW16", 10)          # LOG-100
    add(COMPRESSOR, "AIR_PRESSURE", "bar", "measured", "%MW17", 100)  # the transducer reading
    for name, rail, *_r in MACHINES:                        # derived: volts a machine sees
        add(name, "VOLTS", "V", "derived", f"= {_tagname(rail, 'VOLTS')}", None)
    for name, _rail, _c, frac, _t in MACHINES:              # derived: heat into the loop water
        if frac:
            add(name, "HEAT", "W", "derived",
                f"= {frac} * sqrt3 * {_tagname(name, 'VOLTS')} * {_tagname(name, 'AMPS')} * {PF_NOMINAL}", None)
    for name, limit in TRIP_LIMITS.items():                 # derived: each machine's own trip
        add(name, "TRIP_LIMIT", "degC", "derived", f"= const {limit}", None)
    add(CHILLER, "COOLING_SHORTFALL", "W", "derived",
        f"= heat * (1/share - 1) + cp * design flow * ({_tagname(LOOP, 'SUPPLY_TEMP')} - {LOOP_T_SETPOINT})", None)
    return t


def decode(regs: list[int] | None, coils: list[bool] | None, ts: float) -> dict[str, dict]:
    """regs = MW0..31 (holding block), coils = QX0.0..0.3 -> {tag: {value, quality, ts}}.
    decode runs only on a successful read, so everything it emits is GOOD at `ts`; when a
    poll FAILS the server keeps the previous map and lets requality() age it honestly."""
    out: dict[str, dict] = {}

    def put(asset, sig, val):
        out[_tagname(asset, sig)] = {"value": val, "quality": "GOOD", "ts": ts,
                                     "asset": asset, "signal": sig}

    if regs is not None and len(regs) >= N_REGS:
        for i, name in enumerate(COOLED):
            put(name, "TEMP", regs[i] / 10.0)
        put(LOOP, "FLOW", regs[4] / 10.0)
        put(LOOP, "PUMP_HEALTH", regs[5] / 100.0)
        rail_v = {}
        for i, rail in enumerate(RAILS):
            rail_v[rail] = regs[6 + i] / 10.0
            put(rail, "VOLTS", rail_v[rail])
        for i, (name, rail, *_r) in enumerate(MACHINES):
            amps = regs[8 + i] / 10.0
            put(name, "AMPS", amps)
            put(name, "VOLTS", rail_v[rail])                 # derived
            put(name, "THROUGHPUT", regs[24 + i] / 10.0)
        put(LOOP, "SUPPLY_TEMP", regs[16] / 10.0)
        put(COMPRESSOR, "AIR_PRESSURE", regs[17] / 100.0)
        cooled_heat = {}
        for name, rail, cooled, frac, _t in MACHINES:        # derived heat into the loop water
            if frac:
                w = heat_w(frac, rail_v[rail], out[_tagname(name, "AMPS")]["value"])
                put(name, "HEAT", w)
                if cooled:
                    cooled_heat[name] = w
        for name, limit in TRIP_LIMITS.items():
            put(name, "TRIP_LIMIT", limit)
        put(CHILLER, "COOLING_SHORTFALL", shortfall_w(cooled_heat, regs[4] / 10.0, regs[16] / 10.0))
    if coils is not None:
        for i, name in enumerate(COOLED):
            put(name, "TRIP", 1.0 if (i < len(coils) and coils[i]) else 0.0)
    return out


def requality(tags: dict[str, dict], now: float, stale_s: float = 10.0,
              bad_s: float = 30.0) -> dict[str, dict]:
    """Age-based quality: GOOD within stale_s of the last good read, STALE after, BAD past
    bad_s. Pure — returns a new map; the server calls this every poll (fail or not)."""
    out: dict[str, dict] = {}
    for k, rec in tags.items():
        age = now - rec["ts"]
        q = "GOOD" if age <= stale_s else ("STALE" if age <= bad_s else "BAD")
        out[k] = {**rec, "quality": q}
    return out


# metric name per SCADA signal — MUST stay identical to plant-sim's exposition so the
# aggregator repoint (queries.yaml URL swap at cutover) needs zero engine/config changes.
_METRIC = {
    "VOLTS": "plant_bus_voltage_volts",
    "AMPS": "plant_current_draw_amps",
    "TEMP": "plant_temp_celsius",
    "HEAT": "plant_heat_load_watts",
    "FLOW": "plant_coolant_flow_lpm",
    "PUMP_HEALTH": "plant_pump_health_ratio",
    "THROUGHPUT": "plant_throughput_pct",
    "TRIP": "plant_trip_active",
    "TRIP_LIMIT": "plant_trip_threshold_celsius",
    "SUPPLY_TEMP": "plant_supply_temp_celsius",
    "AIR_PRESSURE": "plant_air_pressure_bar",
    "COOLING_SHORTFALL": "plant_cooling_shortfall_watts",
}


def prom_text(tags: dict[str, dict], extra: list[str] | None = None) -> str:
    """Prometheus exposition with the same names + namespace/pod labels the sim serves.
    BAD tags are absent by construction (decode never emits them); STALE values still
    export (last known engineering value) — the freshness truth lives in /tags quality."""
    lines: list[str] = []
    for rec in tags.values():
        metric = _METRIC.get(rec["signal"])
        if metric is None or rec.get("quality") == "BAD":
            continue
        lines.append(f'{metric}{{namespace="{NS}",pod="{rec["asset"]}"}} {rec["value"]:.4f}')
    lines.extend(extra or [])
    return "\n".join(lines) + "\n"


def engine_parity_metrics() -> set[str]:
    """Every metric name the aggregator's queries.yaml maps to an engine signal — the
    cutover contract. Tested so a rename here can't silently break the repoint."""
    return {"plant_bus_voltage_volts", "plant_current_draw_amps", "plant_temp_celsius",
            "plant_coolant_flow_lpm", "plant_heat_load_watts", "plant_throughput_pct",
            "plant_trip_threshold_celsius", "plant_cooling_shortfall_watts"}


# LOG-100: the series that stay on plant-sim after the cutover, because no PLC in this plant reads
# them. A real plant reads them from other instruments: the power meters (energy monitoring), the
# managed switch (SNMP), and the chiller's own relay contacts. The plant-sim ServiceMonitor
# (plant/deploy.yaml metricRelabelings) keeps exactly these. The rest comes from the tag server.
INSTRUMENT_METRICS = {"plant_feeder_current_amps", "plant_net_offered_fps", "plant_net_latency_ms",
                      "plant_net_utilization_ratio", "plant_net_drop_ratio", "plant_motor_tripped",
                      "plant_overload_ratio", "plant_supply_nominal_volts", "plant_commanded_speed_pct",
                      "plant_cell_connected", "plant_plc_connected", "plant_fault_active"}
INSTRUMENT_BOARD = {"plant_bus_voltage_volts", "plant_current_draw_amps"}   # for pod incomer-1 only
