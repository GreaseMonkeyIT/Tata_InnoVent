"""The plant faults of ideas.md 11.10 (F1 to F16) as one registry. Nothing else.

2026-10-15 (the frame line, operator's word): F4 moved from cnc-1 tool wear to pierce-1 punch wear
(the same kind: a slow drift on a cyclic machine), F10 now reaches the CED tank burners and cure
oven (furnace-1 left the plant). No other fault changed.

Each fault changes one physical parameter of a model that already has it (11.9: a fault never sets
an output value), so two faults combine through the physics. The registry adds no fault and no
physics: it only names the parameter and the unit of the magnitude.

`magnitude` is in the unit of the parameter. The range that a test draws from comes from one rule
for every fault (fault_ranges.py, not here), so no fault gets a hand-picked range.

    from plant.model.faults import FAULTS, inject, clear
    inject(plant, "F1", 0.04)
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Fault:
    fid: str
    title: str
    node: str            # the true root (11.10 "Expected root"), "" for a control case
    control: bool        # a load or product change, not a machine fault (F14, F15, F16)
    unit: str
    healthy: float       # the parameter value of a healthy plant (the magnitude that means "no fault")
    set: Callable        # set(plant, magnitude)


def _press_friction(p, m):
    p.press.friction_extra_n = m * p.press.r.force_kn * 1e3


def _press_leak(p, m):
    p.press.leak_factor = m


def _conveyor_jam(p, m):
    p.conv.jammed = m >= 0.5


def _punch_wear(p, m):
    p.pierce.wear_rate = m


def _weld_tip(p, m):
    p.welders[0].tip_wear = m


def _air_leak(p, m):
    p.comp.leak_m3s_bar = p._leak_healthy * m


def _transducer(p, m):
    p.comp.pt_fault_bar = None if m is None else m


def _loop_pump(p, m):
    p.loop.pump.wear = m


def _tower_fill(p, m):
    p.tower.tower.fouling = m


def _gas_supply(p, m):
    p.gas.p_supply = m


def _grid_sag(p, m):
    if m >= 1.0:
        p.grid.clear()
    else:
        p.grid.dip(m)


def _dyno_trip(p, m):
    p.hot.dyno_trip = m >= 0.5


def _washer_heater(p, m):
    p.washer.offset_k = m


def _hot_heavy(p, m):
    p.eflow.H_HOT = m


def _hard_plate(p, m):
    p._plate_override = None if m is None else m


def _tight_engine(p, m):
    p.cold.tight = m


FAULTS = {f.fid: f for f in (
    Fault("F1", "press-1 ram guide friction rises", "press-1", False, "share of rated force", 0.0, _press_friction),
    Fault("F2", "press-1 pump internal leakage rises", "press-1", False, "x healthy leakage", 1.0, _press_leak),
    Fault("F3", "conveyor-1 jam", "conveyor-1", False, "on (>= 0.5)", 0.0, _conveyor_jam),
    Fault("F4", "pierce-1 punch wears faster", "pierce-1", False, "x healthy wear per hit", 1.0, _punch_wear),
    Fault("F5", "weld-1 unstable arc (worn contact tip)", "weld-1", False, "tip wear 0..1", 0.0, _weld_tip),
    Fault("F6", "plant air leak grows", "compressor-1", False, "x healthy leakage", 1.0, _air_leak),
    Fault("F7", "compressor-1 pressure transducer fails low", "compressor-1", False, "reading, bar(e)", None, _transducer),
    Fault("F8", "cool-1 pump wear", "cool-1", False, "wear 0..1 (head factor 1 - wear)", 0.0, _loop_pump),
    Fault("F9", "cooling tower fill fouling", "cool-1", False, "share of fill characteristic lost", 0.0, _tower_fill),
    Fault("F10", "fuel gas supply pressure low (ced-1 tank burners and cure oven)", "gas-1", False,
          "supply pressure, kPa(g)", None, _gas_supply),
    Fault("F11", "grid sag on the 11 kV incomer", "incomer-1", False, "residual voltage, pu", 1.0, _grid_sag),
    Fault("F12", "hot-1 dynamometer drive trips", "hot-1", False, "on (>= 0.5)", 0.0, _dyno_trip),
    Fault("F13", "washer-1 heater control holds the tank hotter", "washer-1", False, "K above setpoint", 0.0, _washer_heater),
    Fault("F14", "hot-1 runs a heavy schedule (no fault)", "", True, "mean s to rig the next engine", None, _hot_heavy),
    Fault("F15", "harder plate at press-1 (no machine fault)", "", True, "plate UTS, MPa", None, _hard_plate),
    Fault("F16", "a tight engine at cold-1 (product fault)", "", True, "share of extra friction", 0.0, _tight_engine),
)}


def inject(plant, fid, magnitude):
    FAULTS[fid].set(plant, magnitude)


def clear(plant, fid):
    f = FAULTS[fid]
    f.set(plant, f.healthy if f.healthy is not None else plant._healthy[fid])


BINARY = {"F3", "F12"}


def duration_s(fid, magnitude):
    """How long a transient fault lasts (None: it stays). F11: the IEC 61000-4-11 class 3 duration
    of its level (catalog.DIPS_CLASS3, cycles at 50 Hz; the longest for a level)."""
    if fid != "F11":
        return None
    from . import catalog as C
    cycles = max((c for lvl, c in C.DIPS_CLASS3 if abs(lvl - magnitude) < 1e-9), default=None)
    return None if cycles is None else cycles / 50.0
