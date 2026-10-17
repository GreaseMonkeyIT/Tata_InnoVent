"""The plant power network: the utility supply, the transformers, the LV feeders and the buses
(ideas.md 11.7: high-voltage incomer, TR-1, TR-2, rails).

The network is radial, as a plant supply is: grid -> MV bus -> transformer -> LV bus -> cable
feeder -> MCC bus (a "rail") -> loads. Each element is a series impedance per phase (star
equivalent, all values referred to the voltage of the bus they sit on). Each tick:

  1. every load gives its complex power S = P + jQ at the bus voltage of the last tick
  2. a backward sweep adds the load powers and the branch losses from the leaves to the grid
  3. a forward sweep computes each bus voltage from the grid down: V_child = V_parent - Z I
  4. two more sweeps with the same load powers converge the branch losses

A load that slows the network (a motor that draws more current at a low voltage) sees the new
voltage in the next tick. With a 20 ms tick that delay is one cycle.

Utility supply: a Thevenin source. Its impedance comes from the 3-phase fault level and the X/R
ratio. A voltage dip is a change of the source EMF with a slew limit (IEC 61000-4-34 test shapes).
A fault upstream reaches every transformer at the same moment.

Transformer: series impedance from the impedance voltage uk and the load loss (R = Pk / S, X from
uk), the no-load loss and the magnetizing current as a shunt at the primary. The thermal model is
the exponential (differential) model of IEC 60076-7 for top oil and hot spot.
"""
import cmath
import math
from dataclasses import dataclass

from ._jit import JIT, njit

if JIT:
    import numpy as np     # the compiled sweep's arrays (the Python path needs no numpy)

SQRT3 = math.sqrt(3.0)
# Python 3.14 sums complex numbers with Neumaier compensation, 3.11 adds them in order. The compiled
# sweep copies the rule of the Python that runs it (checked against sum() on both, 2026-10-16).
SUM_COMPENSATED = sum([1e16 + 0j, 1 + 0j, -1e16 + 0j], 0j) != 0


# ------------------------------------------------------------------------------- elements --
class Bus:
    """A node of the network. `v` is the phase-to-neutral voltage phasor, V. Loads attach here."""

    def __init__(self, name, v_nom_ll):
        self.name, self.v_nom_ll = name, v_nom_ll
        self.v = complex(v_nom_ll / SQRT3, 0.0)
        self.loads = []          # objects with .s_va() -> complex VA (3-phase), load convention
        self.children = []       # branches that leave this bus
        self.s_down = 0j         # total 3-phase power that flows into this bus from above, VA

    @property
    def v_ll(self):
        return abs(self.v) * SQRT3

    @property
    def v_pu(self):
        return self.v_ll / self.v_nom_ll

    def tags(self):
        return {"voltage_v": self.v_ll, "p_kw": self.s_down.real / 1000.0,
                "q_kvar": self.s_down.imag / 1000.0,
                "current_a": abs(self.s_down) / (SQRT3 * self.v_ll) if self.v_ll > 0 else 0.0}


class Branch:
    """A series impedance between two buses (a cable, a busbar, a transformer's series part).
    z is per phase, ohm, referred to the child bus voltage. `ratio` is the voltage ratio
    parent / child (1 for a cable)."""

    def __init__(self, name, parent, child, z, ratio=1.0):
        self.name, self.parent, self.child = name, parent, child
        self.z, self.ratio = complex(z), ratio
        parent.children.append(self)
        self.i = 0j              # child-side phase current, A
        self.s_loss = 0j         # 3-phase series loss, VA

    def shunt_s(self):
        """Power drawn by the branch's shunt part at the parent bus (none for a cable)."""
        return 0j

    def tags(self):
        return {"current_a": abs(self.i), "loss_kw": self.s_loss.real / 1000.0}


@dataclass(frozen=True)
class CableRating:
    """An LV cable per km at its operating temperature. Each instance names its source."""
    r_ohm_km: float
    x_ohm_km: float
    i_rated_a: float
    source: str = ""


