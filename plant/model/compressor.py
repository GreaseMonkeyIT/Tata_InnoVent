"""compressor-1: a fixed-speed, oil-injected, water-cooled rotary screw compressor with load/unload
control, its receiver and the plant air header (ideas.md 11.7, plant air).

Parts and physics:
  - Drive: an InductionMotor (motor.py) with a star-delta starter. In star each winding sees
    1/sqrt(3) of the line voltage, so the motor runs from v_ll / sqrt(3) until the changeover.
  - Air end, loaded: it takes in free air at the inlet pressure and delivers FAD in proportion to
    speed and to the inlet pressure (a clogged intake filter lowers both FAD and inlet pressure).
    Shaft power follows polytropic compression with oil cooling:
        P = P_rated * (p_in / p_atm) * W(p_d / p_in) / W(p_d,rated / p_atm),  W(r) = r^((n-1)/n) - 1
    With n = 1.3 this gives about 7 % more power per bar at 7.5 bar, the rule of the US DOE
    compressed air sourcebook (1 % per 2 psi). The tests check that independently.
  - Air end, unloaded: the inlet valve closes and the sump blows down. The unloaded power falls
    from the loaded level toward the minimum unloaded power with the blow-down time constant.
  - Control: the controller loads below the load pressure and unloads above the unload pressure,
    both on the pressure TRANSDUCER reading. After a set time unloaded it stops the motor (auto
    stop) if the starts-per-hour limit allows a restart, and it restarts when the reading falls
    below the load pressure.
  - Receiver and header: one air volume, isothermal: V dp/dt = p_atm (Q_in - Q_out), Q in free air.
    Users: a list of demand callables (free air m3/s at the header pressure). Leaks are choked
    orifices: free-air flow in proportion to the absolute pressure. A safety valve vents above
    its set pressure.
  - Oil circuit: the compression heat goes to the oil. A thermostatic valve bypasses the oil cooler
    until the oil reaches its set temperature. The oil cooler (heat.HeatExchanger) gives the heat
    to the cooling water. The element outlet temperature is the injection temperature plus the
    heat of compression over the oil flow. A high element outlet temperature shuts the compressor
    down (latched trip).
  - Aftercooler: the heat in the hot air goes to the same water.

Faults (parameters only, ideas.md 11.9):
  pt_fault_bar   the pressure transducer reads this value (Scenario 2 / F7)
  leak_m3s_bar   extra leak: free air m3/s per bar absolute (F6)
  filter_dp_bar  intake filter pressure drop
  cooler_fouling oil cooler fouling (share of the clean resistance)
"""
import math
from dataclasses import dataclass

from .heat import CP_OIL, CP_WATER, HeatExchanger
from .motor import InductionMotor
from .protection import Overload

P_ATM = 1.013            # bar absolute at the intake (sea level; project choice for the site)
SQRT3 = math.sqrt(3.0)


@dataclass(frozen=True)
class CompressorRating:
    """Datasheet and manual values. Each instance names its source."""
    fad_m3min: float          # free air delivery at the rated working pressure (ISO 1217 Annex C)
    p_work_barg: float        # rated working pressure, bar(e)
    p_max_barg: float         # maximum working pressure, bar(e)
    p_shaft_kw: float         # shaft power at the rated working pressure, loaded
    unloaded_frac: float      # minimum unloaded power / loaded power after full blow-down
    blowdown_s: float         # blow-down time constant of the sump
    n_poly: float = 1.3       # polytropic exponent with oil injection (project choice; gives the DOE rule)
    oil_heat_frac: float = 0.80       # share of the shaft power that heats the oil
    air_heat_frac: float = 0.12       # share that leaves in the hot air, to the aftercooler water
    oil_flow_kgs: float = 1.0 # oil injection flow
    oil_set_c: float = 60.0   # thermostatic valve: the oil cooler takes heat above this injection temperature
    oil_mass_kg: float = 25.0 # oil and element metal heat capacity as oil equivalent (project choice)
    t_trip_c: float = 120.0   # element outlet temperature shutdown
    water_flow_kgs: float = 1.0       # rated cooling water flow
    water_in_c: float = 28.0          # rated cooling water inlet
    starts_per_hour: int = 6          # motor start limit
    star_delta_s: float = 6.0         # time in star before the changeover
    auto_stop_s: float = 360.0        # unloaded time before an auto stop
    receiver_m3: float = 3.0          # receiver and header air volume
    safety_barg: float = 11.0         # safety valve set pressure
    ol_set_a: float = 0.0             # motor overload relay setting, line current (0: none)
    load_barg: float = 6.9            # control band (project setting)
    unload_barg: float = 7.5
    source: str = ""


def poly_work(r, n):
    return r ** ((n - 1.0) / n) - 1.0


