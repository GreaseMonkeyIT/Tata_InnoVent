"""chiller-1: an air-cooled screw chiller on the process water loop cool-1 (ideas.md 11.7).

The model is the manufacturer's performance data, not a refrigerant cycle: the cooling capacity
and the compressor power at full load come from the product tables as a function of the leaving
water temperature (LWT) and the ambient (condenser entering) air temperature, by bilinear
interpolation and linear extension past the table edges. Part load follows the published
part-load points (power share against load share at the same conditions).

Arrangement: the process loop runs at about 27 C (above the dew point of a humid plant, so the
pipes do not sweat), but an air-cooled chiller leaves water at 4 to 15 C at most (Daikin EWAD:
setpoint 4 to 14 C; Carrier 30XA: 15 C). A three-way tempering valve therefore mixes chilled water
into the process return to hold the process supply at its setpoint. The heat the chiller removes is
what the valve asks for; the chiller's capacity is the capacity at its own leaving water. When the
process asks for more than that, the valve is fully open, all the loop water passes the
evaporator, and the chiller's leaving water rises with the load.

Control (the unit's own controller):
  - holds the LWT setpoint by unloading in steps (screw slide valves or compressors per circuit)
    down to its minimum capacity, then cycles off; a running unit removes min(demand, capacity)
  - anti-recycle: a stopped compressor waits its start-to-start time before it starts again
  - its PLC (or fail-open) enables it; a demand limit caps its capacity share
Protection: a class 10 overload relay on the compressor current and ANSI 27 undervoltage
(protection.py). Current = P / (sqrt(3) V PF), with a low supply voltage raising the current
(constant power), as for the motors.
Faults (parameters only): condenser fouling (dirty coils, F9) raises the effective condensing air
temperature by `fouling_k`, which costs capacity and power as a hotter day does.
"""
import math
from dataclasses import dataclass, field

from .protection import Overload, Undervoltage

SQRT3 = math.sqrt(3.0)


def interp1(xs, ys, x):
    """Linear interpolation with linear extension past both ends."""
    if x <= xs[0]:
        i = 0
    elif x >= xs[-1]:
        i = len(xs) - 2
    else:
        i = max(j for j in range(len(xs) - 1) if xs[j] <= x)
    t = (x - xs[i]) / (xs[i + 1] - xs[i])
    return ys[i] + t * (ys[i + 1] - ys[i])


def bilinear(lwt_axis, amb_axis, table, lwt, amb):
    """table[i][j] at lwt_axis[i], amb_axis[j]."""
    col = [interp1(amb_axis, row, amb) for row in table]
    return interp1(lwt_axis, col, lwt)


@dataclass(frozen=True)
class ChillerRating:
    lwt_axis: tuple            # leaving water temperatures of the table, C
    amb_axis: tuple            # ambient air temperatures of the table, C
    cap_kw: tuple              # cooling capacity table [lwt][amb], kW
    pin_kw: tuple              # compressor input table [lwt][amb], kW
    part_load: tuple           # ((load share, power share), ...) at the same LWT and ambient
    min_load: float            # minimum capacity share before the unit cycles off
    flow_nominal_kgs: float    # evaporator water flow at nominal
    rla_a: float               # rated load amps (overload relay setting)
    pf: float = 0.88           # compressor power factor (project choice unless given)
    v_nom: float = 400.0
    anti_recycle_s: float = 300.0   # start-to-start
    min_off_s: float = 180.0        # stop-to-start
    fan_kw: float = 0.0        # condenser fan power (in pin_kw when the table includes it)
    source: str = ""


