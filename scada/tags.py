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

import time

NS = "plant"
MW = 1024            # OpenPLC %MW0 offset in holding space (PLC_MW_BASE; REGISTER_MAP.md)
N_REGS = 32          # MW0..31 read block
TRIP_C = 78.0

# Static plant topology — mirrors plant/sim/main.py DEVICES (order = the register contract).
# heat_k is the sim's thermal calibration; carried here as SCADA calibration data.
MACHINES = [
    # name           rail     cooled  heat_k
    ("press-1",      "psu-a", True,   0.55),
    ("press-2",      "psu-a", True,   0.55),
    ("cnc-1",        "psu-a", True,   0.55),
    ("qa-scanner-1", "psu-a", False,  None),
    ("conveyor-1",   "psu-b", False,  None),
    ("compressor-1", "psu-b", False,  None),
    ("furnace-1",    "psu-b", True,   1.0),
    ("chiller-1",    "psu-b", False,  None),
]
COOLED = [m[0] for m in MACHINES if m[2]]          # press-1, press-2, cnc-1, furnace-1
RAILS = ["psu-a", "psu-b"]
LOOP = "cool-1"

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
    for name, rail, *_r in MACHINES:                        # derived: volts a machine sees
        add(name, "VOLTS", "V", "derived", f"= {_tagname(rail, 'VOLTS')}", None)
    for name in COOLED:                                     # derived: heat = k * I
        k = next(m[3] for m in MACHINES if m[0] == name)
        add(name, "HEAT", "W", "derived", f"= {k} * {_tagname(name, 'AMPS')}", None)
    add(LOOP, "TRIP_LIMIT", "degC", "derived", f"= const {TRIP_C}", None)
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
        for name, rail, cooled, heat_k in MACHINES:          # derived heat
            if cooled:
                put(name, "HEAT", heat_k * out[_tagname(name, "AMPS")]["value"])
        put(LOOP, "TRIP_LIMIT", TRIP_C)
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
            "plant_trip_threshold_celsius"}
