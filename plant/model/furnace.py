"""furnace-1: a gas-fired car-bottom stress-relieving furnace for large weldments (ideas.md 11.5).

Parts: a car (the hearth) that rolls the load in and out, a ceramic-fibre-lined chamber, high-velocity
burners in zones along the length (gas.py burners on the plant gas header), a combustion air
blower, zone and load thermocouples, and a program controller.

Thermal model, per zone i (a lumped model; the zones exchange heat with their neighbours):
    C_f dT_f/dt = q_avail_i - q_load_i - q_wall_i - sum_j g_zz (T_f,i - T_f,j)
    C_l(T) dT_l/dt = q_load_i
    q_load = h A (T_f - T_l) + sigma eps A (T_f^4 - T_l^4)        (convection + radiation, K)
    q_wall = U_wall A_wall (T_f - T_amb)                            (through the fibre lining)
    q_avail = gas flow x LHV x available heat(T_flue)               (the rest leaves in the flue)
Available heat of natural gas at 10 % excess air falls with the flue temperature (US DOE process
heating tip sheet 2, Bennett chart: about 63 % at 600 C, 61 % at 650 C, 55.6 % at 760 C), so a
hot furnace burns more gas for the same heat. The flue leaves at the zone temperature (no
recuperator).
Steel heat capacity: EN 1993-1-2 (Eurocode 3, fire part), 3.4.1.2:
    cp = 425 + 0.773 T - 1.69e-3 T^2 + 2.22e-6 T^3   J/kg K   (20 C <= T < 600 C)
    cp = 666 + 13002 / (738 - T)                                  (600 C <= T < 735 C)

Program (AWS D1.1 postweld heat treatment, as summarised in the LANL welding standard GWS 1-08):
load at or under 315 C; above 315 C heat at most 220 C/h divided by the thickness in inches (at
most 220, need not be under 55 C/h); hold at 595 to 650 C for 1 h per 25 mm (at least 15 min,
ASME UCS-56 rule used for the time); cool at most 260 C/h divided by the thickness (at most 260)
down to 315 C, then burners off and the load cools in the closed furnace.
Each zone has a PID on its burner firing (modulating, 10:1 turndown, project choice) that follows
the program setpoint from its zone thermocouple.

Controlled cooling (the usual method): burners off, the combustion air blower blows ambient air
through the burner ports and out of the flue, a damper per zone follows the cooling setpoint. The
air flow at full blower is the combustion air of all burners at full fire: 9.52 m3 of air per m3
of methane (stoichiometric) plus the 10 % excess air of the available-heat chart. The air leaves at
the zone temperature (counted with the flue loss). Below 315 C the car comes out and the load
cools in still air (AWS D1.1), which ends the furnace cycle.

Faults (parameters only): a flame failure in one zone (its burner locks out), a low gas pressure
(the gas header, gas.py, F10), a leaking door seal (more wall loss), a drifting zone thermocouple
(sensors.py drift on the zone reading).
"""
import math
from dataclasses import dataclass, field

from .gas import Burner, BurnerRating

SIGMA = 5.670e-8


def cp_steel(t_c):
    """EN 1993-1-2 3.4.1.2, J/kg K."""
    t = min(max(t_c, 20.0), 734.0)
    if t < 600.0:
        return 425.0 + 0.773 * t - 1.69e-3 * t ** 2 + 2.22e-6 * t ** 3
    return 666.0 + 13002.0 / (738.0 - t)