class Cable(Branch):
    def __init__(self, name, parent, child, cable, length_m, runs=1):
        z = complex(cable.r_ohm_km, cable.x_ohm_km) * (length_m / 1000.0) / runs
        super().__init__(name, parent, child, z)
        self.cable, self.length_m, self.runs = cable, length_m, runs

    def loading(self):
        return abs(self.i) / (self.cable.i_rated_a * self.runs)


@dataclass(frozen=True)
class TransformerRating:
    """Nameplate and test data. Each instance names its source."""
    kva: float
    v1_ll: float             # primary rated voltage, V
    v2_ll: float             # secondary no-load voltage, V
    uk_pct: float            # impedance voltage, % (IEC 60076-1)
    p0_w: float              # no-load loss, W
    pk_w: float              # load loss at rated current, W (at reference temperature)
    i0_pct: float = 1.0      # no-load current, % of rated (project choice unless given)
    # IEC 60076-7 thermal data (ONAN distribution transformer values unless given)
    d_theta_or: float = 55.0     # top-oil rise at rated load, K
    h_gr: float = 23.0           # hot-spot to top-oil gradient at rated load, K (H * g_r)
    x_oil: float = 0.8           # oil exponent
    y_wdg: float = 1.6           # winding exponent
    k11: float = 1.0
    k21: float = 1.0
    k22: float = 2.0
    tau_o_min: float = 180.0     # oil time constant
    tau_w_min: float = 4.0       # winding time constant
    source: str = ""

    @property
    def i2_rated(self):
        return self.kva * 1000.0 / (SQRT3 * self.v2_ll)


class Transformer(Branch):
    """A two-winding transformer as a branch from the primary bus to the secondary bus. The
    series impedance is referred to the secondary. The no-load loss and the magnetizing current
    are a shunt at the primary that follows the voltage squared. Off-load tap: `tap` (1.0 =
    principal tap; 1.025 raises the secondary by 2.5 %)."""

    def __init__(self, name, parent, child, rating, tap=1.0):
        r = rating
        zb = r.v2_ll ** 2 / (r.kva * 1000.0)
        rk = r.pk_w / (r.kva * 1000.0)                 # per unit
        xk = math.sqrt(max((r.uk_pct / 100.0) ** 2 - rk ** 2, 0.0))
        super().__init__(name, parent, child, complex(rk, xk) * zb, ratio=r.v1_ll / r.v2_ll / tap)
        self.rating, self.tap = r, tap
        self.theta_amb = 35.0          # ambient air, C (project choice: Indian plant yard)
        self.theta_o = self.theta_amb  # top oil, C
        self._d1 = 0.0                 # the two parts of the hot-spot rise (IEC 60076-7 eq. 8-10)
        self._d2 = 0.0
        self.k = 0.0                   # load factor

    def shunt_s(self):
        r = self.rating
        u2 = (self.parent.v_ll / r.v1_ll) ** 2
        return complex(r.p0_w * u2, self.shunt_q0() * u2)

    def shunt_q0(self):
        """The magnetizing var at rated voltage (computed once per rating: the rating is frozen)."""
        r = self.rating
        q0 = self.__dict__.get("_q0")
        if q0 is None or q0[0] is not r:
            q0 = self._q0 = (r, math.sqrt(max((r.i0_pct / 100.0 * r.kva * 1000.0) ** 2 - r.p0_w ** 2, 0.0)))
        return q0[1]

    @property
    def theta_h(self):
        """Hot-spot temperature, C."""
        return self.theta_o + self._d1 - self._d2

    def step_thermal(self, dt):
        """IEC 60076-7 difference equations (clause 8.2.3), exact for each step with K held."""
        r = self.rating
        self.k = abs(self.i) / r.i2_rated
        R = r.pk_w / r.p0_w if r.p0_w > 0 else 6.0
        to = r.tau_o_min * 60.0
        tw = r.tau_w_min * 60.0
        d_o_inf = ((1.0 + self.k ** 2 * R) / (1.0 + R)) ** r.x_oil * r.d_theta_or
        target_o = self.theta_amb + d_o_inf
        self.theta_o = target_o + (self.theta_o - target_o) * math.exp(-dt / (r.k11 * to))
        d_h_inf = self.k ** r.y_wdg * r.h_gr
        self._d1 = r.k21 * d_h_inf + (self._d1 - r.k21 * d_h_inf) * math.exp(-dt / (r.k22 * tw))
        self._d2 = (r.k21 - 1.0) * d_h_inf + (self._d2 - (r.k21 - 1.0) * d_h_inf) * math.exp(
            -dt / (to / r.k22))

    def tags(self):
        t = super().tags()
        t.update({"load_pct": 100.0 * self.k, "oil_c": self.theta_o, "winding_c": self.theta_h})
        return t


