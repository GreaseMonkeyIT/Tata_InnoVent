"""The building services of the shop (sim overhaul, 2026-10-15): the loads that every real factory
has besides its machines. Without them the site load was too low for its transformers, and the
site exported power when the hot test fed back (ideas.md 17.5).

The site is one shop with two bays and an office block (ideas.md 17.1, operator 2026-10-15):

  fab bay       the fabrication and machining line, two EOT cranes, on TR-1
  assembly bay  the engine assembly line, on TR-2
  office block  plant offices, QA room and the control room, on TR-2

Loads, each with its own electrical power and power factor:

  Lighting      lighting power density x floor area, on a schedule. Allowances from the building
                area method (2021 IECC: manufacturing facility 0.82 W/ft2 = 8.8 W/m2, office
                0.64 W/ft2 = 6.9 W/m2). ECBC 2017 values were not found, so the IECC values stand in.
  Ventilation   roof extractors for general dilution: 8 air changes per hour in the welding bay
                (OHS: 8 to 10 in a welding area), 6 in the assembly bay (general ventilation 6 to 8).
                Sodeca HTTI-100-4T class: 7.5 kW, 56,700 m3/h maximum flow.
  Make-up air   evaporative air washers supply the air that the extractors take out. Fan power by
                P = Q dp / eta (145 Pa static from a 30,000 to 40,000 CFM air washer listing; fan
                efficiency 0.6: project choice) plus a 0.37 kW pump per unit (same listing). They run
                only on a hot day (ambient at or above 30 C: project choice).
  Fume          one central fume extractor per robotic welding cell (IperJet DF6 class: 4 kW,
                4,000 m3/h), running while its cell runs.
  EOT cranes    15 t cranes in the fab bay: a 37 kW hoist (a 15 t x 20 m crane calculation: 32.5 kW
                electrical at the rated load), 11 kW long travel and 5.5 kW cross travel motors (a BHEL
                28.5 m span crane specification). Each crane runs lift cycles (project choices below).
  Office        lighting and plug loads in office hours (plug 8 W/m2: ASHRAE AEDG medium office
                8.07 W/m2), and air conditioning: a design cooling load per floor area (150 to 200
                W/m2 is the Indian planning range, 175 used) at a part load that follows the outdoor
                air over the ISEER range of 24 to 43 C, divided by the system EER (3.5: project
                choice, no VRF value found). The control room and the server room stay cooled.

The power factors are project choices: LED lighting 0.95, fan motors 0.85, inverter air
conditioning 0.95, plug loads 0.90, crane drives 0.95 (VFDs with a diode front end).
"""
import math
import random
from dataclasses import dataclass


def _q(p, pf):
    return p * math.tan(math.acos(pf)) if 0.0 < pf < 1.0 else 0.0


class _Load:
    """Base: a real power p_el (W) and a power factor; the plant attaches it to a bus."""
    pf = 0.9

    def __init__(self, name):
        self.name = name
        self.p_el = 0.0

    def s_va(self):
        return complex(self.p_el, _q(self.p_el, self.pf))

    def tags(self):
        return {"power_kw": self.p_el / 1000.0}


def office_hours(t_day_h, start=9.0, end=18.0):
    return start <= t_day_h < end


# ------------------------------------------------------------------------------- lighting --
@dataclass(frozen=True)
class LightingRating:
    lpd_w_m2: float
    area_m2: float
    off_share: float = 0.0        # share left on outside its schedule (security lighting)
    source: str = ""


class Lighting(_Load):
    pf = 0.95

    def __init__(self, name, r, schedule=None):
        super().__init__(name)
        self.r = r
        self.schedule = schedule      # None: always on (a three-shift shop)

    def step(self, dt, t_day_h):
        on = self.schedule is None or self.schedule(t_day_h)
        self.p_el = self.r.lpd_w_m2 * self.r.area_m2 * (1.0 if on else self.r.off_share)


# ----------------------------------------------------------------------------- ventilation --
@dataclass(frozen=True)
class FanRating:
    motor_kw: float
    q_max_m3h: float
    source: str = ""


class RoofExtractors(_Load):
    """n roof fans that give the bay its air change rate. Each fan runs near its free-air point, so
    its motor runs near its rating (0.9 of it: project choice)."""
    pf = 0.85

    def __init__(self, name, fan, volume_m3, ach, q_per_fan_share=0.8):
        super().__init__(name)
        self.fan = fan
        self.q_need_m3h = volume_m3 * ach
        q_fan = fan.q_max_m3h * q_per_fan_share          # project choice: 80 % of the free-air flow
        self.n = max(1, math.ceil(self.q_need_m3h / q_fan))
        self.q_m3h = self.n * q_fan
        self.running = True
        self.failed = 0               # fault: fans out of service

    def step(self, dt, t_day_h):
        n_on = max(0, self.n - self.failed) if self.running else 0
        self.p_el = n_on * self.fan.motor_kw * 1000.0 * 0.9

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "fans_on": max(0, self.n - self.failed) if self.running else 0,
                "air_m3h": self.q_m3h * (max(0, self.n - self.failed) / self.n if self.running else 0.0)}


@dataclass(frozen=True)
class AirWasherRating:
    q_unit_m3h: float
    static_pa: float
    fan_eta: float
    pump_kw: float
    t_on_c: float                 # runs at or above this outdoor temperature
    source: str = ""


