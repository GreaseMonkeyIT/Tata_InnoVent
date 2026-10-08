"""press-2: a mechanical punching press with a flywheel and a pneumatic clutch-brake (ideas.md 11.5).

Parts: the main motor (motor.py) drives the flywheel through V-belts; the clutch couples the
flywheel to the crankshaft for one stroke; the brake stops the crank at top dead centre; the
slide punches the plate near bottom dead centre. The clutch and the brake work on plant air
(the compressor-1 header), held by a pressure switch.

Physics per stroke (energy method, the usual basis of press sizing):
  - Engaging the clutch brings the crank and the slide from rest to speed; the clutch slip turns
    as much energy into heat as it gives the crank (a clutch engagement costs twice the kinetic
    energy of the driven parts).
  - The punching work: E = F_max x t x c with F_max = perimeter x t x shear strength and c, the
    share of the thickness over which the full force acts (project choice inside the 0.4 to 0.6 of
    press handbooks).
  - Both come out of the flywheel: it slows by Delta_KE / (J w); the motor then draws more current to
    bring it back before the next stroke. A healthy stroke slows it 10 to 20 % at most (The
    Fabricator, "Stamping 101").
  - The brake stops the crank at the top; its stopping time is measured each stroke (ISO 16092-2
    and ANSI B11.1 ask for a brake monitor that stops the press when the stopping time grows past
    its limit).
Faults (parameters only): brake wear (a longer stopping time), flywheel bearing friction (a motor
friction torque), belt slip (less speed at the flywheel), a thicker or harder plate (more work per
stroke: a load, not a fault), low clutch air (the plant air header or a leak at the press).
"""
import math
from dataclasses import dataclass

from .motor import InductionMotor


@dataclass(frozen=True)
class MechPressRating:
    tonnage_t: float
    stroke_mm: float
    spm_continuous: float          # strokes per minute, continuous
    flywheel_rpm: float            # flywheel speed at no load
    flywheel_energy_kj: float      # usable energy at a 20 % slowdown (maker's figure or derived)
    j_crank_kgm2: float            # crank + slide inertia seen at the crank (project choice)
    clutch_min_bar: float          # pressure switch: no stroke below this clutch air pressure
    brake_stop_ms: float           # normal brake stopping time
    brake_limit_ms: float          # brake monitor limit
    source: str = ""


@dataclass
class Blank:
    perimeter_mm: float = 400.0    # one punched hole or contour
    t_mm: float = 10.0
    shear_mpa: float = 400.0       # S355: shear strength about 0.8 x UTS (project choice)
    c: float = 0.5

    def force_kn(self):
        return self.perimeter_mm * self.t_mm * self.shear_mpa / 1000.0

    def work_j(self):
        return self.force_kn() * 1000.0 * self.t_mm / 1000.0 * self.c


