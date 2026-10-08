"""The remaining stations of the fabrication line (ideas.md 11.5): plate cutting, robotic welding,
shot blasting, the paint booth and bake oven, and the weldment inspection scanner.

Each station is a small physical model with the published numbers of a real unit (sources in
catalog.py and README.md). Electrical inputs are real power and a power factor (no motor model:
these are drives and power electronics, project choice for the level of detail).

  PlasmaCutter: arc power = I_arc x U_arc while cutting, input = arc / efficiency. It needs plant
    air at its inlet pressure (plasma O2 from bottles, the shield is plant air). Worn consumables
    (an eroded electrode) raise the arc voltage per pierce (project choice for the rate).
  WeldingCell: MAG arc with the conventional load line of IEC 60974-1, U2 = 14 + 0.05 I2 (V, A);
    input = arc / efficiency; seams and robot moves alternate; a worn contact tip makes the arc
    current unstable (more ripple). The torch cooler is a standalone air-cooled unit (the usual
    package).
  ShotBlast: each turbine takes power in proportion to its abrasive flow over its idle power; the
    dust collector fan works on a filter whose pressure drop builds with dust and falls at each
    pulse cleaning; clogged cartridges (a fault) stop the cleaning from recovering it.
  PaintBooth: exhaust fans with the filter pressure drop growing with paint overspray; a gas-fired
    bake oven (gas.py burner) holds 80 C for the paint cure.
  QaScanner: an electronic constant-power load on a power supply that rides through dips shorter
    than its hold-up time; a longer dip resets it and it reboots.
"""
import math
from dataclasses import dataclass

from .gas import Burner, BurnerRating


def _pf_q(p, pf):
    return p * math.tan(math.acos(pf)) if 0 < pf < 1 else 0.0


# ---------------------------------------------------------------------------------- plasma --
@dataclass(frozen=True)
class PlasmaRating:
    i_arc: float
    u_arc: float
    eff: float
    pf: float
    idle_w: float
    air_bar_min: float
    cut_mm_min: float          # cutting speed for the plate
    fan_kw: float
    source: str = ""


class PlasmaCutter:
    def __init__(self, name, r, cut_m_per_plate=24.0, pierces_per_plate=12, load_s=300.0):
        self.name, self.r = name, r
        self.cut_m, self.pierces, self.load_s = cut_m_per_plate, pierces_per_plate, load_s
        self.state, self.t_state = "load", 0.0
        self.seg_left = 0.0
        self.n_pierce = 0
        self.wear_v = 0.0               # extra arc voltage from electrode wear
        self.wear_v_per_pierce = 0.01   # project choice (V per pierce)
        self.air_bar = 7.5
        self.plates = 0
        self.p_el = 0.0
        self.u_arc = 0.0

    def step(self, dt):
        r = self.r
        self.t_state += dt
        seg_m = self.cut_m / self.pierces
        if self.state == "load" and self.t_state >= self.load_s:
            self.state, self.t_state, self.n_pierce = "traverse", 0.0, 0
        elif self.state == "traverse" and self.t_state >= 4.0:
            if self.air_bar < r.air_bar_min:
                self.state = "gas_fault"
            else:
                self.state, self.t_state, self.seg_left = "cut", 0.0, seg_m
                self.n_pierce += 1
                self.wear_v += self.wear_v_per_pierce
        elif self.state == "cut":
            self.seg_left -= r.cut_mm_min / 60000.0 * dt
            if self.air_bar < r.air_bar_min:
                self.state = "gas_fault"
            elif self.seg_left <= 0:
                if self.n_pierce >= self.pierces:
                    self.plates += 1
                    self.state, self.t_state = "load", 0.0
                else:
                    self.state, self.t_state = "traverse", 0.0
        arc = self.state == "cut"
        self.u_arc = r.u_arc + self.wear_v if arc else 0.0
        self.p_el = (r.i_arc * self.u_arc / r.eff if arc else r.idle_w) + r.fan_kw * 1000.0

    def s_va(self):
        return complex(self.p_el, _pf_q(self.p_el, self.r.pf))

    def tags(self):
        return {"arc_a": self.r.i_arc if self.state == "cut" else 0.0, "arc_v": self.u_arc,
                "power_kw": self.p_el / 1000.0, "air_bar": self.air_bar, "plates": self.plates,
                "state": self.state}


