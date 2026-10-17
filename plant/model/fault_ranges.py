"""The magnitude range of each fault of the registry (faults.py), by ONE rule (2026-10-16).

Rule: a trial draws a severity s, uniform in (0, 1], and sets
    magnitude = healthy + s x (end - healthy).
The end (s = 1) is never a hand-picked number. It is, in this order of preference:
  1. where the plant's own physics or protection acts, computed from the models here: the relief
     valve, a pump that stalls, the low-gas switch, a compressor that cannot keep up, the 50 C limit
     of the compressor's cooling water;
  2. a published limit or level (IEC 61000-4-34 sag levels, a measured fouling loss, a steel grade);
  3. a physical bound (a water tank at atmospheric pressure).
Two ends have no such anchor yet; they are marked "project choice" for the operator's review.
Binary faults (a jam, a drive trip) have no range.

    python -m model.fault_ranges        (from plant/: prints the table)
"""
import math

from . import catalog as C
from .press_brake import nu_vg46


def _bisect(f, lo, hi, n=60):
    """The root of an increasing f on [lo, hi] (f(lo) < 0 < f(hi))."""
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0.0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def ends(p):
    """{fid: (healthy, end, how)} for plant p (a UtilitiesPlant)."""
    out = {}
    pr = p.press
    r = pr.r
    a = r.area_m2
    # the hardest, thickest plate of the batches (S355J2 UTS 630 MPa, 8 + 1.2 mm)
    from .press_brake import Plate
    hard = Plate(uts_mpa=630.0, t_mm=9.2, length_m=pr.plate.length_m, v_mm=pr.plate.v_mm)
    f_bend = hard.force_kn() * 1e3
    p_relief = (r.p_max_bar + r.p_relief_margin_bar) * 1e5
    # F1: the extra guide friction at which the hardest bend reaches the relief valve
    fr_end = p_relief * a - f_bend + r.ram_mass_kg * 9.81 - pr.friction_n
    out["F1"] = (0.0, fr_end / (r.force_kn * 1e3), "press-1 relief valve on the hardest plate")
    # F2: the leakage factor at which the pump flow is all leakage at that bend pressure (a stall),
    # with the oil at its 45 C thermostat point
    p_b = min(p_relief, (f_bend + pr.friction_n - r.ram_mass_kg * 9.81) / a)
    q_th = pr.disp_m3 * pr.w_n / (2 * math.pi)
    leak1 = pr.c_leak * p_b / nu_vg46(45.0)
    out["F2"] = (1.0, q_th / leak1, "press-1 pump flow all leakage at the hardest bend (stall)")
    # F4: the wear rate at which a punch does not last one member of the most holes
    if p.fab:
        out["F4"] = (1.0, C.PUNCH_450.life_hits / C.SIDE_MEMBER.holes_max,
                     "pierce-1 punch worn out within one member")
    # F5: the model's own scale (1 = a badly worn contact tip)
    out["F5"] = (0.0, 1.0, "weld cell model scale")
    # F6: the leak at which users + leak take the whole free air delivery at the load pressure
    comp = p.comp
    p_load = C.GA37W_7_5.load_barg + 1.013
    fad = C.GA37W_7_5.fad_m3min / 60.0
    users = sum(d(p_load) for d in comp.demands[:1])        # the plant air users (not the plasma)
    leak_h = p._leak_healthy * p_load
    out["F6"] = (1.0, (fad - users) / leak_h, "compressor-1 cannot keep the load pressure")
    # F7: any reading under the load pressure keeps the compressor loaded; the end is a dead zero
    out["F7"] = (C.GA37W_7_5.load_barg, 0.0, "transducer reading from the load pressure down to zero")
    # F8: the pump wear at which compressor-1's cooling water leaves at 50 C (Atlas Copco maximum)
    loop = p.loop
    k_tot = loop.k_ser + loop.k_parallel()
    q_n = loop.pump.operate(1.0, 0.0, k_tot)
    q_heat = 0.91 * C.GA37W_7_5.p_shaft_kw * 1000.0           # oil + air heat to the water at load
    m_need = q_heat / (4186.0 * (50.0 - 31.0))                 # from a 31 C supply
    # compressor-1's share of the loop flow: parallel branches split as 1 / sqrt(k) (the open ones)
    open_b = [b for b in loop.branches if b.valve > 0.0]
    share = (1.0 / math.sqrt(p.b_comp.k_eff())) / sum(1.0 / math.sqrt(b.k_eff()) for b in open_b)
    def flow_short(w):
        loop.pump.wear = w
        q = loop.pump.operate(1.0, 0.0, k_tot)
        loop.pump.wear = 0.0
        return m_need - q * share * 997.0
    out["F8"] = (0.0, _bisect(flow_short, 0.0, 0.99), "compressor-1 cooling water out at 50 C")
    # F9: the measured loss of a fouled film fill (Brentwood: 18 %)
    out["F9"] = (0.0, 0.18, "Brentwood: a fouled film pack lost 18 % of its capability")
    # F10: the supply pressure at which the header falls to the burners' low-gas switch
    g = p.gas
    trip = min(b.r.p_low_trip_kpa for b in g.burners) if g.burners else 7.5
    flow = sum(b.q_n_m3h for b in g.burners)
    def header_ok(ps):
        g.p_supply = ps
        out_p = g.outlet(flow)
        g.p_supply = g.r.p_supply_kpa
        return out_p - trip
    out["F10"] = (g.r.p_supply_kpa, _bisect(header_ok, 0.0, g.r.p_supply_kpa),
                  "the gas header at the burners' low-gas switch, all burners firing")
    # F11: IEC 61000-4-34 class 3 residual levels (a draw picks one level, not a severity)
    out["F11"] = (1.0, 0.0, "IEC 61000-4-34 levels 0.8, 0.7, 0.4, 0 (draw a level)")
    # F13: a water tank at atmospheric pressure stays under about 95 C (setpoint 80 C)
    if p.asm:
        out["F13"] = (0.0, 95.0 - C.MECWASH.t_wash_c, "a water tank under 95 C")
        # F14: project choice: a heavy schedule rigs the next engine in a fifth of the time
        out["F14"] = (p.eflow.H_HOT if p.eflow else 300.0, (p.eflow.H_HOT if p.eflow else 300.0) * 0.2,
                      "project choice: rigging in a fifth of the normal time")
        # F16: project choice: up to twice the +-20 % reject band of the cold test
        out["F16"] = (0.0, 0.40, "project choice: twice the cold test reject band (+-20 %, US 9863847)")
    # F15: a plate of the next grade, S460 (UTS up to 720 MPa) instead of S355J2 (up to 630)
    out["F15"] = (630.0, 720.0, "S460 plate (UTS up to 720 MPa) on a line set for S355J2")
    return out


IEC_LEVELS = (0.8, 0.7, 0.4, 0.0)


def magnitude(fid, severity, table):
    healthy, end, _ = table[fid]
    if fid == "F11":
        return IEC_LEVELS[min(len(IEC_LEVELS) - 1, int(severity * len(IEC_LEVELS)))]
    return healthy + severity * (end - healthy)


if __name__ == "__main__":
    from .plant import UtilitiesPlant
    p = UtilitiesPlant()
    for fid, (h, e, how) in ends(p).items():
        print(f"{fid:4s} healthy {h:10.4g}  end {e:10.4g}  {how}")