# ------------------------------------------------------------------------------- the grid --
@dataclass(frozen=True)
class GridRating:
    v_ll: float              # nominal voltage, V
    fault_mva: float         # 3-phase short-circuit level at the incomer
    x_r: float               # X/R of the source
    f_hz: float = 50.0
    source: str = ""


class Grid:
    """The utility source behind the incomer: an EMF behind the source impedance. A dip sets a
    target EMF; the EMF moves toward it at `slew_pu_s` per second (an instant change when 0)."""

    def __init__(self, name, bus, rating):
        self.name, self.bus, self.rating = name, bus, rating
        zmag = rating.v_ll ** 2 / (rating.fault_mva * 1e6)
        ang = math.atan(rating.x_r)
        self.z = cmath.rect(zmag, ang)
        self.e_pu = 1.0              # source EMF, per unit
        self.target_pu = 1.0
        self.slew_pu_s = 0.0
        self.f_hz = rating.f_hz

    def step(self, dt):
        gap = self.target_pu - self.e_pu
        if self.slew_pu_s <= 0.0 or abs(gap) <= self.slew_pu_s * dt:
            self.e_pu = self.target_pu
        else:
            self.e_pu += math.copysign(self.slew_pu_s * dt, gap)

    def dip(self, residual_pu, slew_pu_s=0.0):
        """Start a voltage dip to `residual_pu` of nominal (IEC 61000-4-34 gives 0, 0.4, 0.7, 0.8
        residual levels). `clear()` ends it."""
        self.target_pu, self.slew_pu_s = residual_pu, slew_pu_s

    def clear(self):
        self.target_pu = 1.0


