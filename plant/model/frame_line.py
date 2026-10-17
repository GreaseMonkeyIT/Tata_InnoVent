"""Truck frame stations (ideas.md 11.5, the frame line, 2026-10-15): the side member piercing line,
the cathodic electrodeposition (CED) paint line and the frame riveting station.

The product is a riveted heavy-truck ladder frame, as made in a frame factory such as Tata Motors
Jamshedpur (its plant profile lists a 5,000 t press, CNC punching and CED painting). The order of
work follows the frame maker Press Kogyo: the side member plate is cut and pierced (NC piercing),
formed into a U channel on a 5,000 t press (press_brake.PressBrake as lmp-1), painted as parts
(medium and heavy trucks), and the side and cross members are joined into a ladder mainly with
rivets and bolts.

PunchLine (pierce-1): a CNC hydraulic punch for side members. Each hole is one hit: the axes move the
  member to the hole, then the punch shears it. Punch force F = pi d t tau with the shear strength
  tau = 0.8 UTS (the rule press-2 also uses). A worn punch needs more force (the edge rounds): the
  force rises with the punch wear, and the punch is changed at its life (F4: a faster wear rate).
  The hydraulic unit is a fixed pump on an induction motor: pressure F / A while the punch shears,
  circulation pressure otherwise. Hörmann Automotive: up to 800 holes per member, and punching is
  the bottleneck of the frame line.
CedLine (ced-1): a single-hoist dip line: degrease, rinse, zinc phosphate, rinse, CED, ultrafiltrate
  rinse, then the cure oven. The heated tanks fire gas burners (gas.py, F10 path); the CED bath is
  held near 30 C by chiller-1 (the bath takes the rectifier and pump heat); the rectifier deposits a
  film of a set thickness: the charge is film mass / coulomb yield, and the current falls as the film
  insulates the part (exponential, three time constants in the deposition time). The cure oven is
  gas-fired and takes the heat of each load.
RivetStation (rivet-1): the ladder is joined fastener by fastener: an operator places the tool
  (handling, plant flow), the hydraulic unit swages the fastener. Hydac mobile riveting systems for
  truck frames reach 500 kN. Each fastener gives a record: its install force.
"""
import math
import random
from dataclasses import dataclass

from .gas import Burner, BurnerRating
from .motor import InductionMotor

STEEL_CP = 460.0           # J/kg K, carbon steel


# ------------------------------------------------------------------------------- piercing --
@dataclass(frozen=True)
class SideMember:
    uts_mpa: float
    t_mm: float
    length_m: float
    web_mm: float
    flange_mm: float
    hole_mm: float
    holes_min: int
    holes_max: int
    source: str = ""

    @property
    def mass_kg(self):
        return (self.web_mm + 2.0 * self.flange_mm) * self.t_mm * 1e-6 * self.length_m * 7850.0

    @property
    def area_m2(self):
        """Painted surface: both faces of the developed section."""
        return 2.0 * (self.web_mm + 2.0 * self.flange_mm) / 1000.0 * self.length_m


@dataclass(frozen=True)
class PunchRating:
    force_kn: float            # punching head capacity
    p_max_bar: float           # system pressure at full force (sets the cylinder area)
    stroke_mm: float           # cylinder travel per hit
    stroke_s: float            # time of the working stroke
    hit_s: float               # one hit: positioning plus the stroke
    p_circ_bar: float          # circulation pressure between strokes
    eta_hm: float              # pump hydraulic-mechanical efficiency
    axes_kw: float             # carriage and gauge drives while positioning
    life_hits: int             # punch change interval
    wear_force_gain: float     # extra force share of a punch at the end of its life
    change_s: float            # punch change time
    source: str = ""