class MechanicalPress:
    IDLE, STROKE, BRAKING, LOCKED = "idle", "stroke", "braking", "locked"

    def __init__(self, name, rating, motor_rating, handling_s=6.0):
        self.name, self.r = name, rating
        self.motor = InductionMotor(f"{name}-motor", motor_rating)
        self.w_m_n = motor_rating.n_rpm * math.pi / 30.0
        # belt ratio: the flywheel turns at its rated speed when the motor runs at no load
        self.ratio = 120.0 * motor_rating.f_hz / motor_rating.poles / rating.flywheel_rpm
        w_fw = rating.flywheel_rpm * math.pi / 30.0
        # flywheel inertia from its usable energy at a 20 % slowdown: E = 0.5 J w^2 (1 - 0.8^2)
        self.j_fw = 2.0 * rating.flywheel_energy_kj * 1000.0 / (w_fw ** 2 * (1.0 - 0.8 ** 2))
        self.blank = Blank()
        self.handling_s = handling_s
        self.state = self.IDLE
        self.t_state = 0.0
        self.strokes = 0
        self.belt_slip = 0.0           # fault: share of speed lost at the belt
        self.brake_wear = 0.0          # fault: share added to the stopping time
        self.air_bar = 6.0             # clutch air at the press (from the plant air header)
        self.last_stop_ms = rating.brake_stop_ms
        self.peak_tonnage = 0.0
        self.min_fw_rpm = self.fw_start = rating.flywheel_rpm
        self._pending = 0.0            # stroke energy still to take from the flywheel, J
        self.auto = True

    @property
    def j_reflected(self):
        """Flywheel inertia seen at the motor shaft."""
        return self.j_fw / self.ratio ** 2

    def fw_rpm(self):
        return self.motor.rpm / self.ratio * (1.0 - self.belt_slip)

    def step(self, dt, v_ll, f_hz):
        r = self.r
        self.t_state += dt
        t_stroke = 60.0 / r.spm_continuous
        load_t = 0.0
        if self.state == self.IDLE:
            if (self.auto and self.t_state >= self.handling_s and self.motor.rpm > 0.9 * self.motor.rating.n_rpm
                    and self.air_bar >= r.clutch_min_bar):
                w_crank = self.fw_rpm() * math.pi / 30.0
                self._pending = 2.0 * 0.5 * r.j_crank_kgm2 * w_crank ** 2 + self.blank.work_j()
                self.peak_tonnage = self.blank.force_kn() / 9.81
                self.min_fw_rpm = self.fw_start = self.fw_rpm()
                self.state, self.t_state = self.STROKE, 0.0
        elif self.state == self.STROKE:
            # the energy comes out in the clutch engagement and the working part of the stroke, the
            # last 30 degrees of crank before bottom dead centre (project choice from press energy
            # curves), as a torque at the flywheel
            t_work = t_stroke * 30.0 / 360.0
            w_fw = max(self.fw_rpm() * math.pi / 30.0, 1.0)
            p_draw = self._pending / max(t_work - self.t_state, dt) if self.t_state < t_work else 0.0
            take = min(self._pending, p_draw * dt)
            self._pending -= take
            load_t = (take / dt) / w_fw / self.ratio                    # torque at the motor shaft
            self.min_fw_rpm = min(self.min_fw_rpm, self.fw_rpm())
            if self.t_state >= t_stroke:
                self.state, self.t_state = self.BRAKING, 0.0
                self.last_stop_ms = r.brake_stop_ms * (1.0 + self.brake_wear)
                self.strokes += 1
        elif self.state == self.BRAKING:
            if self.t_state * 1000.0 >= self.last_stop_ms:
                if self.last_stop_ms > r.brake_limit_ms:
                    self.state = self.LOCKED            # the brake monitor stops the press
                else:
                    self.state, self.t_state = self.IDLE, 0.0
        # the motor turns the flywheel (its inertia reflected) and the stroke torque
        drag = 0.02 * self.motor.rating.t_n            # bearings, belts and windage (project choice)
        self.motor.step(dt, v_ll, f_hz, lambda w, t=load_t: t + drag * min(1.0, max(w, 0.0) / (0.05 * self.w_m_n)),
                        j_load=self.j_reflected)

    def slowdown(self):
        """Flywheel slowdown of the last stroke, share of its speed just before the stroke."""
        n0 = self.fw_start
        return max(0.0, 1.0 - self.min_fw_rpm / n0) if n0 else 0.0

    def tags(self):
        return {"current_a": self.motor.amps, "power_kw": self.motor.p_in / 1000.0,
                "flywheel_rpm": self.fw_rpm(), "strokes": self.strokes,
                "tonnage_t": self.peak_tonnage, "clutch_air_bar": self.air_bar,
                "brake_stop_ms": self.last_stop_ms, "state": self.state}

    def check(self):
        out = self.motor.check()
        if self.fw_rpm() < 0:
            out.append(f"{self.name}: flywheel turning backwards")
        return out
