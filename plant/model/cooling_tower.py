"""The process cooling of cool-1 (operator's conventional choice, 2026-10-08): an induced-draft
counterflow cooling tower with a gasketed plate heat exchanger between the open tower water and
the closed process loop (the plate exchanger keeps the machine coolers clean).

Cooling tower: Merkel's equation on the fill,
    KaV/L = integral from T_cold to T_hot of cp dT / (h_sat(T) - h_air(T)),
    h_air(T) = h_air,in + (L/G) cp (T - T_cold),  h_air,in = h_sat(T_wet bulb),
evaluated with the 4-point Chebyshev rule (0.1, 0.4, 0.6, 0.9 of the range) of the CTI and BEE
tower material. The fill characteristic follows KaV/L = C (L/G)^n with n = -0.6 (SPX Marley
TB-R61: the exponent runs -0.35 to -1.1, mostly -0.55 to -0.65); C comes from the maker's rating.
Saturated air enthalpy: the Magnus form of the saturation pressure (Alduchov and Eskridge 1996)
and h = 1.006 T + w (2501 + 1.86 T) kJ/kg dry air at 101.325 kPa.
Fan control (the conventional thermostat): the fans run while the basin is above its set point
and stop 1 K under it; with the fans stopped a natural draft of 15 % of the air flow remains
(project choice). Fill fouling (F9) lowers the characteristic C: Brentwood measured 18 % loss of
capability on a fouled film pack.

Plate heat exchanger: counterflow effectiveness-NTU (heat.py) from one design duty.
Basin: a lumped water mass; the tower pump runs at its fixed flow.
Water loss: evaporation = 0.00085 x 1.8 x flow x range (BEE guidebook, chapter 7).
"""
import math
from dataclasses import dataclass

from ._jit import JIT, njit
from .heat import CP_WATER, HeatExchanger

P_ATM_KPA = 101.325


def p_sat_kpa(t_c):
    """Saturation vapour pressure over water, Magnus form (Alduchov and Eskridge 1996)."""
    return 0.61094 * math.exp(17.625 * t_c / (t_c + 243.04))


def h_sat(t_c):
    """Enthalpy of saturated air, kJ per kg of dry air."""
    p = p_sat_kpa(t_c)
    w = 0.622 * p / (P_ATM_KPA - p)
    return 1.006 * t_c + w * (2501.0 + 1.86 * t_c)


def merkel(t_hot, t_cold, t_wb, l_over_g):
    """KaV/L by the 4-point Chebyshev rule; inf when the air would saturate past the water."""
    return _merkel_h(t_hot, t_cold, h_sat(t_wb), l_over_g)


def _merkel_h(t_hot, t_cold, h_in, l_over_g):
    """merkel() with the inlet air enthalpy h_in given. h_sat is written out here with the same
    operations in the same order (the same value, bit for bit), as this is the plant's hottest
    function: the tower solve calls it about 25 times a tick."""
    rng = t_hot - t_cold
    if rng <= 0:
        return 0.0
    cp = CP_WATER / 1000.0
    s = 0.0
    for f in (0.1, 0.4, 0.6, 0.9):
        t = t_cold + f * rng
        p = 0.61094 * math.exp(17.625 * t / (t + 243.04))
        w = 0.622 * p / (P_ATM_KPA - p)
        dh = (1.006 * t + w * (2501.0 + 1.86 * t)) - (h_in + l_over_g * cp * (t - t_cold))
        if dh <= 0:
            return math.inf
        s += 1.0 / dh
    return cp * rng / 4.0 * s


_merkel_k = njit(_merkel_h)