class PunchLine:
    IDLE, PIERCE, CHANGE = "idle", "pierce", "change"

    def __init__(self, name, r, motor_rating, member, seed=0):
        self.name, self.r, self.m = name, r, member
        self.motor = InductionMotor(f"{name}-motor", motor_rating)
        self.w_n = motor_rating.n_rpm * math.pi / 30.0
        self.motor.w = self.w_n
        self.area = r.force_kn * 1e3 / (r.p_max_bar * 1e5)
        q = self.area * r.stroke_mm / 1000.0 / r.stroke_s
        self.disp_m3 = q / (self.w_n / (2.0 * math.pi))
        self.rng = random.Random(f"punch:{name}:{seed}")
        self.state, self.t_state = self.IDLE, 0.0
        self.holes_left = 0
        self.hits = 0                    # hits on the present punch
        self.wear_per_hit = 1.0 / r.life_hits
        self.wear_rate = 1.0             # F4: a multiplier on the wear per hit (1 = healthy)
        self.wear = 0.0                  # 0 new .. 1 at its life
        self.holes = self.members = self.punches = 0
        self.p_bar = r.p_circ_bar
        self.last_force_kn = 0.0
        self.uts_mpa = member.uts_mpa
        self.p_el_axes = 0.0

    def begin(self, holes, uts_mpa=None):
        """Load a member with `holes` holes (the plant flow calls this after the handling)."""
        if self.state == self.IDLE:
            self.holes_left, self.state, self.t_state = holes, self.PIERCE, 0.0
            if uts_mpa is not None:
                self.uts_mpa = uts_mpa

    @property
    def busy(self):
        return self.state != self.IDLE

    def force_kn(self):
        tau = 0.8 * self.uts_mpa
        return math.pi * self.m.hole_mm * self.m.t_mm * tau / 1000.0 * (1.0 + self.r.wear_force_gain * self.wear)

    def step(self, dt, v_ll, f_hz):
        r = self.r
        self.t_state += dt
        p = r.p_circ_bar
        self.p_el_axes = 0.0
        if self.state == self.PIERCE:
            ph = self.t_state % r.hit_s
            positioning = ph < r.hit_s - r.stroke_s
            if positioning:
                self.p_el_axes = r.axes_kw * 1000.0
            else:
                f = self.force_kn()
                p = min(r.p_max_bar, f * 1e3 / self.area / 1e5)
            if ph < dt and self.t_state > dt:              # one hit done
                self.holes_left -= 1
                self.holes += 1
                self.hits += 1
                self.last_force_kn = self.force_kn()
                self.wear = min(1.5, self.wear + self.wear_per_hit * self.wear_rate)
                if self.hits >= r.life_hits or self.wear >= 1.0:
                    self.state, self.t_state = self.CHANGE, 0.0
                elif self.holes_left <= 0:
                    self.members += 1
                    self.state, self.t_state = self.IDLE, 0.0
        elif self.state == self.CHANGE:
            if self.t_state >= r.change_s:
                self.hits, self.wear, self.punches = 0, 0.0, self.punches + 1
                if self.holes_left > 0:
                    self.state, self.t_state = self.PIERCE, 0.0
                else:
                    self.members += 1
                    self.state, self.t_state = self.IDLE, 0.0
        self.p_bar = p
        t_load = (self.disp_m3 / (2.0 * math.pi)) * p * 1e5 / r.eta_hm
        self.motor.step(dt, v_ll, f_hz, lambda w, t=t_load: t * min(1.0, max(w, 0.0) / (0.05 * self.w_n)))

    def s_va(self):
        return complex(self.motor.p_in + self.p_el_axes, self.motor.q_in + 0.75 * self.p_el_axes)

    def tags(self):
        return {"current_a": self.motor.amps, "power_kw": (self.motor.p_in + self.p_el_axes) / 1000.0,
                "pressure_bar": self.p_bar, "last_force_kn": self.last_force_kn, "holes": self.holes,
                "members": self.members, "punch_hits": self.hits, "state": self.state}

    def check(self):
        out = self.motor.check()
        if self.p_bar > self.r.p_max_bar + 1e-6:
            out.append(f"{self.name}: pressure above the system maximum")
        return out


# ------------------------------------------------------------------------------------ CED --
@dataclass(frozen=True)
class CedRating:
    degrease_c: float
    phosphate_c: float
    tank_m3: float               # each heated pretreatment tank
    tank_ua_w_k: float           # loss of a heated tank
    tank_burner_kw: float
    tank_band_k: float           # burner thermostat band
    bath_c: float                # CED bath setpoint (the chiller holds the circulation at this)
    bath_m3: float
    bath_circ_kgs: float         # bath circulation through the chiller heat exchanger
    pumps_kw: float              # circulation and ultrafiltration pumps, always on
    volts: float
    deposition_s: float
    ramp_s: float                # soft-start voltage ramp
    film_um: float
    film_density: float          # g/cm3 of the cured film
    yield_mg_c: float            # coulomb yield
    rect_eta: float
    stage_s: tuple               # degrease, rinse, phosphate, rinse, (CED), UF rinse
    transfer_s: float            # hoist move between tanks
    oven_c: float                # cure oven air setpoint
    oven_kw: float
    oven_ua_w_k: float
    oven_c_j_k: float
    cure_s: float                # time in the oven per load
    oven_fans_kw: float
    pf: float = 0.88
    source: str = ""


