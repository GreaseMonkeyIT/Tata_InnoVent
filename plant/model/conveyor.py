"""conveyor-1: a powered roller conveyor that moves weldments between the fabrication stations
(ideas.md 11.5).

Resistance of a roller conveyor (the rolling-resistance form of the drive-power formula, IOP Conf.
Ser. Mater. Sci. Eng. 393 012037): F = m g mu_r on a level line, mu_r = 0.01 to 0.02 for a steel load
on steel rollers (Pulseroller technical guide) plus the rollers' own drag. Power at the drive
shaft P = F v; through the gear unit (SEW R/F/K: 96 % for a 3-stage unit) to the motor.
Motor: an SEW DRN132S4 5.5 kW IE3 gearmotor; its efficiency (89.6 / 90.6 / 90.6 % at 100 / 75 / 50 %
load, SEW addendum 33089094) gives the electrical input; under 50 % load the efficiency falls
toward the no-load losses (project choice: losses at 50 % held as a floor).
Start: the load accelerates to the line speed (F + m a). A jam (F3) holds the load: the motor stalls
at its breakdown torque and its class 10 overload relay trips (protection.py).
"""
import math
from dataclasses import dataclass

from .protection import Overload

G = 9.81


@dataclass(frozen=True)
class ConveyorRating:
    p_motor_kw: float
    eta_motor: tuple            # ((load share, efficiency), ...)
    eta_gear: float
    v_mps: float                # line speed
    accel_mps2: float
    mu_r: float
    roller_drag_n: float        # empty-line drag
    i_rated_a: float
    pf: float = 0.80            # project choice
    v_nom: float = 400.0
    source: str = ""


class Conveyor:
    IDLE, MOVING, TRIPPED = "idle", "moving", "tripped"

    def __init__(self, name, rating, length_m=12.0):
        self.name, self.r = name, rating
        self.length = length_m
        self.state = self.IDLE
        self.load_kg = 0.0
        self.x = 0.0
        self.v = 0.0
        self.mu_extra = 0.0            # fault: a seized roller bearing (more drag)
        self.jammed = False            # fault F3
        self.ol = Overload(rating.i_rated_a)
        self.p_el = self.amps = 0.0
        self.moves = 0

    def send(self, mass_kg):
        if self.state == self.IDLE:
            self.load_kg, self.x, self.state = mass_kg, 0.0, self.MOVING

    def eta_motor(self, load):
        pts = self.r.eta_motor
        if load >= pts[0][0]:
            return pts[0][1]
        lo = pts[-1]
        if load <= lo[0]:
            # losses held at the 50 % value: efficiency falls toward zero load
            p_out = load * self.r.p_motor_kw
            loss = lo[0] * self.r.p_motor_kw * (1.0 / lo[1] - 1.0)
            return p_out / (p_out + loss) if p_out > 0 else 0.0
        for (l1, e1), (l0, e0) in zip(pts, pts[1:]):
            if l0 <= load <= l1:
                return e0 + (e1 - e0) * (load - l0) / (l1 - l0)
        return pts[-1][1]

    def step(self, dt, v_ll):
        r = self.r
        f = 0.0
        if self.state == self.MOVING:
            m = self.load_kg
            f = r.roller_drag_n + m * G * (r.mu_r + self.mu_extra)
            if self.jammed:
                f_max = 3.0 * r.p_motor_kw * 1000.0 / r.v_mps * r.eta_gear   # breakdown torque, about 3 x rated
                f, self.v = f_max, 0.0
            else:
                a = r.accel_mps2 if self.v < r.v_mps else 0.0
                f += m * a
                self.v = min(r.v_mps, self.v + a * dt)
                self.x += self.v * dt
                if self.x >= self.length:
                    self.state, self.v, self.load_kg = self.IDLE, 0.0, 0.0
                    self.moves += 1
        p_shaft = f * (self.v if not self.jammed else r.v_mps * 0.05) / r.eta_gear
        if self.jammed and self.state == self.MOVING:
            p_shaft = 3.0 * r.p_motor_kw * 1000.0 * 0.3       # a stalled motor: torque at low speed, heat
        load = p_shaft / (r.p_motor_kw * 1000.0)
        running = self.state != self.TRIPPED
        if running and self.state == self.MOVING:
            eta = max(self.eta_motor(min(load, 1.0)), 0.3)
            self.p_el = p_shaft / eta
            i_load = self.p_el / (math.sqrt(3.0) * v_ll * r.pf) if v_ll > 1 else 0.0
            self.amps = 7.0 * r.i_rated_a if self.jammed else max(i_load, 0.35 * r.i_rated_a)
        elif running:
            self.p_el, self.amps = 0.0, 0.0
        else:
            self.p_el, self.amps = 0.0, 0.0
        if self.ol.step(self.amps, dt) and self.state != self.TRIPPED:
            self.state = self.TRIPPED

    def tags(self):
        return {"current_a": self.amps, "power_kw": self.p_el / 1000.0, "speed_mps": self.v,
                "moves": self.moves, "state": self.state}

    def check(self):
        return [f"{self.name}: speed out of range"] if not 0.0 <= self.v <= self.r.v_mps + 1e-9 else []