@njit
def _bisect_k(t_hot, h_in, lg, target, lo, hi, steps):
    """The plain bisection of CoolingTower.cold_water, compiled (plant/model/_jit.py)."""
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        if _merkel_k(t_hot, mid, h_in, lg) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# The tower solve below gives the same answer as a plain 60-step bisection, bit for bit, with fewer
# evaluations (2026-10-16, speed only). A bisection's answer depends only on the outcome of each
# comparison merkel(mid) > target. merkel falls as the cold water rises, so once the root is known
# to lie between a and b, where the comparison is clearly true at a and clearly false at b, every
# mid below a is true and every mid above b is false without evaluating it. Only the mids between
# a and b are evaluated. "Clearly": merkel - target beyond BRACKET_MARGIN, about 60 x the largest
# rounding noise of merkel near the root (1.6e-12 over 300 random towers, 2026-10-16). The root
# comes from a secant solve started at the last tick's answer. When anything fails (no last answer,
# an infinite merkel, no clear bracket) the plain bisection runs.
BRACKET_MARGIN = 1e-10
BISECTION_STEPS = 60


@dataclass(frozen=True)
class TowerRating:
    flow_m3h: float           # rated water flow
    t_hot: float              # rated hot water, C
    t_cold: float             # rated cold water, C
    t_wb: float               # rated wet bulb, C
    air_kgs: float            # air mass flow at full fan
    fan_kw: float             # fan motor power (all fans)
    n_exp: float = -0.6
    natural_draft: float = 0.15
    basin_m3: float = 6.0
    source: str = ""

    @property
    def lg(self):
        return self.flow_m3h / 3600.0 * 997.0 / self.air_kgs

    @property
    def c_char(self):
        return merkel(self.t_hot, self.t_cold, self.t_wb, self.lg) / self.lg ** self.n_exp