def available_heat(t_flue_c):
    """Share of the gas heat (LHV basis) that stays in the furnace, natural gas, 10 % excess air,
    cold combustion air (DOE tip sheet 2 points, linear between and beyond them)."""
    pts = ((600.0, 0.63), (650.0, 0.61), (760.0, 0.556))
    if t_flue_c <= pts[0][0]:
        a = (pts[1][1] - pts[0][1]) / (pts[1][0] - pts[0][0])
        return min(0.85, pts[0][1] + a * (t_flue_c - pts[0][0]))
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if t_flue_c <= x1:
            return y0 + (y1 - y0) * (t_flue_c - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    return y1 + (y1 - y0) / (x1 - x0) * (t_flue_c - x1)


@dataclass(frozen=True)
class FurnaceRating:
    zones: int
    burner_kw: float              # rated input per zone burner
    burner_p_kpa: float           # burner inlet pressure at rated input
    load_t: float                 # steel load per batch, tonnes
    car_t: float                  # car and fixtures as steel equivalent, tonnes
    load_area_m2: float           # exposed load surface
    wall_area_m2: float           # chamber lining area
    u_wall: float                 # lining heat loss coefficient, W/m2 K
    c_furnace_j_k: float          # chamber heat capacity (fibre hot face, atmosphere)
    h_conv: float                 # convection coefficient to the load, W/m2 K
    eps: float                    # effective emissivity furnace-load
    g_zone: float                 # heat exchange between neighbouring zones, W/K
    blower_kw: float              # combustion air blower at full fire
    source: str = ""


@dataclass
class Program:
    thickness_mm: float = 25.0
    t_load_max: float = 315.0
    t_hold: float = 620.0
    t_end: float = 315.0

    def heat_rate_c_h(self):
        inch = self.thickness_mm / 25.4
        return max(55.0, min(220.0, 220.0 / inch))

    def cool_rate_c_h(self):
        inch = self.thickness_mm / 25.4
        return min(260.0, 260.0 / inch)

    def hold_s(self):
        return max(900.0, 3600.0 * self.thickness_mm / 25.0)


class Furnace:
    LOADING, HEAT, HOLD, COOL, DONE = "loading", "heat", "hold", "cool", "done"

    def __init__(self, name, rating, gas_supply, program=None, t_amb_c=35.0):
        self.name, self.r = name, rating
        self.prog = program or Program()
        self.t_amb = t_amb_c
        self.gas = gas_supply
        lhv = gas_supply.r.lhv_mj_m3
        br = BurnerRating(q_rated_kw=rating.burner_kw, p_rated_kpa=rating.burner_p_kpa, eff=1.0,
                          p_low_trip_kpa=0.5 * rating.burner_p_kpa)
        self.burners = [Burner(f"{name}-z{i + 1}", br, lhv) for i in range(rating.zones)]
        gas_supply.burners.extend(self.burners)
        n = rating.zones
        self.t_f = [t_amb_c] * n
        self.t_l = [t_amb_c] * n
        self.m_zone = (rating.load_t + rating.car_t) * 1000.0 / n
        self.setpoint = t_amb_c
        self.state = self.LOADING
        self.t_state = 0.0
        self.t = 0.0
        self._i = [0.0] * n                # PID integrators
        self.flame_fail = [False] * n
        self.door_leak = 0.0               # extra wall loss share (a leaking door seal)
        self.damper = [0.0] * n            # cooling air damper per zone, 0..1
        self._ic = [0.0] * n
        gas_m3s = rating.zones * self.burners[0].q_n_m3h / 3600.0
        self.air_kgs_max = gas_m3s * 9.52 * 1.10 * 1.2       # stoichiometric x 1.1, air 1.2 kg/m3
        self.tc_bias = [0.0] * n           # zone thermocouple drift, K
        self.q_avail = self.q_load = self.q_wall = self.q_flue = 0.0
        self.gas_m3 = 0.0
        self.e_in = self.e_load = self.e_wall = self.e_flue = 0.0

    # ----------------------------------------------------------------- program --
    def start(self):
        if self.state in (self.LOADING, self.DONE):
            self.state, self.t_state = self.HEAT, 0.0
            self.setpoint = max(self.zone_reading(i) for i in range(self.r.zones))

    def zone_reading(self, i):
        return self.t_f[i] + self.tc_bias[i]

    def _program(self, dt):
        p = self.prog
        load_min = min(self.t_l)
        if self.state == self.HEAT:
            # Under 315 C the code sets no rate (the controller ramps at 3 x the limit, project
            # choice); above it, the AWS limit. A guaranteed soak holds the ramp while the coldest
            # load lags the setpoint by more than 50 K (project choice).
            rate = p.heat_rate_c_h() / 3600.0 if load_min >= p.t_load_max else 3.0 * p.heat_rate_c_h() / 3600.0
            if self.setpoint - load_min < 50.0:
                self.setpoint = min(p.t_hold, self.setpoint + rate * dt)
            if load_min >= p.t_hold - 10.0:
                self.state, self.t_state = self.HOLD, 0.0
        elif self.state == self.HOLD:
            self.setpoint = p.t_hold
            if self.t_state >= p.hold_s():
                self.state, self.t_state = self.COOL, 0.0
        elif self.state == self.COOL:
            self.setpoint = max(p.t_end, self.setpoint - p.cool_rate_c_h() / 3600.0 * dt)
            if max(self.t_l) <= p.t_end + 5.0:
                self.state, self.t_state = self.DONE, 0.0      # car out: still-air cooling outside

    # ------------------------------------------------------------------- step --
    def step(self, dt):
        """Advance dt; the gas header must have been stepped (gas.step) for this tick's pressure."""
        r = self.r
        self.t += dt
        self.t_state += dt
        self._program(dt)
        firing_on = self.state in (self.HEAT, self.HOLD, self.COOL)
        # PID per zone on firing (gains: project choices, tuned to hold +-5 K on the hold)
        kp, ki = 0.02, 0.0002
        for i, b in enumerate(self.burners):
            if not firing_on:
                b.firing, self._i[i] = 0.0, 0.0
                continue
            e = self.setpoint - self.zone_reading(i)
            self._i[i] = min(max(self._i[i] + e * dt, -1.0 / ki), 1.0 / ki)
            u = kp * e + ki * self._i[i]
            b.firing = 0.0 if u < 0.1 else min(1.0, u)          # 10:1 turndown: off under 10 %
            b.enabled = not self.flame_fail[i]
        # cooling air dampers (COOL only): open when the zone is above the cooling setpoint
        for i in range(r.zones):
            if self.state == self.COOL:
                e = self.zone_reading(i) - self.setpoint
                self._ic[i] = min(max(self._ic[i] + e * dt, 0.0), 1.0 / 0.0002)
                self.damper[i] = min(1.0, max(0.0, 0.02 * e + 0.0002 * self._ic[i]))
            else:
                self.damper[i], self._ic[i] = 0.0, 0.0
        # heat per zone
        n = r.zones
        a_l, a_w = r.load_area_m2 / n, r.wall_area_m2 / n
        q_av_tot = q_ld_tot = q_wl_tot = q_fl_tot = 0.0
        new_f, new_l = list(self.t_f), list(self.t_l)
        for i, b in enumerate(self.burners):
            q_gas = b.heat_w                                    # burner eff = 1: LHV heat in the gas
            ah = available_heat(self.t_f[i])
            q_av = q_gas * ah
            tf, tl = self.t_f[i] + 273.15, self.t_l[i] + 273.15
            q_ld = r.h_conv * a_l * (tf - tl) + SIGMA * r.eps * a_l * (tf ** 4 - tl ** 4)
            q_wl = r.u_wall * a_w * (1.0 + self.door_leak * (1.0 if i == n - 1 else 0.0)) * (self.t_f[i] - self.t_amb)
            q_zz = sum(r.g_zone * (self.t_f[i] - self.t_f[j]) for j in (i - 1, i + 1) if 0 <= j < n)
            q_air = self.damper[i] * self.air_kgs_max / n * 1005.0 * max(0.0, self.t_f[i] - self.t_amb)
            q_wl_air = q_air
            c_f = r.c_furnace_j_k / n
            new_f[i] = self.t_f[i] + (q_av - q_ld - q_wl - q_zz - q_wl_air) / c_f * dt
            new_l[i] = self.t_l[i] + q_ld / (self.m_zone * cp_steel(self.t_l[i])) * dt
            q_av_tot += q_av
            q_ld_tot += q_ld
            q_wl_tot += q_wl
            q_fl_tot += q_gas - q_av + q_wl_air
        self.t_f, self.t_l = new_f, new_l
        self.q_avail, self.q_load, self.q_wall, self.q_flue = q_av_tot, q_ld_tot, q_wl_tot, q_fl_tot
        self.gas_m3 += sum(b.flow_m3h for b in self.burners) * dt / 3600.0
        self.e_in += sum(b.heat_w for b in self.burners) * dt
        self.e_flue += q_fl_tot * dt
        self.e_wall += q_wl_tot * dt
        self.e_load += q_ld_tot * dt

    def stored_j(self):
        """Heat in the chamber and the load above ambient (for the energy balance)."""
        e = sum(self.r.c_furnace_j_k / self.r.zones * (t - self.t_amb) for t in self.t_f)
        return e

    def blower_kw(self):
        firing = sum(b.firing for b in self.burners if not b.locked_out) / self.r.zones
        air = max(firing, sum(self.damper) / self.r.zones)
        return self.r.blower_kw * (0.3 + 0.7 * air) if self.state in (self.HEAT, self.HOLD, self.COOL) else 0.0

    def tags(self):
        out = {f"zone{i + 1}_c": self.zone_reading(i) for i in range(self.r.zones)}
        out.update({f"load{i + 1}_c": t for i, t in enumerate(self.t_l)})
        out.update({"setpoint_c": self.setpoint, "gas_m3h": sum(b.flow_m3h for b in self.burners),
                    "blower_kw": self.blower_kw(), "state": self.state})
        return out

    def check(self):
        out = []
        if any(not math.isfinite(t) for t in self.t_f + self.t_l):
            out.append(f"{self.name}: a temperature is not finite")
        if max(self.t_f) > 1100.0:
            out.append(f"{self.name}: chamber above 1100 C")
        if min(self.t_l) < self.t_amb - 1.0:
            out.append(f"{self.name}: load colder than the ambient")
        return out
