"""The engine assembly line, first stations (ideas.md 11.6): the pallet conveyor, a tightening
station with nutrunners, the parts washer and the pressure-decay leak test.

  PalletConveyor: a roller pallet transfer (Bosch Rexroth TS 5 class) moving one engine per takt;
    each section's drive takes a small power; a jammed pallet holds the line behind it.
  TighteningStation: each bolt is tightened to a snug torque and then turned a set angle (torque +
    angle, the usual strategy for head and main bearing bolts); the final torque scatters with the
    tool's capability (3 sigma under 4 % for an angle-head tool); a worn socket widens the scatter.
    The controller draws its mean power with short peaks while it runs; a supply dip under its
    minimum resets it (the bolt in progress is lost and redone).
  PartsWasher: a heated wash tank (heat balance: heater in, losses and the parts out), the high and
    low pressure spray pumps and a hot-air dryer. The block leaves near the wash temperature; a
    heater control fault (F13) holds the tank hotter, so the block leaves warmer.
  LeakTester: pressure decay on the water jacket. The test volume is filled with plant air, held to
    settle and measured: the pressure falls by the leak (dP/dt = p_atm Q_leak / V) and by the part
    cooling during the test (ideal gas, dP/P = dT/T: about 0.34 % per K near 20 C). A part that comes
    warm from the washer cools in the test and reads as a leak: the F13 false reject. Low plant air
    (under the needed supply) means the fill pressure is not reached.
"""
import math
import random
from dataclasses import dataclass

P_ATM_PA = 101325.0


# ------------------------------------------------------------------------------ pallet conveyor --
@dataclass(frozen=True)
class PalletConveyorRating:
    speed_m_min: float
    section_kw: float
    sections: int
    pallet_kg: float
    source: str = ""


class PalletConveyor:
    def __init__(self, name, r, takt_s=290.0):
        self.name, self.r, self.takt_s = name, r, takt_s
        self.t = 0.0
        self.jam = False          # fault: a pallet stuck at a stop
        self.moved = 0
        self.p_el = 0.0

    def step(self, dt):
        self.t += dt
        move_s = 3.0 / (self.r.speed_m_min / 60.0)          # 3 m between stations (project choice)
        phase = self.t % self.takt_s
        moving = phase < move_s and not self.jam
        if self.jam:
            # the drives keep running against the friction clutches (the TS 5 roller clutch)
            self.p_el = 1000.0 * self.r.section_kw * self.r.sections * 0.9
        else:
            self.p_el = 1000.0 * self.r.section_kw * self.r.sections * (0.8 if moving else 0.3)
        if not self.jam and phase < dt:
            self.moved += 1

    def tags(self):
        return {"power_kw": self.p_el / 1000.0, "engines_moved": self.moved, "jam": float(self.jam)}


# ------------------------------------------------------------------------------- tightening --
@dataclass(frozen=True)
class NutrunnerRating:
    target_nm: float              # final torque expected after snug + angle
    sigma_frac: float             # 1 sigma of the final torque share (3 sigma / 3)
    bolt_s: float
    mean_w: float                 # controller mean power while running
    peak_w: float
    standby_w: float
    v_min_pu: float               # the controller resets under this supply share
    source: str = ""


class TighteningStation:
    def __init__(self, name, r, bolts=10, spindles=2, seed=0):
        self.name, self.r = name, r
        self.bolts, self.spindles = bolts, spindles
        self.rng = random.Random(f"nut:{name}:{seed}")
        self.socket_wear = 0.0        # fault: widens the scatter (0 .. 1)
        self.t_bolt = 0.0
        self.done = 0
        self.torques = []
        self.resets = 0
        self.p_el = r.standby_w
        self.active = True

    def step(self, dt, v_pu):
        r = self.r
        if v_pu < r.v_min_pu:
            if self.t_bolt > 0:
                self.resets += 1
            self.t_bolt = 0.0
            self.p_el = 0.0
            return
        if not self.active:
            self.p_el = r.standby_w
            return
        self.t_bolt += dt
        peak = self.t_bolt > r.bolt_s - 0.5
        self.p_el = self.spindles * (r.peak_w if peak else r.mean_w)
        if self.t_bolt >= r.bolt_s:
            self.t_bolt = 0.0
            for _ in range(self.spindles):
                sd = r.sigma_frac * (1.0 + 3.0 * self.socket_wear)
                self.torques.append(r.target_nm * (1.0 + self.rng.gauss(0.0, sd)))
                self.done += 1

    def tags(self):
        last = self.torques[-1] if self.torques else 0.0
        return {"power_w": self.p_el, "bolts": self.done, "last_torque_nm": last, "resets": self.resets}


