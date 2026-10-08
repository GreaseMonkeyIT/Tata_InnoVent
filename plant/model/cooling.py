"""cool-1: the process cooling-water loop (ideas.md 11.7).

A closed loop: the pump drives the water through the chiller's evaporator into the supply header,
through the users in parallel branches, into the return header and back to the pump.

Hydraulics (steady each step):
  - Each user branch has a resistance k_i (dp = k_i q_i^2, in metres of head). Parallel branches
    share the header pressure difference, so q_i = sqrt(H_branch / k_i). The parallel branches
    act as one resistance k_par = 1 / (sum 1/sqrt(k_i))^2. The headers, the evaporator and the
    piping add a series resistance k_ser. The pump curve meets H = (k_ser + k_par) Q^2 (a closed
    loop has no static head).
  - A branch valve (0..1) multiplies the branch flow area: k_i / valve^2.

Heat (lumped):
  - Two water masses, the supply header and the return header, each with half the loop water.
  - The users take water at the supply temperature and give back heat q_i (each user computes its
    own q_i from its heat exchanger, so a user with less flow or warmer water removes less heat).
  - The chiller cools the return water toward its setpoint, as far as its capacity allows.
  - The pump's shaft power ends up in the water as heat (a closed loop).

The loop gives each user its supply temperature and its flow; the plant asks each user for its
heat. Faults (parameters only): pump wear (pump.py), a stuck or closed branch valve, a fouled user
exchanger (heat.py).
"""
import math
from dataclasses import dataclass

from .heat import CP_WATER, RHO_WATER


@dataclass
class Branch:
    """One user branch. `heat(t_supply, m_kgs) -> W` is the user's heat into the water."""
    name: str
    k: float                   # branch resistance, m / (m3/s)^2, valve fully open
    heat: object = None        # callable(t_supply_c, m_kgs) -> W into the water
    valve: float = 1.0
    q_m3s: float = 0.0
    q_w: float = 0.0

    def k_eff(self):
        return self.k / max(self.valve, 1e-3) ** 2


class CoolingLoop:
    def __init__(self, name, pump, w_n_frac=1.0, volume_m3=3.0, k_series=0.0, t0=28.0):
        self.name, self.pump = name, pump
        self.n_frac = w_n_frac             # pump speed share (a fixed-speed pump: 1.0)
        self.volume = volume_m3
        self.k_ser = k_series
        self.branches = []
        self.chiller = None                # object with remove(t_in, m_kgs, dt) -> W removed
        self.t_supply = self.t_return = t0
        self.q_m3s = 0.0
        self.q_users = 0.0
        self.q_chiller = 0.0
        self.running = True

    def add(self, branch):
        self.branches.append(branch)
        return branch

    def k_parallel(self):
        s = sum(1.0 / math.sqrt(b.k_eff()) for b in self.branches if b.valve > 0.0)
        return 1.0 / s ** 2 if s > 0 else float("inf")

    def step(self, dt):
        # hydraulics
        if self.running and self.n_frac > 0.0:
            k_tot = self.k_ser + self.k_parallel()
            self.q_m3s = self.pump.operate(self.n_frac, 0.0, k_tot) if math.isfinite(k_tot) else 0.0
        else:
            self.q_m3s = 0.0
            self.pump.q, self.pump.p_shaft = 0.0, 0.0
        h_par = self.k_parallel() * self.q_m3s ** 2 if math.isfinite(self.k_parallel()) else 0.0
        for b in self.branches:
            b.q_m3s = math.sqrt(h_par / b.k_eff()) if b.valve > 0.0 and h_par > 0 else 0.0
        # users at the supply temperature
        self.q_users = 0.0
        for b in self.branches:
            m = b.q_m3s * RHO_WATER
            b.q_w = b.heat(self.t_supply, m) if (b.heat and m > 0) else 0.0
            self.q_users += b.q_w
        m_tot = self.q_m3s * RHO_WATER
        mass_half = 0.5 * self.volume * RHO_WATER
        c_half = mass_half * CP_WATER
        # return header: mixes the user outlets (supply temperature + their heat) and the pump heat
        q_pump = self.pump.p_shaft
        if m_tot > 0:
            t_mix = self.t_supply + (self.q_users + q_pump) / (m_tot * CP_WATER)
            a = math.exp(-m_tot * dt / mass_half)
            self.t_return = t_mix + (self.t_return - t_mix) * a
        else:
            self.t_return += (self.q_users + q_pump) * dt / c_half
        # chiller on the way from the return header to the supply header
        self.q_chiller = self.chiller.remove(self.t_return, m_tot, dt) if (self.chiller and m_tot > 0) else 0.0
        if m_tot > 0:
            t_leave = self.t_return - self.q_chiller / (m_tot * CP_WATER)
            a = math.exp(-m_tot * dt / mass_half)
            self.t_supply = t_leave + (self.t_supply - t_leave) * a
        return self.q_m3s

    def heat_content(self, t_ref=0.0):
        """Heat stored in the loop water above t_ref, J (for the energy balance)."""
        c_half = 0.5 * self.volume * RHO_WATER * CP_WATER
        return c_half * (self.t_supply - t_ref) + c_half * (self.t_return - t_ref)

    def check(self):
        out = []
        if self.q_m3s < 0 or any(b.q_m3s < 0 for b in self.branches):
            out.append(f"{self.name}: a negative flow")
        if not (0.0 < self.t_supply < 100.0 and 0.0 < self.t_return < 100.0):
            out.append(f"{self.name}: loop water outside 0 to 100 C")
        return out