class AirWashers(_Load):
    """Evaporative air washers that supply the make-up air of the extractors."""
    pf = 0.85

    def __init__(self, name, r, q_m3h):
        super().__init__(name)
        self.r = r
        self.n = max(1, math.ceil(q_m3h / r.q_unit_m3h))
        self.q_m3h = q_m3h
        self.on = False
        self.pad_fouling = 0.0        # fault: extra pressure share on the pads (scale, dust)

    def step(self, dt, t_day_h, t_amb_c):
        # thermostat with 1 K hysteresis (project choice)
        if t_amb_c >= self.r.t_on_c:
            self.on = True
        elif t_amb_c < self.r.t_on_c - 1.0:
            self.on = False
        if not self.on:
            self.p_el = 0.0
            return
        dp = self.r.static_pa * (1.0 + self.pad_fouling)
        fan_w = self.q_m3h / 3600.0 * dp / self.r.fan_eta
        self.p_el = fan_w + self.n * self.r.pump_kw * 1000.0

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "on": float(self.on), "units": self.n}


class FumeExtractor(_Load):
    """A central fume extractor that runs while its welding cell runs (its seams and moves)."""
    pf = 0.85

    def __init__(self, name, fan, cell):
        super().__init__(name)
        self.fan, self.cell = fan, cell
        self.filter_clog = 0.0        # fault: a clogged filter (less air at the same power)

    def step(self, dt, t_day_h):
        self.p_el = self.fan.motor_kw * 1000.0 * 0.9 if self.cell.p_el > 100.0 else 0.0

    def tags(self):
        q = self.fan.q_max_m3h * (1.0 - 0.5 * self.filter_clog) if self.p_el > 0 else 0.0
        return {"power_kw": self.p_el / 1000.0, "air_m3h": q}


# --------------------------------------------------------------------------------- cranes --
@dataclass(frozen=True)
class CraneRating:
    swl_t: float                  # safe working load
    hook_t: float                 # hook block and ropes (project choice)
    hoist_kw_el_rated: float      # hoist electrical input at the rated load
    lt_kw: float                  # long travel motor
    ct_kw: float                  # cross travel motor
    source: str = ""


class EotCrane(_Load):
    """An EOT crane doing lift cycles: hoist up, cross and long travel, lower, return. The hoist
    power scales with the lifted mass; lowering on a VFD with a braking resistor draws little
    (0.1 of the rated hoist input: project choice); the travel motors run at half their rating
    (rolling resistance and acceleration: project choice). Cycle: every 6 min with a random lift of
    1 to 4 t (booms, plates, fixtures), hoist 30 s, travel 40 s, lower 30 s, return 40 s (project
    choices)."""
    pf = 0.95
    PHASES = (("hoist", 30.0), ("travel", 40.0), ("lower", 30.0), ("return", 40.0))

    def __init__(self, name, r, period_s=360.0, offset_s=0.0, seed=0):
        super().__init__(name)
        self.r = r
        self.period_s, self.offset_s = period_s, offset_s
        self.rng = random.Random(f"crane:{name}:{seed}")
        self.lift_t = 0.0
        self.phase = "idle"
        self.t = 0.0
        self._cycle = -1
        self.lifts = 0
        self.hoist_drag = 0.0         # fault: hoist gearbox or brake drag (share of rated input)

    def step(self, dt, t_day_h):
        self.t += dt
        x = self.t - self.offset_s
        if x < 0.0:
            self.phase, self.p_el = "idle", 0.0
            return
        k, ph = int(x // self.period_s), x % self.period_s
        if k != self._cycle:
            self._cycle = k
            self.lift_t = self.rng.uniform(1.0, 4.0)
            self.lifts += 1
        r = self.r
        self.phase = "idle"
        for name, d in self.PHASES:
            if ph < d:
                self.phase = name
                break
            ph -= d
        share = (self.lift_t + r.hook_t) / (r.swl_t + r.hook_t)
        if self.phase == "hoist":
            p = r.hoist_kw_el_rated * (share + self.hoist_drag)
        elif self.phase == "lower":
            p = r.hoist_kw_el_rated * (0.1 + self.hoist_drag)
        elif self.phase in ("travel", "return"):
            p = 0.5 * (r.lt_kw + r.ct_kw)
        else:
            p = 0.0
        self.p_el = p * 1000.0

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "lift_t": self.lift_t if self.phase in ("hoist", "travel") else 0.0,
                "lifts": self.lifts}


# --------------------------------------------------------------------------------- office --
@dataclass(frozen=True)
class OfficeRating:
    area_m2: float
    cooling_w_m2: float           # design cooling load per floor area
    eer: float                    # air conditioning system EER, W/W
    plug_w_m2: float
    plug_off_share: float         # plug load left on outside office hours (IT, standby)
    always_cooled_kw: float       # control room and server room, cooling kW, always on
    t_part_lo_c: float = 24.0     # part load from the ISEER outdoor range
    t_part_hi_c: float = 43.0
    source: str = ""


class OfficeAircon(_Load):
    pf = 0.95

    def __init__(self, name, r):
        super().__init__(name)
        self.r = r
        self.cooling_w = 0.0
        self.eer_loss = 0.0           # fault: dirty condenser or low refrigerant (share of EER lost)

    def step(self, dt, t_day_h, t_amb_c):
        r = self.r
        plr = min(1.0, max(0.2, (t_amb_c - r.t_part_lo_c) / (r.t_part_hi_c - r.t_part_lo_c)))
        occ = r.area_m2 * r.cooling_w_m2 * plr if office_hours(t_day_h) else 0.0
        self.cooling_w = occ + r.always_cooled_kw * 1000.0 * max(plr, 0.5)
        self.p_el = self.cooling_w / (r.eer * (1.0 - self.eer_loss))

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "cooling_kw": self.cooling_w / 1000.0}


class PlugLoad(_Load):
    pf = 0.90

    def __init__(self, name, r):
        super().__init__(name)
        self.r = r

    def step(self, dt, t_day_h):
        r = self.r
        self.p_el = r.plug_w_m2 * r.area_m2 * (1.0 if office_hours(t_day_h) else r.plug_off_share)