# ------------------------------------------------------------------------------------- washer --
@dataclass(frozen=True)
class WasherRating:
    t_wash_c: float
    tank_l: float
    heater_kw: float
    hp_pump_kw: float
    lp_pump_kw: float
    dryer_kw: float
    cycle_s: float
    part_kg: float
    loss_w_k: float
    source: str = ""


class PartsWasher:
    def __init__(self, name, r, t_amb=35.0):
        self.name, self.r = name, r
        self.t_tank = r.t_wash_c
        self.t_amb = t_amb
        self.offset_k = 0.0           # F13: the heater control holds the tank this much hotter
        self.t = 0.0
        self.parts = 0
        self.t_part_out = t_amb
        self.p_el = 0.0

    def step(self, dt):
        r = self.r
        self.t += dt
        sp = r.t_wash_c + self.offset_k
        heater = r.heater_kw * 1000.0 if self.t_tank < sp else 0.0
        phase = self.t % r.cycle_s
        spraying = phase < 0.75 * r.cycle_s
        # each part takes heat from the tank to come up to the wash temperature (cast iron 460 J/kg K)
        part_w = (r.part_kg * 460.0 * (self.t_tank - self.t_amb) / r.cycle_s) if spraying else 0.0
        c_tank = r.tank_l * 4186.0
        self.t_tank += (heater - part_w - r.loss_w_k * (self.t_tank - self.t_amb)) / c_tank * dt
        pumps = (r.hp_pump_kw + r.lp_pump_kw) * 1000.0 if spraying else 0.0
        dryer = r.dryer_kw * 1000.0 if not spraying else 0.0
        self.p_el = heater + pumps + dryer
        if phase < dt:
            self.parts += 1
            # the hot-air dryer brings the part a little under the wash temperature (project choice)
            self.t_part_out = self.t_tank - 15.0

    def tags(self):
        return {"tank_c": self.t_tank, "power_kw": self.p_el / 1000.0, "part_out_c": self.t_part_out,
                "parts": self.parts}


# ---------------------------------------------------------------------------------- leak test --
@dataclass(frozen=True)
class LeakTestRating:
    p_test_bar: float             # gauge test pressure
    volume_l: float               # test volume (water jacket)
    fill_s: float
    settle_s: float
    test_s: float
    reject_pa: float              # pressure-decay reject limit over the test time
    supply_margin_bar: float      # supply must exceed the test pressure by this
    cool_k_per_s_per_k: float     # part cooling rate per K above the room (project choice)
    source: str = ""


class LeakTester:
    def __init__(self, name, r, t_room=35.0):
        self.name, self.r, self.t_room = name, r, t_room
        self.leak_sccm = 0.0          # a real leak in the part (scc/min at the test pressure)
        self.air_bar = 7.0
        self.results = []             # (decay Pa, passed)

    def test(self, t_part_c):
        """One test of a part that arrives at t_part_c. Returns (decay Pa, passed, fill_ok)."""
        r = self.r
        if self.air_bar < r.p_test_bar + r.supply_margin_bar:
            self.results.append((float("nan"), False))
            return float("nan"), False, False
        p_abs = r.p_test_bar * 1e5 + P_ATM_PA
        v = r.volume_l / 1000.0
        # leak: Q at standard conditions (scc/min) -> pressure fall in the volume
        q_std = self.leak_sccm * 1e-6 / 60.0
        dp_leak = P_ATM_PA * q_std / v * r.test_s
        # thermal: the part (and the air in it) cools toward the room during settle + test
        k = r.cool_k_per_s_per_k
        dT0 = t_part_c - self.t_room
        dT_start = dT0 * math.exp(-k * r.settle_s)
        dT_end = dT_start * math.exp(-k * r.test_s)
        t_start = self.t_room + dT_start + 273.15
        dp_th = p_abs * (dT_start - dT_end) / t_start          # ideal gas at constant volume
        decay = dp_leak + dp_th
        ok = decay < r.reject_pa
        self.results.append((decay, ok))
        return decay, ok, True


# ----------------------------------------------------------------------------- test stands --
def fmep_motored_bar(rpm):
    """Motored friction mean effective pressure of a diesel, bar (Heywood, Internal Combustion
    Engine Fundamentals, the motored-friction correlation 0.97 + 0.15 N + 0.05 N^2, N in 1000 rpm)."""
    n = rpm / 1000.0
    return 0.97 + 0.15 * n + 0.05 * n * n


