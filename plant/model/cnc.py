"""cnc-1: a large horizontal boring mill machining the pin bores of excavator booms (ideas.md 11.5).

Cutting power (Sandvik Coromant turning/boring formula, the boring bar as a single point):
    Pc = vc x ap x fn x kc / (60 x 10^3)  kW,   kc = kc1 x hm^-mc,  hm = fn sin(kappa_r)
with vc in m/min, ap and fn in mm, kc in N/mm2 (Kienzle). S355 sits in Sandvik's P1.1/P1.2 groups:
kc1 about 1500 to 1770 N/mm2, mc 0.25.
Tool wear: the flank wear VB grows with cutting time in the steady region (linear, project choice
for the rate) and the cutting power rises with it, K = 1 + 0.0011 VB [um] (Kovalcik et al., MM
Science Journal 2020: +27 % at VB 244 um, milling C45). The tool is changed at VB = 0.3 mm, the
ISO 3685 tool-life criterion.
Spindle drive: a VFD and spindle motor, 22 kW continuous and 26 kW for 30 min, 3383 N m, 2500 rpm
(DN Solutions DBC 130). Electrical input = cutting power / drive efficiency + the base load (the
hydraulic unit, the coolant pump, the chip conveyor, the axes and the controls). The drive trips
on its own DC bus undervoltage in a deep sag.
Heat: the spindle losses go to the spindle cooler on cool-1.
Faults (parameters only): tool wear (F4, the normal growth; a faster wear rate), a broken tool (the
power falls to the air-cut level), spindle bearing wear (more friction power), coolant pump failure.
"""
import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CncRating:
    p_s1_kw: float
    p_30min_kw: float
    torque_nm: float
    rpm_max: float
    supply_kva: float
    base_kw: float                 # hydraulics, coolant, chip conveyor, axes, controls (project choice)
    eta_drive: float = 0.85        # VFD + spindle motor + gearbox (project choice)
    uv_trip_pu: float = 0.75       # VFD undervoltage: trips under this share of nominal ...
    uv_trip_s: float = 0.05        # ... held this long (project choice, typical ride-through)
    v_nom: float = 400.0
    source: str = ""


@dataclass
class Op:
    """One boring pass."""
    name: str
    vc: float          # m/min
    ap: float          # mm
    fn: float          # mm/rev
    d_mm: float        # bore diameter
    length_mm: float


@dataclass
class Material:
    kc1: float = 1700.0
    mc: float = 0.25


class BoringMill:
    CUT, IDLE, TOOL_CHANGE, LOAD, TRIPPED = "cut", "idle", "tool_change", "load_part", "tripped"

    def __init__(self, name, rating, program=None, load_s=600.0, tool_change_s=120.0):
        self.name, self.r = name, rating
        self.program = program or [
            Op("rough bore", 120.0, 4.0, 0.35, 110.0, 160.0),
            Op("semi-finish", 140.0, 1.5, 0.25, 118.0, 160.0),
            Op("finish bore", 160.0, 0.5, 0.15, 121.0, 160.0),
        ] * 4                                   # four pin bores per boom (project choice)
        self.mat = Material()
        self.load_s, self.tool_change_s = load_s, tool_change_s
        self.state, self.t_state = self.LOAD, 0.0
        self.op_i, self.op_left = 0, 0.0
        self.vb_um = 50.0                       # a fresh insert after the run-in
        self.wear_um_per_min = 4.0              # project choice: about 60 min of cutting to 0.3 mm
        self.parts = 0
        self.tools = 1
        self.broken = False
        self.bearing_kw = 0.0                   # fault: extra spindle bearing friction
        self.coolant_ok = True
        self.p_cut = self.p_el = 0.0
        self.rpm = 0.0
        self.heat_w = 0.0
        self._uv = 0.0

    def kc(self, op):
        hm = op.fn                               # kappa_r = 90 degrees
        return self.mat.kc1 * hm ** (-self.mat.mc)

    def cut_power_kw(self, op):
        k_vb = 1.0 + 0.0011 * self.vb_um
        return op.vc * op.ap * op.fn * self.kc(op) / 60000.0 * k_vb

    def step(self, dt, v_ll):
        r = self.r
        self.t_state += dt
        # VFD undervoltage
        if v_ll < r.uv_trip_pu * r.v_nom:
            self._uv += dt
            if self._uv >= r.uv_trip_s and self.state != self.TRIPPED:
                self.state, self.t_state = self.TRIPPED, 0.0
        else:
            self._uv = 0.0
        self.p_cut, self.rpm = 0.0, 0.0
        if self.state == self.LOAD:
            if self.t_state >= self.load_s:
                self.state, self.t_state, self.op_i = self.CUT, 0.0, 0
                self.op_left = self._op_time(self.program[0])
        elif self.state == self.CUT:
            op = self.program[self.op_i]
            self.rpm = min(r.rpm_max, op.vc * 1000.0 / (math.pi * op.d_mm))
            if self.broken:
                self.p_cut = 0.05 * self.cut_power_kw(op)        # air cut: the insert is gone
            else:
                self.p_cut = self.cut_power_kw(op)
                self.vb_um += self.wear_um_per_min * dt / 60.0
            self.op_left -= dt
            if self.op_left <= 0:
                self.op_i += 1
                if self.vb_um >= 300.0:
                    self.state, self.t_state = self.TOOL_CHANGE, 0.0
                elif self.op_i >= len(self.program):
                    self.parts += 1
                    self.state, self.t_state = self.LOAD, 0.0
                else:
                    self.op_left = self._op_time(self.program[self.op_i])
        elif self.state == self.TOOL_CHANGE:
            if self.t_state >= self.tool_change_s:
                self.vb_um, self.tools, self.broken = 50.0, self.tools + 1, False
                if self.op_i >= len(self.program):
                    self.parts += 1
                    self.state, self.t_state = self.LOAD, 0.0
                else:
                    self.state, self.t_state = self.CUT, 0.0
                    self.op_left = self._op_time(self.program[self.op_i])
        running = self.state != self.TRIPPED
        p_spindle = self.p_cut + (self.bearing_kw + 0.02 * r.p_s1_kw * (self.rpm / r.rpm_max) if self.rpm else 0.0)
        base = r.base_kw * (1.0 if self.coolant_ok else 0.7) if running else 0.0
        self.p_el = (p_spindle / r.eta_drive + base) if running else 0.0
        self.heat_w = 1000.0 * (p_spindle / r.eta_drive - self.p_cut) if running else 0.0

    def _op_time(self, op):
        n = op.vc * 1000.0 / (math.pi * op.d_mm)
        return op.length_mm / (op.fn * n) * 60.0

    def tags(self):
        return {"spindle_kw": self.p_cut, "power_kw": self.p_el, "spindle_rpm": self.rpm,
                "spindle_load_pct": 100.0 * self.p_cut / self.r.p_s1_kw, "parts": self.parts,
                "tool": self.tools, "state": self.state}

    def check(self):
        out = []
        if self.p_cut > self.r.p_30min_kw * 1.5:
            out.append(f"{self.name}: cutting power beyond the spindle drive")
        if self.vb_um < 0 or self.p_el < 0:
            out.append(f"{self.name}: negative wear or power")
        return out