class _Tank:
    def __init__(self, name, t_set, m3, ua, burner):
        self.name, self.t_set, self.ua, self.burner = name, t_set, ua, burner
        self.c = m3 * 1000.0 * 4186.0
        self.t = t_set
        self.on = False

    def step(self, dt, t_shop, part_w, band):
        if self.t <= self.t_set - band:
            self.on = True
        elif self.t >= self.t_set:
            self.on = False
        self.burner.firing = 1.0 if self.on else 0.0
        q = self.burner.heat_w - self.ua * (self.t - t_shop) - part_w
        self.t += q / self.c * dt


class CedLine:
    WAIT = "wait"

    def __init__(self, name, r, gas_supply, chiller, t_shop=30.0):
        self.name, self.r = name, r
        lhv = gas_supply.r.lhv_mj_m3
        br = BurnerRating(q_rated_kw=r.tank_burner_kw, p_rated_kpa=15.0, eff=0.80, p_low_trip_kpa=7.5)
        self.tanks = [_Tank("degrease", r.degrease_c, r.tank_m3, r.tank_ua_w_k, Burner(f"{name}-degrease", br, lhv)),
                      _Tank("phosphate", r.phosphate_c, r.tank_m3, r.tank_ua_w_k, Burner(f"{name}-phosphate", br, lhv))]
        bo = BurnerRating(q_rated_kw=r.oven_kw, p_rated_kpa=15.0, eff=0.75, p_low_trip_kpa=7.5)
        self.oven_burner = Burner(f"{name}-oven", bo, lhv)
        gas_supply.burners.extend([t.burner for t in self.tanks] + [self.oven_burner])
        self.chiller = chiller
        self.t_shop = t_shop
        self.t_bath = r.bath_c
        self.c_bath = r.bath_m3 * 1000.0 * 4186.0
        self.t_oven = r.oven_c
        # the wet section: one load at a time (a single hoist); stages in order
        self.load = None                 # dict: kg, area, stage, t
        self.stages = ["degrease", "rinse1", "phosphate", "rinse2", "ced", "uf"]
        self.oven = []                   # loads curing: [kg, t_left, heat_left_s]
        self.i_dc = self.v_dc = 0.0
        self.q_chill = 0.0
        self.p_el = 0.0
        self.loads = 0
        self.charge_c = 0.0

    def begin(self, part_kg, area_m2):
        if self.load is None:
            self.load = {"kg": part_kg, "area": area_m2, "i": 0, "t": 0.0, "moving": True,
                         "t_part": self.t_shop}
            return True
        return False

    @property
    def busy(self):
        return self.load is not None

    def _stage_s(self, name):
        r = self.r
        if name == "ced":
            return r.ramp_s + r.deposition_s
        idx = {"degrease": 0, "rinse1": 1, "phosphate": 2, "rinse2": 3, "uf": 4}[name]
        return r.stage_s[idx]

    def step(self, dt):
        r = self.r
        part_w = {"degrease": 0.0, "phosphate": 0.0}
        self.i_dc = self.v_dc = 0.0
        ld = self.load
        if ld is not None:
            ld["t"] += dt
            name = self.stages[ld["i"]]
            if ld["moving"]:
                if ld["t"] >= r.transfer_s:
                    ld["moving"], ld["t"] = False, 0.0
                    if name == "ced":
                        q = ld["area"] * 1e4 * r.film_um * 1e-4 * r.film_density * 1000.0 / r.yield_mg_c
                        ld["q_total"], ld["tau"] = q, r.deposition_s / 3.0
            else:
                if name in part_w:
                    tank = self.tanks[0 if name == "degrease" else 1]
                    # the part comes up to the tank temperature in about a third of the stage
                    k = 3.0 / self._stage_s(name)
                    part_w[name] = ld["kg"] * STEEL_CP * k * max(0.0, tank.t - ld["t_part"])
                    ld["t_part"] += k * (tank.t - ld["t_part"]) * dt
                elif name == "ced":
                    t = ld["t"]
                    self.v_dc = r.volts * min(1.0, t / r.ramp_s)
                    if t >= r.ramp_s:
                        x = t - r.ramp_s
                        self.i_dc = ld["q_total"] / ld["tau"] * math.exp(-x / ld["tau"])
                    else:
                        self.i_dc = 0.0
                    self.charge_c += self.i_dc * dt
                if ld["t"] >= self._stage_s(name):
                    ld["i"] += 1
                    ld["t"], ld["moving"] = 0.0, True
                    if ld["i"] >= len(self.stages):
                        # into the oven: the load heats to the cure temperature over the first half
                        self.oven.append([ld["kg"], r.cure_s, 0.5 * r.cure_s])
                        self.load = None
        for t in self.tanks:
            t.step(dt, self.t_shop, part_w[t.name], r.tank_band_k)
        # CED bath: the rectifier power becomes heat in the bath, the pumps too; chiller-1 cools the
        # circulation (its tempering valve holds the stream at its setpoint)
        p_dc = self.v_dc * self.i_dc
        q_in = p_dc + r.pumps_kw * 1000.0 * 0.5
        self.q_chill = self.chiller.remove(self.t_bath, r.bath_circ_kgs, dt) if self.chiller else 0.0
        self.t_bath += (q_in - self.q_chill + 0.0) / self.c_bath * dt
        # cure oven
        part_load = 0.0
        for o in self.oven:
            o[1] -= dt
            if o[2] > 0.0:
                part_load += o[0] * STEEL_CP * (180.0 - self.t_shop) / (0.5 * r.cure_s)
                o[2] -= dt
        done = [o for o in self.oven if o[1] <= 0.0]
        if done:
            self.loads += len(done)
            self.oven = [o for o in self.oven if o[1] > 0.0]
        e = r.oven_c - self.t_oven
        self.oven_burner.firing = 0.0 if e < 0.5 else min(1.0, 0.1 + 0.2 * e)
        q = self.oven_burner.heat_w - r.oven_ua_w_k * (self.t_oven - self.t_shop) - part_load
        self.t_oven += q / r.oven_c_j_k * dt
        self.p_el = p_dc / r.rect_eta + r.pumps_kw * 1000.0 + r.oven_fans_kw * 1000.0

    def s_va(self):
        return complex(self.p_el, self.p_el * math.tan(math.acos(self.r.pf)))

    def tags(self):
        return {"degrease_c": self.tanks[0].t, "phosphate_c": self.tanks[1].t, "bath_c": self.t_bath,
                "rect_v": self.v_dc, "rect_a": self.i_dc, "oven_c": self.t_oven,
                "oven_gas_m3h": self.oven_burner.flow_m3h,
                "tank_gas_m3h": sum(t.burner.flow_m3h for t in self.tanks),
                "power_kw": self.p_el / 1000.0, "chiller_kw": self.q_chill / 1000.0, "loads": self.loads,
                "stage": self.stages[self.load["i"]] if self.load else "idle"}

    def check(self):
        out = []
        if not 5.0 < self.t_bath < 45.0:
            out.append(f"{self.name}: CED bath outside 5 to 45 C ({self.t_bath:.1f})")
        if self.i_dc < 0 or self.p_el < 0:
            out.append(f"{self.name}: negative current or power")
        return out