# --------------------------------------------------------------------------------- welding --
@dataclass(frozen=True)
class WelderRating:
    i_weld: float
    eff: float
    pf: float
    idle_w: float
    source: str = ""


class WeldingCell:
    def __init__(self, name, r, seams=8, seam_s=45.0, move_s=8.0, change_s=240.0, seed=0):
        import random
        self.name, self.r = name, r
        self.seams, self.seam_s, self.move_s, self.change_s = seams, seam_s, move_s, change_s
        self.rng = random.Random(f"weld:{name}:{seed}")
        self.state, self.t_state, self.seam = "change", 0.0, 0
        self.tip_wear = 0.0          # fault: 0 healthy .. 1 badly worn (current ripple)
        self.parts = 0
        self.i = self.u = self.p_el = 0.0

    def step(self, dt):
        self.t_state += dt
        if self.state == "change" and self.t_state >= self.change_s:
            self.state, self.t_state, self.seam = "weld", 0.0, 0
        elif self.state == "weld" and self.t_state >= self.seam_s:
            self.seam += 1
            self.state, self.t_state = ("move", 0.0) if self.seam < self.seams else ("change", 0.0)
            if self.seam >= self.seams:
                self.parts += 1
        elif self.state == "move" and self.t_state >= self.move_s:
            self.state, self.t_state = "weld", 0.0
        if self.state == "weld":
            ripple = 0.02 + 0.15 * self.tip_wear          # relative current ripple (project choice)
            self.i = max(0.0, self.r.i_weld * (1.0 + self.rng.gauss(0.0, ripple)))
            self.u = 14.0 + 0.05 * self.i                  # IEC 60974-1 conventional load voltage
            self.p_el = self.i * self.u / self.r.eff
        else:
            self.i, self.u, self.p_el = 0.0, 0.0, self.r.idle_w

    def s_va(self):
        return complex(self.p_el, _pf_q(self.p_el, self.r.pf))

    def tags(self):
        return {"weld_a": self.i, "weld_v": self.u, "power_kw": self.p_el / 1000.0,
                "seams": self.seam, "parts": self.parts, "state": self.state}


# ------------------------------------------------------------------------------- shot blast --
@dataclass(frozen=True)
class ShotBlastRating:
    turbines: int
    turbine_kw: float              # rated turbine motor power
    idle_frac: float               # turbine power with no abrasive (project choice)
    abrasive_kg_min: float         # rated abrasive flow per turbine at rated power (project choice)
    collector_m3h: float
    dp_clean_pa: float
    dp_clean_trigger_pa: float     # pulse cleaning starts at this filter pressure drop
    fan_dp_pa: float               # fan total pressure at the rated flow (project choice)
    fan_eta: float
    pf: float = 0.85
    source: str = ""


class ShotBlast:
    def __init__(self, name, r):
        self.name, self.r = name, r
        self.feed = [1.0] * r.turbines   # abrasive feed share per turbine (a jam sets 0)
        self.running = True
        self.dp_filter = r.dp_clean_pa
        self.clog = 0.0                  # fault: share of the filter that pulse cleaning cannot clear
        self.dust_pa_per_s = 0.2         # filter loading rate while blasting (project choice)
        self.p_el = 0.0
        self.flow_m3h = r.collector_m3h

    def step(self, dt):
        r = self.r
        if not self.running:
            self.p_el = 0.0
            return
        p_t = sum(r.turbine_kw * (r.idle_frac + (1.0 - r.idle_frac) * f) for f in self.feed)
        self.dp_filter += self.dust_pa_per_s * dt
        floor = r.dp_clean_pa + self.clog * (r.dp_clean_trigger_pa - r.dp_clean_pa) * 2.0
        if self.dp_filter >= r.dp_clean_trigger_pa:
            self.dp_filter = max(floor, r.dp_clean_pa)              # a pulse cleaning cycle
        # fan on a quadratic system: more filter drop, less flow (fan curve against system)
        extra = max(0.0, self.dp_filter - r.dp_clean_pa)
        self.flow_m3h = r.collector_m3h * math.sqrt(r.fan_dp_pa / (r.fan_dp_pa + extra))
        p_fan = self.flow_m3h / 3600.0 * (r.fan_dp_pa + extra) / r.fan_eta / 1000.0
        self.p_el = 1000.0 * (p_t + p_fan)

    def s_va(self):
        return complex(self.p_el, _pf_q(self.p_el, self.r.pf))

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "filter_dp_pa": self.dp_filter,
                "air_m3h": self.flow_m3h, "abrasive_kg_min": sum(self.feed) * self.r.abrasive_kg_min}


