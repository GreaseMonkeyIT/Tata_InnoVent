"""Three phases from symmetrical components (task 24, 2026-10-28): each phase as a phasor, the supply
unbalance, and the negative sequence of the network.

The network of power.py carries one phasor per bus: the positive sequence (a balanced plant). This
module adds the negative sequence, solved on the same radial network after each load flow, and builds
each phase from the two (Fortescue):

    Va = V1 + V2,   Vb = a^2 V1 + a V2,   Vc = a V1 + a^2 V2,   a = exp(j 120 deg)

There is no zero sequence: the LV side is three-wire for the motors, and a Dyn11 transformer does not pass
it from the 11 kV side. The unbalance factor is u2 = |V2| / |V1| (EN 50160 and IEC 61000-4-30 take the
negative to positive sequence ratio; EN 50160: 95 % of the 10 min values under 2 % on LV and MV).

The negative sequence comes from the supply (`supply_unbalance`: the source EMF's negative sequence as a
share of its positive sequence, at an angle): an upstream unbalance, as a real 11 kV supply has. In the
negative sequence:
  - the grid source, the cables and the transformers have their positive-sequence impedance (static
    devices: Z2 = Z1);
  - an induction motor is its own circuit at slip 2 - s: the negative-sequence field turns against the
    rotor, so the rotor resistances go with 1 / (2 - s), near the locked-rotor value (SEL, as cited in a
    Control.com thread; US patent 4,025,834). motor.py's double cage at 2 - s gives it, at 1 % voltage so
    that no leakage saturation acts. So a motor draws a negative-sequence current near its starting
    current times u2: the 6 to 10 times current unbalance of a motor (the rule of thumb behind the DOE
    tip sheet 7);
  - every other load is a constant impedance at its present power and voltage (project choice), except a
    load that feeds power back (hot-1's active front end controls its currents: none).

Outputs per bus (the 11 kV bus at incomer-1 and the four MCC buses): the three phase-to-neutral
voltages (magnitude and angle), |V2| and u2 in %; at incomer-1 and at every motor feeder: the three line
currents and |I2|, and at a motor feeder the measured Z2 = V2 / I2 (magnitude and angle). With a balanced
supply V2 = I2 = 0 and the phases are the positive sequence turned by 0, -120 and +120 degrees.

The negative sequence does not act back on the plant (project choice, a known limit): the extra rotor
and stator losses of I2 do not heat the motors (DOE tip sheet 7: the rise grows by 2 u2^2 per cent),
and its braking torque is left out. The positive sequence, every machine and every existing tag stay as
they are, whatever the unbalance.
"""
import cmath
import math

from .motor import solve

A = cmath.exp(2j * math.pi / 3.0)
A2 = A * A
SQRT3 = math.sqrt(3.0)


def phases(x1, x2):
    """(a, b, c) phasors from the positive and negative sequence."""
    return x1 + x2, A2 * x1 + A * x2, A * x1 + A2 * x2


def motor_y2(motor, f_hz):
    """The negative-sequence admittance (per phase, star equivalent) of a running motor, 0 when it is
    not on the supply."""
    if motor.amps <= 0.0:
        return 0j
    r = motor.rating
    v = 0.01 * r.v_ll                                  # 1 %: no leakage saturation in the circuit
    op = solve(motor._params(), r, 2.0 - motor.slip, v, f_hz, motor.t_wind)
    return op.i1 / (v / SQRT3)


def load_y2(ld, v_ph, f_hz):
    """The negative-sequence admittance of one bus load at the bus's positive-sequence voltage."""
    m = getattr(ld, "model", None)
    if m is not None and hasattr(m, "rating") and hasattr(m, "slip"):
        return getattr(ld, "count", 1) * motor_y2(m, f_hz)
    s = ld.s_va()
    if s.real <= 0.0 or abs(v_ph) < 1e-6:
        return 0j
    return s.conjugate() / (3.0 * abs(v_ph) ** 2)


class NegativeSequence:
    """The negative sequence of a network for a supply unbalance. `solve()` after each load flow gives
    v2 per bus, i2 per branch (child side) and per load."""

    def __init__(self, net, unbalance=0.0, angle_deg=0.0):
        self.net = net
        self.unbalance, self.angle_deg = unbalance, angle_deg
        self.v2, self.i2_branch, self.i2_load, self.y2_load = {}, {}, {}, {}
        self.i2_source = 0j

    def solve(self):
        net = self.net
        g = net.grid
        buses, order = net.buses(), net._order()
        f = g.f_hz
        e1 = g.e_pu * g.rating.v_ll / SQRT3
        e2 = self.unbalance * e1 * cmath.exp(1j * math.radians(self.angle_deg))
        y_load, y_in = {}, {}
        for b in buses:
            y = 0j
            for ld in b.loads:
                yl = load_y2(ld, b.v, f)
                self.y2_load[id(ld)] = yl
                y += yl
            y_load[id(b)] = y
            y_in[id(b)] = y
        for br in reversed(order):                     # the admittance each bus sees below it
            yc = y_in[id(br.child)]
            yb = 0j if yc == 0 else 1.0 / (br.z + 1.0 / yc)
            sh = br.shunt_s()
            ysh = sh.conjugate() / (3.0 * abs(br.parent.v) ** 2) if abs(br.parent.v) > 1e-6 else 0j
            y_in[id(br.parent)] += yb / br.ratio ** 2 + ysh
        root = net.root
        v2 = {id(root): e2 / (1.0 + g.z * y_in[id(root)])}
        self.i2_source = v2[id(root)] * y_in[id(root)]
        i2b = {}
        for br in order:                               # from the source down
            vp = v2[id(br.parent)] / br.ratio
            yc = y_in[id(br.child)]
            i = 0j if yc == 0 else vp / (br.z + 1.0 / yc)
            i2b[id(br)] = i
            v2[id(br.child)] = vp - br.z * i
        self.v2, self.i2_branch = v2, i2b
        self.i2_load = {}
        for b in buses:
            for ld in b.loads:
                self.i2_load[id(ld)] = v2[id(b)] * self.y2_load[id(ld)]
        return self


def bus_tags(name, bus, v2):
    """Phase voltages (phase to neutral), |V2| and u2 of a bus."""
    va, vb, vc = phases(bus.v, v2)
    v1 = abs(bus.v)
    out = {}
    for ph, v in zip("abc", (va, vb, vc)):
        out[f"{name}.v{ph}_v"] = abs(v)
        out[f"{name}.v{ph}_deg"] = math.degrees(cmath.phase(v))
    out[f"{name}.v2_v"] = abs(v2)
    out[f"{name}.unbalance_pct"] = 100.0 * abs(v2) / v1 if v1 > 0 else 0.0
    return out


def current_tags(name, i1, i2, v2=None):
    """Line currents, |I2| and, given V2, the measured Z2 = V2 / I2."""
    out = {f"{name}.i{ph}_a": abs(i) for ph, i in zip("abc", phases(i1, i2))}
    out[f"{name}.i2_a"] = abs(i2)
    if v2 is not None:
        z2 = v2 / i2 if abs(i2) > 1e-9 else complex(float("nan"), float("nan"))
        out[f"{name}.z2_ohm"] = abs(z2)
        out[f"{name}.z2_deg"] = math.degrees(cmath.phase(z2)) if abs(i2) > 1e-9 else float("nan")
    return out