# ---------------------------------------------------------------------------- the network --
class Network:
    """A radial network with one grid source. `buses` must start at the grid bus; each branch
    joins a parent to a child."""

    def __init__(self, grid):
        self.grid = grid
        self.root = grid.bus
        self.branches = []

    def add(self, branch):
        self.branches.append(branch)
        return branch

    def _walk(self):
        """(buses, branches) in breadth-first order from the root. The walk is kept while the
        topology is the same (each bus keeps its number of branches): a new branch must leave a bus
        of the walk to be reachable, so a change always shows (2026-10-16, speed only)."""
        w = getattr(self, "_walk_cache", None)
        if w is not None and all(len(b.children) == n for b, n in zip(w[0], w[2])):
            return w
        buses, order, todo = [], [], [self.root]
        while todo:
            b = todo.pop(0)
            buses.append(b)
            for br in b.children:
                order.append(br)
                todo.append(br.child)
        self._walk_cache = (buses, order, [len(b.children) for b in buses])
        return self._walk_cache

    def _order(self):
        """Branches in order from the root (a breadth-first walk)."""
        return list(self._walk()[1])

    def buses(self):
        return list(self._walk()[0])

    def _kernel_ok(self, order):
        """The compiled sweep knows two shunts: none (Branch, Cable) and the transformer's."""
        return all(type(br).shunt_s is Branch.shunt_s or type(br).shunt_s is Transformer.shunt_s
                   for br in order)

    def _solve_k(self, buses, order, load_s, e, sweeps):
        """The sweeps of `solve`, compiled (_sweep_k): the same operations in the same order. The
        fixed data (the walk, impedances, ratios, transformer shunts) are packed once and rebuilt
        when one of those objects changes."""
        w = self._walk()
        refs = [self.grid.z] + [x for br in order for x in (br.z, br.ratio, getattr(br, "rating", None))]
        k = getattr(self, "_k_pack", None)
        if k is None or k[0] is not w or len(k[1]) != len(refs) or any(a is not b for a, b in zip(k[1], refs)):
            bi = {id(b): n for n, b in enumerate(buses)}
            ri = {id(br): j for j, br in enumerate(order)}
            cptr, cidx = [0], []
            for b in buses:
                cidx += [ri[id(br)] for br in b.children]
                cptr.append(len(cidx))
            nbr, nb = len(order), len(buses)
            idx = np.array([nbr, nb] + [bi[id(br.parent)] for br in order] + [bi[id(br.child)] for br in order]
                           + cptr + cidx, np.int64)
            tr = [isinstance(br, Transformer) for br in order]
            fs = np.array([br.ratio for br in order] + [float(t) for t in tr]
                          + [br.rating.p0_w if t else 0.0 for br, t in zip(order, tr)]
                          + [br.shunt_q0() if t else 0.0 for br, t in zip(order, tr)]
                          + [br.rating.v1_ll if t else 1.0 for br, t in zip(order, tr)] + [2.0, SQRT3], np.float64)
            cs = np.array([br.z for br in order] + [self.grid.z], np.complex128)
            k = self._k_pack = (w, refs, idx, fs, cs)
        dyn = np.array([b.v for b in buses] + load_s, np.complex128)
        out = _sweep_k(dyn, k[2], k[3], k[4], e, sweeps, SUM_COMPENSATED).tolist()
        nb = len(buses)
        for n, b in enumerate(buses):
            b.v, b.s_down = out[n], out[nb + n]
        for j, br in enumerate(order):
            br.i, br.s_loss = out[2 * nb + j], out[2 * nb + len(order) + j]

    def solve(self, sweeps=3):
        """Backward/forward sweep with the load powers of this tick.

        A deep dip (source EMF under 0.3 pu, an interruption) is not a load flow: the loads have
        dropped out (contactors, drive undervoltage). The buses then follow the source: the last
        healthy voltage profile scaled by the EMF, no branch current (a simplification for deep
        dips only; the sweep cannot converge with the source near zero)."""
        order = self._order()
        if self.grid.e_pu < 0.3:
            prof = getattr(self, "_profile", None) or {id(b): complex(b.v_nom_ll / SQRT3, 0.0)
                                                       for b in self.buses()}
            for b in self.buses():
                b.v = prof[id(b)] * self.grid.e_pu
                b.s_down = 0j
            for br in order:
                br.i, br.s_loss = 0j, 0j
            return self
        # Below 0.7 pu a constant-power load is treated as a constant impedance (S ~ V^2), the
        # usual load-model rule of power system tools: at a deep dip contactors drop out and
        # drives shut down, and no real load keeps its power at zero voltage.
        def k_low(bus):
            return min(1.0, (bus.v_pu / 0.7) ** 2)
        e = self.grid.e_pu * self.grid.rating.v_ll / SQRT3
        if JIT and self._kernel_ok(order):
            buses = self.buses()
            self._solve_k(buses, order, [sum((ld.s_va() for ld in b.loads), 0j) * k_low(b) for b in buses],
                          e, sweeps)
            if self.grid.e_pu > 0.9:
                self._profile = {id(b): b.v / self.grid.e_pu for b in buses}
            return self
        load_s = {id(b): sum((ld.s_va() for ld in b.loads), 0j) * k_low(b) for b in self.buses()}
        for _ in range(sweeps):
            s_in = {}
            for br in reversed(order):                       # backward: leaves first
                c = br.child
                s_c = load_s[id(c)] + sum((s_in[id(x)] for x in c.children), 0j)
                c.s_down = s_c
                i = (s_c / (3.0 * c.v)).conjugate() if abs(c.v) > 1e-6 else 0j
                br.i = i
                br.s_loss = 3.0 * br.z * abs(i) ** 2
                s_in[id(br)] = s_c + br.s_loss + br.shunt_s()
            root = self.root
            s_root = load_s[id(root)] + sum((s_in[id(br)] for br in root.children), 0j)
            root.s_down = s_root
            i_src = (s_root / (3.0 * root.v)).conjugate() if abs(root.v) > 1e-6 else 0j
            root.v = e - self.grid.z * i_src
            for br in order:                                 # forward: from the grid down
                v_p = br.parent.v / br.ratio
                br.child.v = v_p - br.z * br.i
        if self.grid.e_pu > 0.9:                             # keep a healthy profile per unit EMF
            self._profile = {id(b): b.v / self.grid.e_pu for b in self.buses()}
        return self

    def step(self, dt):
        self.grid.step(dt)
        self.solve()
        for br in self.branches:
            if isinstance(br, Transformer):
                br.step_thermal(dt)

    def check(self):
        out = []
        for b in self.buses():
            if not math.isfinite(abs(b.v)) or b.v_pu > 1.2:
                out.append(f"{b.name}: voltage out of physical range ({b.v_ll:.0f} V)")
        for br in self.branches:
            if br.s_loss.real < -1e-6:
                out.append(f"{br.name}: negative series loss")
        return out


