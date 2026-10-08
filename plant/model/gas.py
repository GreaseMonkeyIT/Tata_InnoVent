"""Fuel gas: the plant's natural gas supply, its pressure regulator, the header, and the burners
that take gas from it (ideas.md 11.7; users: furnace-1 and the paint bake oven).

  - Supply: the utility line pressure upstream of the regulator (a fault lowers it, F10).
  - Regulator: holds its outlet at the set pressure with a small droop (the outlet falls in
    proportion to the flow), while the inlet stays above the outlet by its minimum differential.
    Below that the regulator is wide open and the outlet follows the inlet minus the valve drop.
  - Header: a small volume; the users draw from it.
  - Burner (one per firing zone): the flow through the burner's gas valve and orifice goes with
    the square root of the pressure difference (dp = k Q^2, turbulent orifice), so a burner set
    for its rated pressure fires less when the header pressure falls. Heat = flow x heating value
    x combustion efficiency. A low-gas-pressure switch shuts the safety valves below its set point
    (required by EN 746-2 and NFPA 86 on industrial furnaces) and the burner locks out until a
    reset.

Gas is treated as incompressible across a burner (low pressures, a few kPa), a standard first
approximation for low-pressure gas trains.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GasSupplyRating:
    p_supply_kpa: float        # line pressure upstream of the regulator, kPa(g)
    p_set_kpa: float           # regulator outlet set pressure, kPa(g)
    droop_kpa_per_m3h: float   # outlet fall per unit flow (regulator droop)
    dp_min_kpa: float          # minimum inlet - outlet difference for regulation
    k_open: float              # wide-open valve resistance, kPa / (m3/h)^2
    lhv_mj_m3: float           # lower heating value of the gas, MJ per standard m3
    source: str = ""


class GasSupply:
    def __init__(self, name, rating):
        self.name, self.r = name, rating
        self.p_supply = rating.p_supply_kpa
        self.p_out = rating.p_set_kpa
        self.flow_m3h = 0.0
        self.burners = []

    def outlet(self, flow_m3h):
        r = self.r
        regulated = r.p_set_kpa - r.droop_kpa_per_m3h * flow_m3h
        wide_open = self.p_supply - r.k_open * flow_m3h ** 2
        if self.p_supply - regulated >= r.dp_min_kpa:
            return max(0.0, min(regulated, wide_open))
        return max(0.0, min(wide_open, self.p_supply - r.dp_min_kpa))

    def step(self, dt):
        """Solve the header pressure against the burners' demand (fixed point, few iterations)."""
        p = self.p_out
        for _ in range(30):
            flow = sum(b.flow_at(p) for b in self.burners)
            p_new = self.outlet(flow)
            if abs(p_new - p) < 1e-6:
                break
            p = 0.5 * (p + p_new)
        self.p_out, self.flow_m3h = p, sum(b.flow_at(p) for b in self.burners)
        for b in self.burners:
            b.update(p, dt)
        return p

    def heat_w(self):
        return sum(b.heat_w for b in self.burners)


@dataclass(frozen=True)
class BurnerRating:
    q_rated_kw: float          # firing rate at the rated gas pressure
    p_rated_kpa: float         # gas pressure at the burner inlet for the rated firing rate
    eff: float                 # combustion efficiency share of LHV that reaches the load and walls
    p_low_trip_kpa: float      # low gas pressure switch set point
    source: str = ""


class Burner:
    """One firing zone. `firing` (0..1) is the control valve opening set by its temperature
    controller (modulating). The flow at full fire and rated pressure gives the rated input."""

    def __init__(self, name, rating, lhv_mj_m3):
        self.name, self.r, self.lhv = name, rating, lhv_mj_m3
        self.q_n_m3h = rating.q_rated_kw * 3.6 / lhv_mj_m3     # kW -> MJ/h -> m3/h
        self.k = rating.p_rated_kpa / self.q_n_m3h ** 2
        self.firing = 0.0
        self.enabled = True
        self.locked_out = False
        self.flow_m3h = 0.0
        self.heat_w = 0.0

    def flow_at(self, p_kpa):
        if not self.enabled or self.locked_out or self.firing <= 0.0 or p_kpa <= 0.0:
            return 0.0
        k = self.k / max(self.firing, 1e-3) ** 2              # the valve opening scales the area
        return math.sqrt(p_kpa / k)

    def update(self, p_kpa, dt):
        if self.enabled and self.firing > 0.0 and p_kpa < self.r.p_low_trip_kpa:
            self.locked_out = True                             # low gas pressure switch
        self.flow_m3h = self.flow_at(p_kpa)
        self.heat_w = self.flow_m3h * self.lhv / 3.6 * 1000.0 * self.r.eff

    def reset(self):
        self.locked_out = False