class CoolingTower:
    def __init__(self, name, rating, t_wb=28.0, setpoint_c=29.0, t0=None):
        self.name, self.r = name, rating
        self.t_wb = t_wb
        self.setpoint = setpoint_c
        self.fouling = 0.0                # F9: share of the fill characteristic lost
        self.fan_on = True
        self.t_basin = t0 if t0 is not None else setpoint_c
        self.t_hot = self.t_cold = self.t_basin
        self.q_w = 0.0
        self.evap_m3h = 0.0
        self._c_char = rating.c_char      # the rating is frozen: compute its characteristic once
        self._h_wb = (None, 0.0)          # (wet bulb, h_sat of it)
        self._last = None                 # the last solve's answer, the start of the next one

    def kav_l(self, lg):
        return self._c_char * lg ** self.r.n_exp * (1.0 - self.fouling)

    def cold_water(self, t_hot, m_kgs, air_kgs):
        """Cold water leaving the fill for this hot water, water flow and air flow: the root of
        merkel(t_hot, t_cold) = KaV/L, as a 60-step bisection on [wet bulb + 1e-4, t_hot] finds it
        (see BRACKET_MARGIN for how most of its evaluations are skipped)."""
        lg = m_kgs / air_kgs
        target = self.kav_l(lg)
        if self._h_wb[0] != self.t_wb:
            self._h_wb = (self.t_wb, h_sat(self.t_wb))
        h_in = self._h_wb[1]
        lo, hi = self.t_wb + 1e-4, t_hot
        if JIT:                    # compiled: the plain bisection is fast enough
            self._last = _bisect_k(t_hot, h_in, lg, target, lo, hi, BISECTION_STEPS)
            return self._last
        a, b = self._bracket(t_hot, h_in, lg, target, lo, hi)
        memo = {}
        for _ in range(BISECTION_STEPS):
            mid = 0.5 * (lo + hi)
            if mid <= a:
                up = True
            elif mid >= b:
                up = False
            else:
                up = memo.get(mid)
                if up is None:
                    up = memo[mid] = _merkel_h(t_hot, mid, h_in, lg) > target
            if up:
                lo = mid           # needs less transfer: the water leaves warmer
            else:
                hi = mid
        self._last = 0.5 * (lo + hi)
        return self._last

    def _bracket(self, t_hot, h_in, lg, target, lo, hi):
        """(a, b) around the root with merkel - target > margin at a and < -margin at b; (-inf,
        inf) when there is none (then every mid is evaluated: the plain bisection)."""
        none = (-math.inf, math.inf)
        x1 = self._last
        if x1 is None or not lo < x1 < hi:
            return none
        f = lambda t: _merkel_h(t_hot, t, h_in, lg) - target        # noqa: E731
        x0 = x1 + 1e-3 if x1 + 1e-3 < hi else 0.5 * (x1 + hi)
        f0, f1 = f(x0), f(x1)
        for _ in range(12):                                         # secant
            if not (math.isfinite(f0) and math.isfinite(f1)) or f1 == f0:
                return none
            x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
            if not lo < x2 < hi:
                return none
            x0, f0, x1 = x1, f1, x2
            f1 = f(x1)
            if abs(x1 - x0) < 1e-12 * max(1.0, abs(x1)):
                break
        margin = BRACKET_MARGIN * max(1.0, abs(target))
        slope = abs((f1 - f0) / (x1 - x0)) if x1 != x0 else 0.0
        d = max(4.0 * margin / slope if slope > 0 else 1e-9, 1e-13 * max(1.0, abs(x1)))
        for _ in range(5):
            a, b = x1 - d, x1 + d
            if lo < a and b < hi:
                fa, fb = f(a), f(b)
                if fa > margin and fb < -margin:
                    return a, b
            d *= 8.0
        return none

    def step(self, dt, q_in_w, m_kgs):
        """The tower pump takes basin water through the plate exchanger (q_in_w added) to the fill."""
        if self.t_basin > self.setpoint:
            self.fan_on = True
        elif self.t_basin < self.setpoint - 1.0:
            self.fan_on = False
        air = self.r.air_kgs * (1.0 if self.fan_on else self.r.natural_draft)
        self.t_hot = self.t_basin + q_in_w / (m_kgs * CP_WATER)
        self.t_cold = self.cold_water(self.t_hot, m_kgs, air) if self.t_hot > self.t_wb + 1e-3 else self.t_hot
        self.q_w = m_kgs * CP_WATER * (self.t_hot - self.t_cold)
        c_b = self.r.basin_m3 * 997.0 * CP_WATER
        a = math.exp(-m_kgs * CP_WATER * dt / c_b)
        self.t_basin = self.t_cold + (self.t_basin - self.t_cold) * a
        self.evap_m3h = 0.00085 * 1.8 * (m_kgs * 3.6) * (self.t_hot - self.t_cold)
        return self.q_w

    def fan_kw(self):
        return self.r.fan_kw if self.fan_on else 0.0


class TowerSystem:
    """The tower and the plate exchanger as the cooler of cool-1: CoolingLoop calls remove()."""

    def __init__(self, name, tower_rating, phe_design, t_wb=28.0, setpoint_c=29.0):
        self.name = name
        self.tower = CoolingTower(f"{name}-tower", tower_rating, t_wb=t_wb, setpoint_c=setpoint_c)
        d = phe_design
        self.m_tower = tower_rating.flow_m3h / 3600.0 * 997.0
        self.phe = HeatExchanger(f"{name}-phe", d["m_process"], CP_WATER, d["t_process_in"],
                                 d["t_process_out"], self.m_tower, CP_WATER, d["t_tower_in"])
        self.q_w = 0.0
        self.running = True               # the tower pump

    def remove(self, t_in, m_kgs, dt):
        if not self.running or m_kgs <= 0:
            self.q_w = 0.0
            self.tower.step(dt, 0.0, self.m_tower)
            return 0.0
        q = max(0.0, self.phe.step(t_in, m_kgs, self.tower.t_basin, self.m_tower))
        self.tower.step(dt, q, self.m_tower)
        self.q_w = q
        return q

    def check(self):
        out = []
        t = self.tower
        if t.t_cold < t.t_wb - 1e-6:
            out.append(f"{self.name}: cold water under the wet bulb")
        if t.q_w < -1e-6:
            out.append(f"{self.name}: the tower adds heat")
        return out