# ------------------------------------------------------------------------------- riveting --
@dataclass(frozen=True)
class RivetRating:
    fasteners: int               # per frame
    tools: int                   # tools (and operators) working on one frame at once
    install_kn: float            # nominal swage force
    sigma_frac: float            # 1 sigma of the install force share
    swage_s: float
    hpu_idle_kw: float
    hpu_peak_kw: float
    pf: float = 0.85
    source: str = ""


class RivetStation:
    def __init__(self, name, r, seed=0):
        self.name, self.r = name, r
        self.rng = random.Random(f"rivet:{name}:{seed}")
        self.left = 0                    # fasteners not yet started
        self.swage_left = [0.0] * r.tools
        self.open = 0                    # fasteners in a tool now
        self.frames = self.fasteners = 0
        self.last_kn = 0.0
        self.p_el = r.hpu_idle_kw * 1000.0

    def begin(self):
        self.left = self.r.fasteners

    @property
    def busy(self):
        return self.left > 0 or self.open > 0

    def swage(self, tool):
        """One fastener on `tool` (the plant flow calls this after the operator places the tool)."""
        if self.left > 0 and self.swage_left[tool] <= 0.0:
            self.swage_left[tool] = self.r.swage_s
            self.left -= 1
            self.open += 1

    def tool_free(self, tool):
        return self.swage_left[tool] <= 0.0

    def step(self, dt):
        r = self.r
        running = 0
        for k in range(r.tools):
            if self.swage_left[k] > 0.0:
                running += 1
                self.swage_left[k] -= dt
                if self.swage_left[k] <= 0.0:
                    self.swage_left[k] = 0.0
                    self.last_kn = r.install_kn * (1.0 + self.rng.gauss(0.0, r.sigma_frac))
                    self.fasteners += 1
                    self.open -= 1
                    if self.left == 0 and self.open == 0:
                        self.frames += 1
        self.p_el = (r.hpu_idle_kw + running * (r.hpu_peak_kw - r.hpu_idle_kw)) * 1000.0

    def s_va(self):
        return complex(self.p_el, self.p_el * math.tan(math.acos(self.r.pf)))

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "last_install_kn": self.last_kn, "fasteners": self.fasteners,
                "frames": self.frames}
