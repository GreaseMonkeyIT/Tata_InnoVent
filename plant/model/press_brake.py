"""press-1: a CNC hydraulic press brake (ideas.md 11.5): an LVD PPEB 320/40 class machine bending
excavator boom plate.

Parts: the main motor (motor.py) at constant speed, a fixed-displacement pump, the directional and
proportional valves, a relief valve, two cylinders on the ram, the oil tank, and a water-cooled oil
cooler on cool-1 (heat.py).

Cycle (the PLC starts each bend): IDLE -> APPROACH (fast down by gravity with the prefill valves
open, the pump circulates to tank) -> BEND (pump flow into the cylinders: speed = Q_eff / A, pressure
from the bending force) -> HOLD (dwell at pressure, the pump flow goes over the relief) -> DECOMPRESS
-> RETURN (pump flow under the annulus, pressure from the ram weight and friction) -> IDLE while the
operator moves the plate (part handling).

Physics:
  - Pump: Q_th = displacement x speed. Internal leakage grows with pressure and with thinner oil:
    Q_leak = c_leak x p / nu(T) (laminar leakage, Hagen-Poiseuille), so a hot press bends slower and
    makes more heat. Shaft power = Q_th p / eta_hm + the circulation loss at idle.
  - Oil viscosity: ISO VG 46 by the ASTM D341 (Walther) relation through 46 cSt at 40 C and 6.8 cSt at
    100 C (viscosity index about 100).
  - Bending force (air bending): F = 1.42 x UTS x t^2 x L / V (Durma tonnage rule, the common form);
    the force rises over the first part of the penetration and holds at its peak (project choice
    for the shape).
  - Heat: every loss of the hydraulic circuit (pump losses, throttling, the relief) goes into the
    oil; the useful bending work goes into the plate. The oil and the tank form one thermal mass.
  - A thermostatic water valve on the oil cooler opens above 45 C (project choice), so the oil runs
    inside the 35 to 60 C range that press brake makers give.
  - The oil temperature interlock trips the press at 70 C (Durma: the oil should not exceed 70 C).
Faults (parameters only): ram guide friction (F1), pump wear: a larger leakage coefficient (F2),
a harder or thicker plate (F15, a control case: no machine fault), oil cooler fouling.
"""
import math
from dataclasses import dataclass

from .heat import CP_OIL, CP_WATER, HeatExchanger
from .motor import InductionMotor

G = 9.81


def nu_vg46(t_c):
    """Kinematic viscosity of ISO VG 46 oil, cSt, by ASTM D341: log log (nu + 0.7) = A - B log T."""
    t1, n1, t2, n2 = 313.15, 46.0, 373.15, 6.8
    y1, y2 = math.log10(math.log10(n1 + 0.7)), math.log10(math.log10(n2 + 0.7))
    b = (y1 - y2) / (math.log10(t2) - math.log10(t1))
    a = y1 + b * math.log10(t1)
    return 10 ** (10 ** (a - b * math.log10(t_c + 273.15))) - 0.7


@dataclass(frozen=True)
class PressBrakeRating:
    force_kn: float            # nominal press force
    p_max_bar: float           # maximum system pressure
    v_approach: float          # mm/s
    v_bend: float              # mm/s
    v_return: float            # mm/s
    stroke_mm: float
    tank_l: float
    ram_mass_kg: float         # ram and upper tools (project choice unless given)
    annulus_frac: float        # return-side area / bore area (project choice)
    eta_v_rated: float = 0.95  # pump volumetric efficiency at max pressure and 40 C oil (project choice)
    eta_hm: float = 0.90       # pump hydraulic-mechanical efficiency (project choice)
    p_circ_bar: float = 8.0    # circulation pressure at idle (project choice)
    p_relief_margin_bar: float = 10.0
    hold_s: float = 0.6
    decompress_s: float = 0.3
    trip_oil_c: float = 70.0
    oil_cooler_kw: float = 15.0   # cooler duty at 60 C oil and 27 C water (project choice)
    water_kgs: float = 0.6
    cooler_on_c: float = 45.0     # thermostatic water valve opens above this oil temperature (project choice)
    source: str = ""

    @property
    def area_m2(self):
        return self.force_kn * 1e3 / (self.p_max_bar * 1e5)