@dataclass(frozen=True)
class EngineRating:
    displacement_l: float
    rated_kw: float
    rated_rpm: float
    fuel_l_h_rated: float          # at rated power
    coolant_kw_rated: float        # heat to coolant at rated power
    ambient_kw_rated: float        # radiated to the room at rated power
    lhv_mj_kg: float = 42.7
    fuel_kg_l: float = 0.85
    source: str = ""


class ColdTest:
    """The unfired engine turned by an electric drive (non-regenerative: brake resistor)."""

    def __init__(self, name, engine, test_rpm=500.0, test_s=120.0, load_s=60.0, drive_eta=0.9):
        self.name, self.e = name, engine
        self.test_rpm, self.test_s, self.load_s, self.eta = test_rpm, test_s, load_s, drive_eta
        self.t = 0.0
        self.tight = 0.0               # product fault F16: a tight bearing (share of extra friction)
        self.torque = 0.0
        self.p_el = 0.0
        self.results = []              # (mean torque, passed)
        self._acc = []

    def reference_nm(self):
        return fmep_motored_bar(self.test_rpm) * 1e5 * self.e.displacement_l / 1000.0 / (4.0 * math.pi)

    def step(self, dt):
        self.t += dt
        phase = self.t % (self.load_s + self.test_s)
        if phase >= self.load_s:
            self.torque = self.reference_nm() * (1.0 + self.tight)
            w = self.test_rpm * math.pi / 30.0
            self.p_el = self.torque * w / self.eta
            self._acc.append(self.torque)
        else:
            if self._acc:
                m = sum(self._acc) / len(self._acc)
                self.results.append((m, abs(m / self.reference_nm() - 1.0) <= 0.20))
                self._acc = []
            self.torque, self.p_el = 0.0, 0.0


class HotTest:
    """The fired engine against a regenerative AC dynamometer, through its test schedule."""

    SCHEDULE = ((120.0, 0.0), (120.0, 0.25), (120.0, 0.50), (120.0, 0.75), (240.0, 1.0), (120.0, 0.0))

    def __init__(self, name, engine, regen_eta=0.90, load_s=300.0):
        self.name, self.e = name, engine
        self.regen_eta, self.load_s = regen_eta, load_s
        self.t = 0.0
        self.dyno_trip = False         # F12
        self.load = 0.0
        self.p_brake = self.p_el = 0.0
        self.q_coolant = self.q_exhaust = 0.0
        self.engines = 0

    def efficiency(self, load):
        """Brake efficiency against load: rated from the spec sheet, lower at part load (project
        choice for the shape: efficiency falls with the square of the unloading)."""
        e = self.e
        fuel_kw = e.fuel_l_h_rated / 3600.0 * e.fuel_kg_l * e.lhv_mj_kg * 1000.0
        eta_r = e.rated_kw / fuel_kw
        return eta_r * (1.0 - 0.5 * (1.0 - load) ** 2)

    def step(self, dt):
        self.t += dt
        total = self.load_s + sum(d for d, _ in self.SCHEDULE)
        ph = self.t % total
        self.load = 0.0
        if ph >= self.load_s and not self.dyno_trip:
            x = ph - self.load_s
            for d, l in self.SCHEDULE:
                if x < d:
                    self.load = l
                    break
                x -= d
        elif ph < dt:
            self.engines += 1
        e = self.e
        self.p_brake = self.load * e.rated_kw * 1000.0
        running = ph >= self.load_s and not self.dyno_trip
        if running:
            eta = self.efficiency(max(self.load, 0.05))
            fuel = max(self.p_brake, 0.05 * e.rated_kw * 1000.0) / eta
            # coolant share: rated value from the spec sheet, more at part load (Gao 2019: over half of
            # the fuel energy at low load) -> linear in the unloading (project choice for the shape)
            share_r = e.coolant_kw_rated / (e.rated_kw / self.efficiency(1.0))
            self.q_coolant = fuel * (share_r + 0.25 * (1.0 - self.load))
            self.q_exhaust = max(0.0, fuel - self.p_brake - self.q_coolant
                                 - e.ambient_kw_rated * 1000.0 * max(self.load, 0.1))
        else:
            self.q_coolant = self.q_exhaust = 0.0
        # the regenerative dyno feeds the brake power back: a negative electrical load
        self.p_el = -self.p_brake * self.regen_eta + 5000.0          # 5 kW cell services (project)