class AirCooledChiller:
    def __init__(self, name, rating, process_setpoint_c=27.0, lwt_setpoint_c=14.0, t_amb_c=35.0):
        self.name, self.r = name, rating
        self.setpoint = process_setpoint_c       # the tempering valve holds the process supply here
        self.lwt_setpoint = lwt_setpoint_c       # the chiller's own leaving water setpoint
        self.t_amb = t_amb_c
        self.enabled = True
        self.demand_limit = 1.0
        self.running = True
        self.last_start = 0.0          # the unit started when the model started
        self.last_stop = -1e9
        self.t = 0.0
        self.q_w = 0.0                 # heat removed this step, W
        self.p_in = 0.0                # electrical input, W
        self.amps = 0.0
        self.load = 0.0                # capacity share in use
        self.lwt = lwt_setpoint_c
        self.fouling_k = 0.0
        self.overload = Overload(rating.rla_a)
        self.uv = Undervoltage()
        self.tripped = False
        self.v = rating.v_nom

    def _lwt_env(self, lwt):
        """The table's leaving-water range is the compressor envelope: above its top the unit
        does not gain capacity (it unloads to protect the compressor), so the lookup holds at
        the edge. The ambient is not clamped: past 44 C the table extends linearly."""
        return min(max(lwt, self.r.lwt_axis[0]), self.r.lwt_axis[-1])

    def capacity_w(self, lwt):
        amb = self.t_amb + self.fouling_k
        return 1000.0 * bilinear(self.r.lwt_axis, self.r.amb_axis, self.r.cap_kw, self._lwt_env(lwt), amb)

    def full_power_w(self, lwt):
        amb = self.t_amb + self.fouling_k
        return 1000.0 * bilinear(self.r.lwt_axis, self.r.amb_axis, self.r.pin_kw, self._lwt_env(lwt), amb)

    def part_power_share(self, load):
        xs = [p[0] for p in self.r.part_load]
        ys = [p[1] for p in self.r.part_load]
        return interp1(xs, ys, load)

    def _off(self):
        self.q_w = self.p_in = self.amps = self.load = 0.0

    def remove(self, t_in, m_kgs, dt):
        """Called by the loop: process return water at t_in with flow m_kgs. Returns heat removed, W."""
        self.t += dt
        uv = self.uv.step(self.v / self.r.v_nom, dt)
        allowed = self.enabled and not self.tripped and not uv
        if not allowed:
            if self.running:
                self.last_stop = self.t
            self.running = False
        elif (not self.running and self.t - self.last_start >= self.r.anti_recycle_s
              and self.t - self.last_stop >= self.r.min_off_s):
            self.running, self.last_start = True, self.t
        if not self.running or m_kgs <= 0:
            self._off()
            self.overload.step(0.0, dt)
            return 0.0
        mc = m_kgs * 4186.0
        demand = max(0.0, mc * (t_in - self.setpoint))
        cap_sp = self.capacity_w(self.lwt_setpoint) * self.demand_limit
        if demand < self.r.min_load * cap_sp and t_in <= self.setpoint - 0.5:
            self.running, self.last_stop = False, self.t   # under the minimum step: cycle off
            self._off()
            return 0.0
        if demand <= cap_sp:
            q, self.lwt = max(demand, self.r.min_load * cap_sp), self.lwt_setpoint
        else:
            # valve fully open: all loop water through the evaporator; LWT = t_in - q / (m c)
            lwt = self.lwt_setpoint
            for _ in range(20):
                q = self.capacity_w(lwt) * self.demand_limit
                lwt = 0.5 * lwt + 0.5 * max(self.lwt_setpoint, t_in - q / mc)
            q = min(self.capacity_w(lwt) * self.demand_limit, mc * max(0.0, t_in - self.lwt_setpoint))
            self.lwt = lwt
        cap_here = self.capacity_w(self.lwt)
        self.load = min(1.0, q / cap_here) if cap_here > 0 else 0.0
        self.p_in = (self.full_power_w(self.lwt) * self.part_power_share(self.load)
                     + 1000.0 * self.r.fan_kw)
        self.q_w = q
        self.amps = self.p_in / (SQRT3 * self.v * self.r.pf) if self.v > 1 else 0.0
        if self.overload.step(self.amps, dt):
            self.tripped = True
        return q

    def check(self):
        out = []
        if self.q_w < 0 or self.p_in < 0:
            out.append(f"{self.name}: negative heat or power")
        if self.running and self.p_in > 0 and self.q_w / self.p_in > 12.0:
            out.append(f"{self.name}: COP above 12 is not physical for an air-cooled unit")
        return out
