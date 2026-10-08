"""Shared thermal parts (ideas.md 11.3): a lumped thermal mass and a heat exchanger.

Heat exchanger: the effectiveness-NTU method (Incropera and DeWitt, Fundamentals of Heat and Mass
Transfer, chapter 11). For counterflow:

    NTU = UA / C_min,  Cr = C_min / C_max
    eps = (1 - exp(-NTU (1 - Cr))) / (1 - Cr exp(-NTU (1 - Cr)))      (Cr < 1)
    eps = NTU / (1 + NTU)                                              (Cr = 1)
    q   = eps C_min (T_hot,in - T_cold,in)

C = m_dot c_p of each stream, W/K. UA is set from one rated duty (the datasheet point), so the
model matches the real exchanger there. UA follows the flows: each film coefficient goes with
(flow)^0.8 (Dittus-Boelter, turbulent), so 1/UA = a/m_hot^0.8 + b/m_cold^0.8 with the rated split
of the two films a project choice (equal). Fouling adds a resistance R_f (TEMA fouling factors):
1/UA_fouled = 1/UA_clean + R_f / A, here as a share of the clean resistance.
"""
import math

CP_WATER = 4186.0        # J/kg K
CP_OIL = 2000.0          # J/kg K, mineral compressor and hydraulic oil near 60 C (project choice, 1.9 to 2.1)
RHO_WATER = 997.0        # kg/m3
RHO_OIL = 870.0          # kg/m3


def eps_counterflow(ntu, cr):
    if ntu <= 0.0:
        return 0.0
    if abs(1.0 - cr) < 1e-9:
        return ntu / (1.0 + ntu)
    e = math.exp(-ntu * (1.0 - cr))
    return (1.0 - e) / (1.0 - cr * e)


class HeatExchanger:
    """A counterflow exchanger set from one rated duty.

    rated: hot inlet and outlet temperature, cold inlet temperature, both flows (kg/s) and the
    two specific heats. From these the clean UA follows. `fouling` is the extra thermal resistance
    as a share of the clean one (0 = clean, 0.5 = 50 % more resistance)."""

    def __init__(self, name, m_hot, cp_hot, t_hot_in, t_hot_out, m_cold, cp_cold, t_cold_in):
        self.name = name
        self.m_hot_n, self.m_cold_n = m_hot, m_cold
        self.cp_hot, self.cp_cold = cp_hot, cp_cold
        q = m_hot * cp_hot * (t_hot_in - t_hot_out)
        ch, cc = m_hot * cp_hot, m_cold * cp_cold
        cmin, cmax = min(ch, cc), max(ch, cc)
        eps = q / (cmin * (t_hot_in - t_cold_in))
        if not 0.0 < eps < 1.0:
            raise ValueError(f"{name}: rated duty gives effectiveness {eps:.3f}, not in (0, 1)")
        self.ua_n = cmin * self._ntu_from_eps(eps, cmin / cmax)
        self.fouling = 0.0
        self.q = 0.0
        self.t_hot_out = t_hot_out
        self.t_cold_out = t_cold_in + q / cc

    @staticmethod
    def _ntu_from_eps(eps, cr):
        lo, hi = 0.0, 50.0
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if eps_counterflow(mid, cr) < eps:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def ua(self, m_hot, m_cold):
        """UA at these flows: equal film resistances at the rated flows, each with flow^0.8."""
        if m_hot <= 0.0 or m_cold <= 0.0:
            return 0.0
        r_n = 1.0 / self.ua_n
        r = 0.5 * r_n * (self.m_hot_n / m_hot) ** 0.8 + 0.5 * r_n * (self.m_cold_n / m_cold) ** 0.8
        return 1.0 / (r + self.fouling * r_n)

    def step(self, t_hot_in, m_hot, t_cold_in, m_cold):
        """Steady duty at these inlets (the exchanger's own heat capacity is small next to the
        circuits it joins). Returns q in W (hot to cold)."""
        ch, cc = m_hot * self.cp_hot, m_cold * self.cp_cold
        if ch <= 0.0 or cc <= 0.0:
            self.q = 0.0
            self.t_hot_out, self.t_cold_out = t_hot_in, t_cold_in
            return 0.0
        cmin, cmax = min(ch, cc), max(ch, cc)
        eps = eps_counterflow(self.ua(m_hot, m_cold) / cmin, cmin / cmax)
        self.q = eps * cmin * (t_hot_in - t_cold_in)
        self.t_hot_out = t_hot_in - self.q / ch
        self.t_cold_out = t_cold_in + self.q / cc
        return self.q


class ThermalMass:
    """A lumped mass at one temperature: C dT/dt = q_in - q_out. step() integrates exactly for
    a net heat flow that is linear in T: q = q0 - g (T - t_ref)."""

    def __init__(self, name, c_j_k, t0):
        self.name, self.c, self.t = name, c_j_k, t0

    def step(self, dt, q0, g=0.0, t_ref=0.0):
        if g <= 0.0:
            self.t += q0 * dt / self.c
            return self.t
        t_inf = t_ref + q0 / g
        self.t = t_inf + (self.t - t_inf) * math.exp(-g * dt / self.c)
        return self.t