class MotorLoad:
    """Adapts an InductionMotor (or any model with p_in and q_in) to a bus load; `count` identical
    motors in the same state (the pump sets of a large press)."""

    def __init__(self, model, count=1):
        self.model, self.count = model, count

    def s_va(self):
        return self.count * complex(self.model.p_in, self.model.q_in)


class ConstantLoad:
    """A fixed P + jQ (W, var), or a constant-impedance load when `z_model` is True."""

    def __init__(self, p_w, q_var=0.0, bus=None, z_model=False):
        self.p, self.q, self.bus, self.z_model = p_w, q_var, bus, z_model

    def s_va(self):
        k = self.bus.v_pu ** 2 if (self.z_model and self.bus is not None) else 1.0
        return complex(self.p, self.q) * k


# ----------------------------------------------------------- power factor correction --
@dataclass(frozen=True)
class ApfcRating:
    """An automatic power factor correction (APFC) panel: capacitor steps switched by a controller."""
    step_kvar: float          # one capacitor step at its rated voltage
    steps: int
    v_rated_ll: float
    target_pf: float          # the controller's lagging target
    switch_s: float           # minimum time between two switchings
    reconnect_s: float        # a removed step must discharge before it reconnects
    loss_w_per_kvar: float = 0.5
    source: str = ""


class Apfc:
    """APFC panel on an LV main bus (2026-10-15). Indian tariffs penalise a low power factor (KSEB
    HT industry: penalty below 0.95, doubled below 0.90; MERC: penalty below 0.90, incentive above
    0.95), so a factory corrects it at each transformer's LV main.

    The controller reads the power flow into its bus (the transformer secondary, with the panel's
    own steps in it) and adds a step while the flow is more than 0.6 of a step more lagging than
    the target, or removes one when the flow leads by more than 0.2 of a step (a leading power
    factor earns no incentive and raises the voltage). Capacitor reactive power goes with V^2.
    A removed step stays out for reconnect_s (IEC 60831-1: discharge to 75 V within 3 min)."""

    def __init__(self, name, bus, rating):
        self.name, self.bus, self.r = name, bus, rating
        self.n = 0
        self.t = 0.0
        self._t_switch = -1e9
        self._t_removed = -1e9
        self.failed_steps = 0         # fault: blown fuses or failed capacitors
        bus.loads.append(self)

    def kvar(self):
        k = (self.bus.v_ll / self.r.v_rated_ll) ** 2
        return max(0, self.n - self.failed_steps) * self.r.step_kvar * k

    def s_va(self):
        q = self.kvar() * 1000.0
        return complex(q * self.r.loss_w_per_kvar / 1000.0, -q)

    def step(self, dt):
        r = self.r
        self.t += dt
        if self.t - self._t_switch < r.switch_s:
            return
        s = self.bus.s_down
        step_var = r.step_kvar * 1000.0
        excess = s.imag - math.tan(math.acos(r.target_pf)) * max(s.real, 0.0)
        if excess > 0.6 * step_var and self.n < r.steps and self.t - self._t_removed >= r.reconnect_s:
            self.n += 1
            self._t_switch = self.t
        elif s.imag < -0.2 * step_var and self.n > 0:
            self.n -= 1
            self._t_switch = self._t_removed = self.t

    def tags(self):
        s = self.bus.s_down
        pf = s.real / abs(s) if abs(s) > 0 else 1.0
        return {"steps_on": self.n, "kvar": self.kvar(), "pf": pf}