# ------------------------------------------------------------------------------ paint booth --
@dataclass(frozen=True)
class PaintRating:
    exhaust_kw: float              # all exhaust fan motors at the design point
    dp_clean_pa: float
    dp_change_pa: float            # the filters are changed at this drop
    fan_dp_pa: float               # fan total pressure at design (BHEL: 5 in. water gauge)
    oven_kw: float
    oven_setpoint_c: float
    oven_c_j_k: float              # oven and part heat capacity (project choice)
    oven_ua_w_k: float             # oven loss (project choice)
    pf: float = 0.85
    source: str = ""


class PaintBooth:
    def __init__(self, name, r, gas_supply, t_amb=35.0):
        self.name, self.r = name, r
        self.dp = r.dp_clean_pa
        self.spray_pa_per_h = 20.0       # overspray loading of the filters (project choice)
        br = BurnerRating(q_rated_kw=r.oven_kw, p_rated_kpa=15.0, eff=0.75, p_low_trip_kpa=7.5)
        self.burner = Burner(f"{name}-oven", br, gas_supply.r.lhv_mj_m3)
        gas_supply.burners.append(self.burner)
        self.t_oven, self.t_amb = t_amb, t_amb
        self.p_el = 0.0
        self.flow_share = 1.0

    def step(self, dt):
        r = self.r
        self.dp += self.spray_pa_per_h * dt / 3600.0
        if self.dp >= r.dp_change_pa:
            self.dp = r.dp_clean_pa                                 # the filters are changed
        extra = self.dp - r.dp_clean_pa
        self.flow_share = math.sqrt(r.fan_dp_pa / (r.fan_dp_pa + extra))
        self.p_el = 1000.0 * r.exhaust_kw * self.flow_share * (r.fan_dp_pa + extra) / r.fan_dp_pa
        e = r.oven_setpoint_c - self.t_oven
        self.burner.firing = 0.0 if e < 0.5 else min(1.0, 0.1 + 0.2 * e)
        q = self.burner.heat_w - r.oven_ua_w_k * (self.t_oven - self.t_amb)
        self.t_oven += q / r.oven_c_j_k * dt

    def s_va(self):
        return complex(self.p_el, _pf_q(self.p_el, self.r.pf))

    def tags(self):
        return {"exhaust_kw": self.p_el / 1000.0, "filter_dp_pa": self.dp, "air_share": self.flow_share,
                "oven_c": self.t_oven, "oven_gas_m3h": self.burner.flow_m3h}


# ------------------------------------------------------------------------------- qa-scanner --
@dataclass(frozen=True)
class ScannerRating:
    p_w: float
    holdup_s: float
    v_min_pu: float                # the supply works down to this share of nominal input
    reboot_s: float
    pf: float = 0.94
    source: str = ""


class QaScanner:
    def __init__(self, name, r, inspect_s=90.0):
        self.name, self.r = name, r
        self.inspect_s = inspect_s
        self.state, self.t_state = "run", 0.0
        self.below = 0.0
        self.parts = 0
        self.resets = 0
        self.p_el = r.p_w

    def step(self, dt, v_pu):
        r = self.r
        self.t_state += dt
        if v_pu < r.v_min_pu:
            self.below += dt
            if self.below > r.holdup_s and self.state == "run":
                self.state, self.t_state = "reboot", 0.0
                self.resets += 1
        else:
            self.below = 0.0
        if self.state == "reboot":
            self.p_el = 0.0 if v_pu < r.v_min_pu else 0.6 * r.p_w
            if self.t_state >= r.reboot_s and v_pu >= r.v_min_pu:
                self.state, self.t_state = "run", 0.0
        else:
            self.p_el = r.p_w
            if self.t_state >= self.inspect_s:
                self.parts += 1
                self.t_state = 0.0

    def s_va(self):
        return complex(self.p_el, _pf_q(self.p_el, self.r.pf))

    def tags(self):
        return {"power_w": self.p_el, "parts": self.parts, "resets": self.resets, "state": self.state}