@dataclass
class Plate:
    uts_mpa: float = 510.0     # S355J2: UTS 470 to 630 MPa (British Steel S355J2 sheet); mid value
    t_mm: float = 12.0
    length_m: float = 2.5
    v_mm: float = 120.0        # V-die opening 10 t for 9 to 12 mm plate (MetalForming chart)

    def force_kn(self):
        return 1.42 * self.uts_mpa * self.t_mm ** 2 * self.length_m / self.v_mm

    def depth_mm(self):
        return 0.5 * self.v_mm * 0.9   # about a 90 degree air bend (project choice)


class PressBrake:
    IDLE, APPROACH, BEND, HOLD, DECOMPRESS, RETURN, TRIPPED = (
        "idle", "approach", "bend", "hold", "decompress", "return", "tripped")

    def __init__(self, name, rating, motor_rating, handling_s=25.0, t_oil0=40.0):
        self.name, self.r = name, rating
        self.motor = InductionMotor(f"{name}-motor", motor_rating)
        self.w_n = motor_rating.n_rpm * math.pi / 30.0
        r = rating
        # Pump displacement: the bend speed at full flow with the rated volumetric efficiency.
        q_need = r.v_bend / 1000.0 * r.area_m2
        self.disp_m3 = q_need / (self.w_n / (2 * math.pi)) / r.eta_v_rated
        # Leakage coefficient: Q_leak = c p / nu gives the rated volumetric efficiency at p_max, 40 C.
        q_th_n = self.disp_m3 * self.w_n / (2 * math.pi)
        self.c_leak = (1.0 - r.eta_v_rated) * q_th_n * nu_vg46(40.0) / (r.p_max_bar * 1e5)
        self.leak_factor = 1.0          # F2: pump wear multiplies the leakage
        self.friction_n = 0.02 * r.force_kn * 1e3   # ram guide friction, 2 % of force (project choice)
        self.friction_extra_n = 0.0     # F1
        self.plate = Plate()
        self.handling_s = handling_s
        self.phase = self.IDLE
        self.t_phase = 0.0
        self.x_mm = 0.0                 # ram travel from the top
        self.contact_mm = 0.0           # travel where the punch meets the plate
        self.penetration = 0.0
        self.p_bar = r.p_circ_bar
        self.q_relief = 0.0
        self.t_oil = t_oil0
        c_tank = r.tank_l * 0.87 * CP_OIL + 0.4 * r.tank_l * 7.85 * 460.0 * 0.1   # oil + tank steel (project choice)
        self.c_oil = c_tank
        self.cooler = HeatExchanger(f"{name}-oil-cooler", r.oil_cooler_kw * 1e3 / (CP_OIL * 10.0),
                                    CP_OIL, 60.0, 50.0, r.water_kgs, CP_WATER, 27.0)
        self.q_water = 0.0
        self.valve_open = False
        self.heat_w = 0.0
        self.bends = 0
        self.auto = True                # the PLC runs the cycle
        self.work_j = 0.0               # bending work into plates

    def nu(self):
        return nu_vg46(self.t_oil)

    def q_eff(self, p_pa, speed_frac):
        q_th = self.disp_m3 * speed_frac * self.w_n / (2 * math.pi)
        leak = self.c_leak * self.leak_factor * p_pa / self.nu()
        return max(0.0, q_th - leak), q_th

    def bend_force_n(self):
        f_peak = self.plate.force_kn() * 1e3
        ramp = min(1.0, self.penetration / max(self.plate.t_mm, 1e-3))     # rises over about t
        return f_peak * ramp

    def step(self, dt, v_ll, f_hz, t_water_in, m_water):
        r = self.r
        self.t_phase += dt
        speed = max(0.0, self.motor.w / self.w_n)
        a = r.area_m2
        fr = self.friction_n + self.friction_extra_n
        p_pa = r.p_circ_bar * 1e5
        q_to_cyl = 0.0
        self.q_relief = 0.0
        useful = 0.0
        q_eff, q_th = self.q_eff(r.p_circ_bar * 1e5, speed)
        # ---- the cycle, driven by the PLC
        if self.phase == self.TRIPPED:
            pass
        elif self.phase == self.IDLE:
            if self.auto and self.t_phase >= self.handling_s and speed > 0.9:
                self._go(self.APPROACH)
                self.contact_mm = r.stroke_mm - 60.0          # plate 60 mm above bottom (project choice)
        elif self.phase == self.APPROACH:
            self.x_mm += r.v_approach * dt
            if self.x_mm >= self.contact_mm:
                self.x_mm = self.contact_mm
                self.penetration = 0.0
                self._go(self.BEND)
        elif self.phase == self.BEND:
            f = self.bend_force_n() + fr - self.r.ram_mass_kg * G
            p_pa = max(r.p_circ_bar * 1e5, f / a)
            p_relief = (r.p_max_bar + r.p_relief_margin_bar) * 1e5
            q_eff, q_th = self.q_eff(min(p_pa, p_relief), speed)
            v = min(q_eff / a, r.v_bend / 1000.0)               # the proportional valve limits speed
            if p_pa >= p_relief:
                v, p_pa = 0.0, p_relief                         # stalled: all flow over the relief
            q_to_cyl = v * a
            self.q_relief = max(0.0, q_eff - q_to_cyl)
            dx = v * 1000.0 * dt
            self.x_mm += dx
            self.penetration += dx
            useful = self.bend_force_n() * v
            self.work_j += useful * dt
            if self.penetration >= self.plate.depth_mm():
                self._go(self.HOLD)
        elif self.phase == self.HOLD:
            f = self.plate.force_kn() * 1e3 + fr - self.r.ram_mass_kg * G
            p_pa = f / a
            q_eff, q_th = self.q_eff(p_pa, speed)
            self.q_relief = q_eff                               # the dwell: flow over the relief
            if self.t_phase >= r.hold_s:
                self._go(self.DECOMPRESS)
        elif self.phase == self.DECOMPRESS:
            p_pa = r.p_circ_bar * 1e5
            if self.t_phase >= r.decompress_s:
                self._go(self.RETURN)
                self.bends += 1
        elif self.phase == self.RETURN:
            a_ret = a * r.annulus_frac
            p_pa = max(r.p_circ_bar * 1e5, (self.r.ram_mass_kg * G + fr) / a_ret)
            q_eff, q_th = self.q_eff(p_pa, speed)
            v = min(q_eff / a_ret, r.v_return / 1000.0)
            q_to_cyl = v * a_ret
            self.q_relief = max(0.0, q_eff - q_to_cyl) if v >= r.v_return / 1000.0 else 0.0
            self.x_mm -= v * 1000.0 * dt
            if self.x_mm <= 0.0:
                self.x_mm = 0.0
                self._go(self.IDLE)
        self.p_bar = p_pa / 1e5
        # ---- pump shaft power -> motor load (constant torque at a pressure for a fixed pump)
        p_shaft = q_th * p_pa / r.eta_hm if speed > 0 else 0.0
        t_load = (self.disp_m3 / (2 * math.pi)) * p_pa / r.eta_hm
        energized = self.phase != self.TRIPPED
        self.motor.step(dt, v_ll if energized else 0.0, f_hz,
                        lambda w, t=t_load: t * min(1.0, max(w, 0.0) / (0.05 * self.w_n)))
        # ---- heat: all hydraulic losses into the oil
        self.heat_w = max(0.0, p_shaft - useful) if energized else 0.0
        # thermostatic water valve on the cooler: opens above cooler_on_c, closes 3 K below
        if self.t_oil >= r.cooler_on_c:
            self.valve_open = True
        elif self.t_oil <= r.cooler_on_c - 3.0:
            self.valve_open = False
        m_w = m_water if self.valve_open else 0.0
        q = self.cooler.step(self.t_oil, self.cooler.m_hot_n, t_water_in, m_w) if m_w > 0 else 0.0
        q = max(0.0, q)
        self.q_water = q
        self.t_oil += (self.heat_w - q) / self.c_oil * dt
        if self.t_oil >= r.trip_oil_c and self.phase != self.TRIPPED:
            self._go(self.TRIPPED)

    def _go(self, ph):
        self.phase, self.t_phase = ph, 0.0

    def reset_trip(self):
        if self.phase == self.TRIPPED and self.t_oil < self.r.trip_oil_c - 5.0:
            self._go(self.IDLE)
            self.x_mm = 0.0

    def tags(self):
        return {"current_a": self.motor.amps, "power_kw": self.motor.p_in / 1000.0,
                "pressure_bar": self.p_bar, "oil_c": self.t_oil, "ram_mm": self.x_mm,
                "bends": self.bends, "phase": self.phase}

    def check(self):
        out = self.motor.check()
        if not -1e-6 <= self.x_mm <= self.r.stroke_mm + 1e-6:
            out.append(f"{self.name}: ram outside its stroke")
        if self.p_bar > self.r.p_max_bar + self.r.p_relief_margin_bar + 1e-6:
            out.append(f"{self.name}: pressure above the relief valve")
        if self.q_relief < -1e-12:
            out.append(f"{self.name}: negative relief flow")
        return out
