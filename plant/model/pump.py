"""Centrifugal pump and fan (ideas.md 11.3): pump curve, affinity laws, system curve.

Pump curve at speed n (rated speed n_N), a parabola through the shut-off head and the rated point:
    H(Q, n) = H0 (n / n_N)^2 - a Q^2,      a = (H0 - H_N) / Q_N^2
Efficiency, a parabola with its best point at the rated flow scaled with speed:
    eta(Q, n) = eta_N * (1 - ((Q / Q_bep(n)) - 1)^2),   Q_bep(n) = Q_N n / n_N
These keep the affinity laws exactly: Q ~ n, H ~ n^2, P ~ n^3 at similar points (any pump
handbook, for example the Grundfos Pump Handbook, chapter 1.4).
Shaft power: P = rho g Q H / eta, with a floor at shut-off (the disc friction keeps a pump that
runs against a closed valve at a share of its rated power).

System curve: H_sys = H_static + k Q^2. The operating point solves H(Q, n) = H_sys(Q).

Faults (parameters only): `wear` lowers the head and the efficiency of a worn impeller or a
widened wear-ring gap (head factor 1 - wear, efficiency factor 1 - wear / 2: project choice for
the split). A throttled or clogged circuit raises k.
"""
import math
from dataclasses import dataclass

G = 9.81


@dataclass(frozen=True)
class PumpRating:
    q_m3h: float          # rated flow
    h_m: float            # rated head
    eta: float            # pump efficiency at the rated point (the best efficiency point)
    n_rpm: float          # rated speed
    h0_m: float           # shut-off head
    rho: float = 997.0
    shutoff_frac: float = 0.4    # shaft power at shut-off / rated shaft power (project choice)
    source: str = ""

    @property
    def p_shaft_w(self):
        return self.rho * G * self.q_m3h / 3600.0 * self.h_m / self.eta


class Pump:
    def __init__(self, name, rating):
        self.name, self.r = name, rating
        self.wear = 0.0
        self.q = 0.0            # m3/s
        self.h = 0.0            # m
        self.p_shaft = 0.0      # W
        self.eta = 0.0

    def head(self, q, n_frac):
        r = self.r
        qn = r.q_m3h / 3600.0
        a = (r.h0_m - r.h_m) / qn ** 2
        return (r.h0_m * n_frac ** 2 - a * q ** 2) * (1.0 - self.wear)

    def efficiency(self, q, n_frac):
        r = self.r
        if n_frac <= 0.0:
            return 0.0
        q_bep = r.q_m3h / 3600.0 * n_frac
        e = r.eta * (1.0 - (q / q_bep - 1.0) ** 2) * (1.0 - 0.5 * self.wear)
        return max(e, 0.0)

    def shaft_power(self, q, h, n_frac):
        r = self.r
        p_min = r.shutoff_frac * r.p_shaft_w * n_frac ** 3
        eta = self.efficiency(q, n_frac)
        p_hyd = r.rho * G * q * h
        return max(p_min, p_hyd / eta if eta > 0.05 else p_min)

    def operate(self, n_frac, h_static, k_sys):
        """Operating point against H_sys = h_static + k_sys Q^2 (Q in m3/s). Sets q, h, eta,
        p_shaft and returns q."""
        r = self.r
        qn = r.q_m3h / 3600.0
        a = (r.h0_m - r.h_m) / qn ** 2 * (1.0 - self.wear)
        h0 = r.h0_m * n_frac ** 2 * (1.0 - self.wear)
        if n_frac <= 0.0 or h0 <= h_static:
            self.q, self.h = 0.0, max(h0, 0.0)
        else:
            self.q = math.sqrt((h0 - h_static) / (a + k_sys))
            self.h = h_static + k_sys * self.q ** 2
        self.eta = self.efficiency(self.q, n_frac)
        self.p_shaft = self.shaft_power(self.q, self.h, n_frac) if n_frac > 0 else 0.0
        return self.q

    def torque(self, w, w_n, h_static, k_sys):
        """Load torque on the drive at shaft speed w (rad/s): P_shaft(n) / w."""
        n_frac = max(w, 0.0) / w_n
        self.operate(n_frac, h_static, k_sys)
        return self.p_shaft / w if w > 1e-3 else 0.0