# ------------------------------------------------------------------ compiled sweep (_jit.py) --
@njit
def _children_sum(s_in, cidx, a, b, comp):
    """sum((s_in[j] for j in cidx[a:b]), 0j) as the running Python computes it."""
    if not comp:
        r = 0j
        for k in range(a, b):
            r = r + s_in[cidx[k]]
        return r
    hr = lr = hi = li = 0.0
    for k in range(a, b):
        z = s_in[cidx[k]]
        x = z.real
        t = hr + x
        if abs(hr) >= abs(x):
            lr += (hr - t) + x
        else:
            lr += (x - t) + hr
        hr = t
        x = z.imag
        t = hi + x
        if abs(hi) >= abs(x):
            li += (hi - t) + x
        else:
            li += (x - t) + hi
        hi = t
    re = hr + lr if (lr != 0.0 and math.isfinite(lr)) else hr
    im = hi + li if (li != 0.0 and math.isfinite(li)) else hi
    return complex(re, im)


@njit
def _sweep_k(dyn, idx, fs, cs, e, sweeps, comp):
    """The backward/forward sweeps of Network.solve on packed arrays (Network._solve_k): bus 0 is
    the root, the branches in breadth-first order. dyn = bus voltages then load powers; returns the
    bus voltages, the bus s_down, the branch currents and the branch losses, one array. fs ends with
    2.0 read at run time, so abs(i) ** two calls the C pow as Python's ** 2 does (_jit.py)."""
    nbr, nb = idx[0], idx[1]
    par, chi = idx[2:2 + nbr], idx[2 + nbr:2 + 2 * nbr]
    cptr, cidx = idx[2 + 2 * nbr:3 + 2 * nbr + nb], idx[3 + 2 * nbr + nb:]
    ratio, is_tr, p0, q0, v1 = fs[0:nbr], fs[nbr:2 * nbr], fs[2 * nbr:3 * nbr], fs[3 * nbr:4 * nbr], fs[4 * nbr:5 * nbr]
    two, sqrt3 = fs[5 * nbr], fs[5 * nbr + 1]
    z, zg = cs[0:nbr], cs[nbr]
    v = dyn[0:nb].copy()
    load_s = dyn[nb:2 * nb]
    out = np.zeros(2 * nb + 2 * nbr, np.complex128)
    s_in = np.zeros(nbr, np.complex128)
    for _ in range(sweeps):
        for j in range(nbr - 1, -1, -1):                     # backward: leaves first
            c = chi[j]
            s_c = load_s[c] + _children_sum(s_in, cidx, cptr[c], cptr[c + 1], comp)
            out[nb + c] = s_c
            if abs(v[c]) > 1e-6:
                i = (s_c / (3.0 * v[c])).conjugate()
            else:
                i = 0j
            out[2 * nb + j] = i
            loss = 3.0 * z[j] * abs(i) ** two
            out[2 * nb + nbr + j] = loss
            if is_tr[j] != 0.0:
                u2 = (abs(v[par[j]]) * sqrt3 / v1[j]) ** two
                sh = complex(p0[j] * u2, q0[j] * u2)
            else:
                sh = 0j
            s_in[j] = s_c + loss + sh
        s_root = load_s[0] + _children_sum(s_in, cidx, cptr[0], cptr[1], comp)
        out[nb] = s_root
        if abs(v[0]) > 1e-6:
            i_src = (s_root / (3.0 * v[0])).conjugate()
        else:
            i_src = 0j
        v[0] = e - zg * i_src
        for j in range(nbr):                                 # forward: from the grid down
            v_p = v[par[j]] / ratio[j]
            v[chi[j]] = v_p - z[j] * out[2 * nb + j]
    out[0:nb] = v
    return out