class ScrewCompressor:
    """One compressor package with its receiver and header.

    step(dt, v_ll, f_hz, t_water_in, m_water) advances the package. Users go in `demands`
    (callables: p_header_bara -> free air m3/s)."""

    STOPPED, STAR, LOADED, UNLOADED, TRIPPED = "stopped", "star", "loaded", "unloaded", "tripped"

    def __init__(self, name, rating, motor_rating, t_amb_c=35.0):
        self.name, self.r = name, rating
        self.motor = InductionMotor(f"{name}-motor", motor_rating, t_cool_c=t_amb_c)
        r = rating
        self.w_n = motor_rating.n_rpm * math.pi / 30.0
        self.p_rec = P_ATM + r.p_work_barg            # receiver/header pressure, bar abs
        self.p_sump = P_ATM + r.p_work_barg
        self.state = self.STOPPED
        self.enabled = True                            # the PLC or the local start
        self.t_state = 0.0
        self.starts = []                               # times of the last motor starts
        self.t = 0.0
        self.demands = []
        self.t_oil = r.oil_set_c                       # oil injection temperature, C
        self.t_elem = r.oil_set_c                      # element outlet temperature, C
        self.oil_cooler = HeatExchanger(
            f"{name}-oil-cooler", r.oil_flow_kgs, CP_OIL, r.oil_set_c + 25.0, r.oil_set_c,
            r.water_flow_kgs, CP_WATER, r.water_in_c)
        self.q_water = 0.0                             # heat into the cooling water, W
        self.t_water_out = r.water_in_c
        self.q_fad = 0.0                               # delivered free air, m3/s
        self.q_out = 0.0                               # users + leaks, m3/s
        self.venting = False
        self.loaded_s = self.running_s = 0.0
        # faults
        self.pt_fault_bar = None
        self.leak_m3s_bar = 0.0
        self.filter_dp_bar = 0.0
        self.cooler_fouling = 0.0
        self._p_unl = 0.0                               # present unloaded shaft power, W
        self.overload = Overload(rating.ol_set_a) if rating.ol_set_a > 0 else None

    # -------------------------------------------------------------- helpers --
    def reading_barg(self):
        """What the pressure transducer reports (the controller and the tags use it)."""
        return self.pt_fault_bar if self.pt_fault_bar is not None else self.p_rec - P_ATM

    def p_loaded_w(self, p_d_bara, speed_frac):
        """Shaft power loaded at this discharge pressure and speed."""
        r = self.r
        p_in = P_ATM - self.filter_dp_bar
        ref = poly_work((r.p_work_barg + P_ATM) / P_ATM, r.n_poly)
        return (r.p_shaft_kw * 1000.0 * speed_frac * (p_in / P_ATM)
                * poly_work(p_d_bara / p_in, r.n_poly) / ref)

    def fad_m3s(self, speed_frac):
        p_in = P_ATM - self.filter_dp_bar
        return self.r.fad_m3min / 60.0 * speed_frac * (p_in / P_ATM)

    def _can_start(self):
        self.starts = [s for s in self.starts if self.t - s < 3600.0]
        return len(self.starts) < self.r.starts_per_hour

    # ----------------------------------------------------------------- step --
    def step(self, dt, v_ll, f_hz, t_water_in, m_water):
        r = self.r
        self.t += dt
        self.t_state += dt
        rd = self.reading_barg()
        # ---- controller (Elektronikon-style load/unload with auto stop)
        if self.state == self.TRIPPED or not self.enabled:
            if self.state != self.TRIPPED:
                self._go(self.STOPPED)
        elif self.state == self.STOPPED:
            if rd <= r.load_barg and self._can_start():
                self.starts.append(self.t)
                self._go(self.STAR)
        elif self.state == self.STAR:
            if self.t_state >= r.star_delta_s:
                self._go(self.LOADED if rd <= r.unload_barg else self.UNLOADED)
        elif self.state == self.LOADED:
            if rd >= r.unload_barg:
                self._go(self.UNLOADED)
        elif self.state == self.UNLOADED:
            if rd <= r.load_barg:
                self._go(self.LOADED)
            elif self.t_state >= r.auto_stop_s and self._can_start():
                self._go(self.STOPPED)
        # ---- motor and shaft
        energized = self.state in (self.STAR, self.LOADED, self.UNLOADED)
        speed = max(0.0, self.motor.w / self.w_n)
        if self.state == self.LOADED and speed > 0.9:
            p_sh = self.p_loaded_w(self.p_sump, speed)
            self._p_unl = p_sh
            self.q_fad = self.fad_m3s(speed)
        else:
            # unloaded or starting: the inlet valve is closed and the sump blows down
            p_min = r.unloaded_frac * r.p_shaft_kw * 1000.0 * speed
            a = math.exp(-dt / r.blowdown_s)
            self._p_unl = p_min + (self._p_unl - p_min) * a if energized else 0.0
            p_sh = self._p_unl if energized else 0.0
            self.q_fad = 0.0
        # A screw compressor is a positive-displacement machine: at a given pressure its torque
        # is about constant with speed (the power goes with speed). The torque is the power at
        # this speed over this speed, taken at the rated speed so it never blows up near zero.
        p_at_rated = p_sh / speed if speed > 0.05 else (r.unloaded_frac * r.p_shaft_kw * 1000.0)
        t_load = p_at_rated / self.w_n if energized else 0.0
        if self.state == self.STAR:
            load = lambda w, t=t_load: t * min(1.0, max(w, 0.0) / self.w_n) ** 2
        else:
            load = lambda w, t=t_load: t * min(1.0, max(w, 0.0) / (0.05 * self.w_n))
        self.motor.step(dt, v_ll if energized else 0.0, f_hz, load, star=self.state == self.STAR)
        if self.overload is not None and self.overload.step(self.motor.amps, dt) and self.state != self.TRIPPED:
            self._go(self.TRIPPED)                       # the overload relay opens the contactor
        # ---- air: receiver and header
        p_g = self.p_rec - P_ATM
        users = sum(d(self.p_rec) for d in self.demands)
        leak = self.leak_m3s_bar * self.p_rec
        # safety valve: at its set pressure it vents the surplus (a relieving valve holds the set
        # pressure; it does not chatter in this model)
        surplus = self.q_fad - users - leak
        self.venting = p_g >= r.safety_barg - 1e-6 and surplus > 0.0
        vent = surplus if self.venting else 0.0
        self.q_out = users + leak + vent
        self.p_rec += P_ATM * (self.q_fad - self.q_out) / r.receiver_m3 * dt
        self.p_rec = max(P_ATM, self.p_rec)
        loaded_now = self.state == self.LOADED
        self.p_sump = (self.p_rec + 0.3) if loaded_now else P_ATM + 1.0 + (self.p_sump - P_ATM - 1.0) * math.exp(-dt / r.blowdown_s)
        # ---- oil circuit and water. The heat of compression goes to the oil (oil_heat_frac of
        # the shaft power) and to the hot air (air_heat_frac), which the aftercooler gives to the
        # same water. The motor losses go to the room air (TEFC motor outside the water circuit).
        heat_oil = r.oil_heat_frac * p_sh
        heat_air = r.air_heat_frac * p_sh if self.q_fad > 0.0 else 0.0
        c_oil = r.oil_mass_kg * CP_OIL
        self.oil_cooler.fouling = self.cooler_fouling
        hot_in = self.t_oil + heat_oil / (r.oil_flow_kgs * CP_OIL)
        self.t_elem = hot_in
        if energized and m_water > 0.0:
            q_max = self.oil_cooler.step(hot_in, r.oil_flow_kgs, t_water_in, m_water)
            # thermostatic valve: never cool the injected oil below its set temperature
            q_set = max(0.0, (hot_in - r.oil_set_c) * r.oil_flow_kgs * CP_OIL)
            q_cool = max(0.0, min(q_max, q_set))
        else:
            q_cool = 0.0
        self.t_oil += (heat_oil - q_cool) / c_oil * dt
        self.q_water = q_cool + heat_air
        self.t_water_out = t_water_in + (self.q_water / (m_water * CP_WATER) if m_water > 0 else 0.0)
        if self.t_elem >= r.t_trip_c and self.state != self.TRIPPED:
            self._go(self.TRIPPED)
        if energized:
            self.running_s += dt
            if loaded_now:
                self.loaded_s += dt

    def _go(self, st):
        self.state, self.t_state = st, 0.0

    def reset_trip(self):
        if self.state == self.TRIPPED:
            if self.overload is not None:
                self.overload.reset()
            self._go(self.STOPPED)

    def tags(self):
        return {"pressure_barg": self.reading_barg(), "element_c": self.t_elem,
                "current_a": self.motor.amps, "power_kw": self.motor.p_in / 1000.0,
                "state": self.state, "water_out_c": self.t_water_out,
                "loaded_h": self.loaded_s / 3600.0, "running_h": self.running_s / 3600.0}

    def check(self):
        out = self.motor.check()
        if not math.isfinite(self.p_rec) or self.p_rec < P_ATM - 1e-9:
            out.append(f"{self.name}: receiver pressure out of range")
        if self.p_rec - P_ATM > self.r.safety_barg + 0.5:
            out.append(f"{self.name}: receiver above its safety valve")
        if self.q_fad < 0 or self.q_out < 0:
            out.append(f"{self.name}: a negative air flow")
        return out
