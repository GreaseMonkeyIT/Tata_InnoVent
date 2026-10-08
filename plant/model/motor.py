"""Three-phase squirrel-cage induction motor (ideas.md 11.3, the first shared part model).

The model is the double-cage equivalent circuit, fitted to the manufacturer's datasheet. This is
the published method of Pedra, Corcoles, Monjo, Bogarra and Rolan, "On fixed-frequency induction
motor models from manufacturer data" and "Estimation of induction motor double-cage model
parameters from manufacturer data" (IEEE Trans. Energy Conversion 19(2), 2004). A single cage
cannot give both the small rated slip and the high starting torque of a real cage motor. The second
cage stands for the deep-bar effect of the real rotor.

Per phase, star equivalent, at supply frequency f:

    V --R1--jX1--+--------+-----------------+
                 |        |                 |
                Rfe     jXm     (R2a/s + jX2a) || (R2b/s + jX2b)
                 |        |                 |
    -------------+--------+-----------------+

Losses, as IEC 60034-2-1 splits them:
  - stator winding  3 |I1|^2 R1 (R1 follows the winding temperature, copper)
  - iron            3 |E|^2 / Rfe
  - rotor winding   s * P_airgap (the cage resistances follow the temperature, aluminium)
  - friction and windage  P_fw,N * (n / n_N)^2 (a drag torque that rises with speed, project choice)
  - additional load losses  P_LL,N * (T / T_N)^2, taken from the air-gap power. ABB's note on IEC 60034-2-1 (TM018, 2009)
    gives the assigned allowance as 2.5 % down to 0.5 % of input power at rated load, by size.
    The model uses 0.025 - 0.005 * log10(P_N / 1 kW), which runs between those ends.

The fit uses the rated point (current, speed, efficiency, power factor), the starting point (Is/IN,
Ts/TN) and the breakdown torque (Tmax/TN), plus the 75 % load efficiency, which sets the share of
the fixed losses (iron, friction and windage). The other datasheet points (50 % load, part-load
power factor) and the published effect of a low supply voltage are NOT in the fit. The tests use
them to check the model.

Dynamics. The electrical transients of a motor last a few cycles (tens of ms), so each step solves
the circuit in steady state (quasi-static) at the present slip. The rotor speed integrates the
torque balance J dw/dt = T_motor - T_load. Near rated speed the torque-speed slope is steep, so
the step is implicit (linearized backward Euler) and stays stable for any step size.

Thermal. Two masses: the windings (stator copper and rotor cage) and the body (iron, frame,
shaft). The winding losses (stator, rotor, additional) heat the windings. The iron, friction and
bearing losses heat the body. Heat flows from the windings to the body, and from the body to the
cooling air. The shaft fan cools a TEFC motor (IC411), so the body-to-air conductance falls when the
motor slows. The heat capacities come from the datasheet weight. When the datasheet gives the
maximum starting times from cold and from hot, they set the winding capacity and the limit
temperature (two equations, two unknowns). The conductances come from the rated temperature rise.
The split of the rise between the two steps is a project choice.
"""
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from .fit import golden_max, nelder_mead

CU_INV_ALPHA = 234.5     # copper: R(T) = R0 * (234.5 + T) / (234.5 + T0) (IEC 60034-1, 8.6.2.3.3)
AL_INV_ALPHA = 225.0     # aluminium cage: 225 instead of 234.5 (IEC 60034-1, 8.6.2.3.3)
FE_FW_SPLIT = 2.0 / 3.0  # iron share of the fixed losses, the rest is friction and windage (project choice)


@dataclass(frozen=True)
class MotorRating:
    """Nameplate and datasheet values. Each instance names its source."""
    p_kw: float          # rated output
    v_ll: float          # rated line voltage, V
    f_hz: float          # rated frequency
    poles: int
    n_rpm: float         # rated speed
    i_a: float           # rated current at v_ll
    eta: float           # efficiency at rated load, 0..1
    pf: float            # power factor at rated load
    is_in: float         # locked-rotor current / rated current
    ts_tn: float         # locked-rotor torque / rated torque
    tmax_tn: float       # breakdown torque / rated torque
    j_kgm2: float        # rotor inertia
    eta_75: float        # efficiency at 75 % load (in the fit: fixed-loss share)
    eta_50: float = None   # efficiency at 50 % load (a check, not in the fit)
    pf_75: float = None    # power factor at 75 % load (a check)
    pf_50: float = None    # power factor at 50 % load (a check)
    i0_a: float = None     # no-load current (in the fit when given: it sets the magnetizing reactance)
    pf_start: float = None  # power factor at locked rotor (in the fit when given)
    x1_x2: float = 0.4 / 0.6   # stator / rotor leakage at locked rotor (IEEE 112, design B = IEC design N)
    rise_k: float = 80.0   # winding temperature rise at rated load, K (class B limit unless given)
    t_amb_c: float = 40.0  # rated ambient (IEC 60034-1)
    mass_kg: float = 0.0   # total motor weight (datasheet). It sets the heat capacities.
    winding_mass_frac: float = 0.10  # copper and cage share of the weight (project choice), unless
                                     # the starting times below calibrate the winding capacity
    t_start_cold_s: float = None     # maximum starting time from cold (datasheet)
    t_start_hot_s: float = None      # maximum starting time from hot (datasheet)
    winding_gradient: float = 0.4    # share of the rated rise between windings and body (project choice)
    still_cooling: float = 0.35  # standstill cooling / rated cooling, TEFC fan stopped (project choice)
    source: str = ""

    @property
    def w_sync(self):
        return 4.0 * math.pi * self.f_hz / self.poles

    @property
    def s_n(self):
        n_sync = 120.0 * self.f_hz / self.poles
        return (n_sync - self.n_rpm) / n_sync

    @property
    def t_n(self):
        return self.p_kw * 1000.0 / (self.n_rpm * math.pi / 30.0)

    @property
    def t_ref_c(self):
        """Winding temperature at rated load: the datasheet losses hold at this temperature."""
        return self.t_amb_c + self.rise_k


@dataclass(frozen=True)
class CircuitParams:
    """Equivalent circuit at rated frequency and at the reference temperature t_ref_c, ohm."""
    r1: float
    x1: float
    xm: float
    rfe: float
    r2a: float
    x2a: float
    r2b: float
    x2b: float
    p_fw_n: float        # friction and windage at rated speed, W
    p_ll_n: float        # additional load losses at rated load, W
    sat: float = 0.0     # leakage saturation: the share the leakage reactances lose at starting current


@dataclass
class OperatingPoint:
    """The steady-state solution at one slip."""
    s: float
    i1: complex
    e: complex
    i2: complex
    p_in: float
    q_in: float
    p_ag: float
    p_cu1: float
    p_fe: float
    p_cu2: float
    t_em: float          # electromagnetic torque after the additional load losses, N m
    p_ll: float = 0.0    # additional load losses, W

    @property
    def amps(self):
        return abs(self.i1)

    @property
    def pf(self):
        s = math.hypot(self.p_in, self.q_in)
        return self.p_in / s if s > 0 else 0.0


def solve(c, rating, s, v_ll, f_hz, t_wind_c=None):
    """Solve the circuit at slip s. Reactances scale with f / f_N. Resistances follow the winding
    temperature. Rfe scales with f / f_N, so the iron loss at constant V/f rises with frequency."""
    k = f_hz / rating.f_hz
    t = rating.t_ref_c if t_wind_c is None else t_wind_c
    kcu = (CU_INV_ALPHA + t) / (CU_INV_ALPHA + rating.t_ref_c)
    kal = (AL_INV_ALPHA + t) / (AL_INV_ALPHA + rating.t_ref_c)
    if abs(s) < 1e-9:
        s = 1e-9
    v_ph = v_ll / math.sqrt(3.0)
    ym = 1.0 / (c.rfe * k) + 1.0 / complex(0.0, c.xm * k)
    ksat, i_mag = 1.0, 0.0
    for _ in range(4 if c.sat > 0 else 1):     # leakage saturation follows the current: fixed point
        z1 = complex(c.r1 * kcu, c.x1 * k * ksat)
        y2 = (1.0 / complex(c.r2a * kal / s, c.x2a * k * ksat)
              + 1.0 / complex(c.r2b * kal / s, c.x2b * k * ksat))
        zp = 1.0 / (ym + y2)
        i1 = v_ph / (z1 + zp)
        ksat_new = leakage_factor(c, rating, abs(i1))
        if abs(ksat_new - ksat) < 1e-6:
            break
        ksat = 0.5 * (ksat + ksat_new)
    e = i1 * zp
    i2 = e * y2
    s_in = 3.0 * v_ph * i1.conjugate()
    p_ag = 3.0 * (e * i2.conjugate()).real
    w_sync = 4.0 * math.pi * f_hz / rating.poles
    # Additional load losses vary with the square of the torque (IEC 60034-2-1). They come out of
    # the air-gap power: P_ag = P_LL + rotor winding loss + T w.
    t_raw = p_ag / w_sync if w_sync > 0 else 0.0
    p_ll = c.p_ll_n * (t_raw / rating.t_n) ** 2 * (f_hz / rating.f_hz) if c.p_ll_n > 0 else 0.0
    p_ll = min(p_ll, max(0.0, p_ag)) if p_ag > 0 else 0.0
    p_rot = p_ag - p_ll
    return OperatingPoint(
        s=s, i1=i1, e=e, i2=i2, p_in=s_in.real, q_in=s_in.imag,
        p_ag=p_ag, p_cu1=3.0 * abs(i1) ** 2 * c.r1 * kcu, p_fe=3.0 * abs(e) ** 2 / (c.rfe * k),
        p_cu2=s * p_rot, t_em=p_rot / w_sync if w_sync > 0 else 0.0, p_ll=p_ll)


def leakage_factor(c, rating, amps):
    """Leakage saturation (Boldea and Nasar, The Induction Machines Design Handbook, the chapter on
    leakage saturation): above about 1.5 x rated current the slot and tooth-tip iron saturates and
    the leakage reactances fall. The model drops them linearly with current from 1.5 x rated, to
    1 - sat at 0.6 x the datasheet starting current, and holds them there: the tooth tips are fully
    saturated by then (project choice for the shape). Then a start at 90 % voltage still draws
    about 10 % less current, as IEEE 141 gives. sat comes from the fit."""
    if c.sat <= 0.0:
        return 1.0
    x = amps / rating.i_a
    x1 = max(0.6 * rating.is_in, 2.0)
    f = (x - 1.5) / (x1 - 1.5)
    return 1.0 - c.sat * min(1.0, max(0.0, f))


def mech_losses(c, rating, w):
    """Friction and windage as a drag torque at speed w (rad/s): P_fw,N * (n / n_N)^2, so the
    torque rises in proportion to speed (project choice for the shape). Returns (torque, power)."""
    w_n = rating.n_rpm * math.pi / 30.0
    t = c.p_fw_n / w_n * (w / w_n)
    return t, t * w


def breakdown(c, rating, v_ll=None, f_hz=None):
    """(slip, torque) at the breakdown (maximum) electromagnetic torque, motoring region."""
    v = rating.v_ll if v_ll is None else v_ll
    f = rating.f_hz if f_hz is None else f_hz
    tq = lambda ls: solve(c, rating, math.exp(ls), v, f).t_em
    grid = [math.log(1e-3) + i * (math.log(1.0) - math.log(1e-3)) / 40 for i in range(41)]
    best = max(range(len(grid)), key=lambda i: tq(grid[i]))
    lo, hi = grid[max(0, best - 1)], grid[min(len(grid) - 1, best + 1)]
    ls = golden_max(tq, lo, hi, tol=1e-7)
    return math.exp(ls), tq(ls)


# ------------------------------------------------------------------------------- the fit --
def loss_budget(r, fixed_share):
    """Split the rated losses (IEC 60034-2-1). fixed_share = (iron + friction) / total losses."""
    p_out = r.p_kw * 1000.0
    p_in = p_out / r.eta
    total = p_in - p_out
    ll_frac = max(0.005, min(0.025, 0.025 - 0.005 * math.log10(max(r.p_kw, 1.0))))
    p_ll = ll_frac * p_in
    p_fix = fixed_share * total
    p_fe, p_fw = FE_FW_SPLIT * p_fix, (1.0 - FE_FW_SPLIT) * p_fix
    p_rot = (p_out + p_fw) / (1.0 - r.s_n)       # air-gap power less the additional losses
    p_ag = p_rot + p_ll
    p_cu2 = r.s_n * p_rot
    p_cu1 = total - p_ll - p_fe - p_fw - p_cu2
    return dict(p_in=p_in, total=total, p_ll=p_ll, p_fe=p_fe, p_fw=p_fw, p_ag=p_ag, p_cu2=p_cu2,
                p_cu1=p_cu1)


def _circuit(r, x, fixed_share):
    """Build CircuitParams from the free variables x (logs of X1, Xm, Rfe, R2a, X2a, R2b, X2b, then
    the logit of the leakage saturation)."""
    b = loss_budget(r, fixed_share)
    r1 = max(b["p_cu1"], 1e-6) / (3.0 * r.i_a ** 2)
    x1, xm, rfe, r2a, x2a, r2b, x2b = (math.exp(v) for v in x[:7])
    sat = 0.6 / (1.0 + math.exp(-x[7])) if len(x) > 7 else 0.0     # 0 .. 0.6
    return CircuitParams(r1=r1, x1=x1, xm=xm, rfe=rfe, r2a=r2a, x2a=x2a, r2b=r2b, x2b=x2b,
                         p_fw_n=b["p_fw"], p_ll_n=b["p_ll"], sat=sat), b


def _residuals(r, x, fixed_share):
    c, b = _circuit(r, x, fixed_share)
    op = solve(c, r, r.s_n, r.v_ll, r.f_hz)
    st = solve(c, r, 1.0, r.v_ll, r.f_hz)
    _, tmax = breakdown(c, r)
    # The rated point weighs 3: the machine runs there. The starting points weigh 1.
    res = [
        3.0 * (op.amps / r.i_a - 1.0),
        3.0 * (op.p_ag / b["p_ag"] - 1.0),
        3.0 * (op.p_fe / b["p_fe"] - 1.0),
        st.amps / (r.is_in * r.i_a) - 1.0,
        st.t_em / (r.ts_tn * r.t_n) - 1.0,
        tmax / (r.tmax_tn * r.t_n) - 1.0,
    ]
    if r.pf_start:
        res.append(st.pf - r.pf_start)
    # Leakage split at locked rotor: stator / rotor = 0.4 / 0.6 for design B (NEMA) or N (IEC
    # 60034-12), the empirical split of IEEE 112. A locked-rotor test cannot separate them.
    z2 = 1.0 / (1.0 / complex(c.r2a, c.x2a) + 1.0 / complex(c.r2b, c.x2b))
    res.append(math.log(max(c.x1, 1e-9) / (r.x1_x2 * max(z2.imag, 1e-9))))
    if r.i0_a:
        res.append(solve(c, r, 1e-6, r.v_ll, r.f_hz).amps / r.i0_a - 1.0)
    else:
        res.append(0.2 * (x[0] - x[6]))   # no no-load current given: stator leakage near the inner cage's
    return res, c, op


def _eta_at(c, r, load, w_guess=None):
    """Efficiency and power factor at a shaft load fraction, rated voltage and frequency."""
    t_target = load * r.t_n
    lo, hi = 1e-6, 0.5
    for _ in range(80):
        s = 0.5 * (lo + hi)
        op = solve(c, r, s, r.v_ll, r.f_hz)
        w = (1.0 - s) * r.w_sync
        t_drag, p_drag = mech_losses(c, r, w)
        if op.t_em - t_drag > t_target:
            hi = s
        else:
            lo = s
    p_shaft = (op.t_em - t_drag) * w
    return p_shaft / op.p_in, op.pf, op, s


FIT_CACHE = os.path.join(os.path.dirname(__file__), "fits.json")
FIT_VERSION = 1          # raise it when the circuit or the fit changes: old cache entries then miss


def _key(r):
    d = asdict(r)
    d.pop("source", None)
    d["_v"] = FIT_VERSION
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


@lru_cache(maxsize=32)
def fit(r):
    """The fitted circuit of a MotorRating: (CircuitParams, report). A fit takes about 15 s, so
    the result is kept in fits.json (tracked in git, so a container never refits)."""
    key = _key(r)
    try:
        with open(FIT_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        e = cache[key]
        return CircuitParams(**e["params"]), e["report"]
    c, rep = _fit(r)
    cache[key] = {"source": r.source, "params": asdict(c), "report": rep}
    try:
        with open(FIT_CACHE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cache, fh, indent=1, sort_keys=True)
            fh.write("\n")
    except OSError:
        pass
    return c, rep


def _fit(r):
    """Fit the circuit to a MotorRating. Returns (CircuitParams, report dict)."""
    zb = r.v_ll / math.sqrt(3.0) / r.i_a
    x0 = [math.log(v) for v in (0.08 * zb, 2.5 * zb, 60.0 * zb, 0.25 * zb, 0.05 * zb,
                                 1.1 * r.s_n * zb, 0.12 * zb)] + [-1.0]

    def inner(fixed_share, x_start):
        obj = lambda x: sum(v * v for v in _residuals(r, x, fixed_share)[0])
        x, val = nelder_mead(obj, x_start, step=0.4, max_iter=4000)
        for _ in range(3):                      # restarts polish the simplex
            x, val = nelder_mead(obj, x, step=0.05, max_iter=4000)
        return x, val

    # outer search: the fixed-loss share that gives the datasheet efficiency at 75 % load
    lo, hi = 0.10, 0.55
    x_best = x0
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        x_best, _ = inner(mid, x_best)
        c, _ = _circuit(r, x_best, mid)
        eta75, _, _, _ = _eta_at(c, r, 0.75)
        # more fixed loss lowers part-load efficiency relative to full load
        if eta75 > r.eta_75:
            lo = mid
        else:
            hi = mid
    share = 0.5 * (lo + hi)
    x_best, val = inner(share, x_best)
    c, b = _circuit(r, x_best, share)
    res, _, _ = _residuals(r, x_best, share)
    return c, {"fixed_share": share, "objective": val, "residuals": list(res), "budget": b}


# ------------------------------------------------------------------------- the motor --
class InductionMotor:
    """One motor with its shaft, its windings' temperature, and its faults.

    Inputs each step: line voltage and frequency at the terminals (a contactor or a drive gives
    them), the load torque as a function of shaft speed, the load inertia, and the cooling air
    temperature. Faults change parameters only (ideas.md 11.9):
      friction_nm     extra bearing drag torque (a failing bearing)
      cooling         share of the rated cooling (a blocked fan cowl or dirty fins)
      rotor_r_factor  cage resistance factor (cracked or broken rotor bars)
      locked          the shaft cannot turn (a seized bearing, a jammed machine)
    """

    def __init__(self, name, rating, j_load=0.0, t_cool_c=None):
        self.name, self.rating = name, rating
        self.c, self.fit_report = fit(rating)
        self.j = rating.j_kgm2 + j_load
        r = rating
        self.t_cool_c = r.t_amb_c if t_cool_c is None else t_cool_c
        # thermal constants: heat capacities from the weight (copper 385, aluminium 900, iron 460
        # J/kg K: a winding mix of 450), conductances from the rated losses and the rated rise
        b = self.fit_report["budget"]
        p_wind_n = b["p_cu1"] + b["p_cu2"] + b["p_ll"]
        p_loss_n = b["total"]
        mass = r.mass_kg if r.mass_kg > 0 else 10.4 * r.p_kw   # 229 kg / 22 kW when not given
        self.c_wind = r.winding_mass_frac * mass * 450.0
        self.c_body = (1.0 - r.winding_mass_frac) * mass * 460.0
        self.t_limit_c = None
        if r.t_start_cold_s and r.t_start_hot_s and r.t_start_cold_s > r.t_start_hot_s:
            # Two datasheet limits, two unknowns. With the rotor locked the windings heat almost
            # adiabatically: t = C (T_lim - T0) / P_lr. From cold (T0 = ambient) and from hot
            # (T0 = rated temperature) that gives the limit temperature and the winding capacity.
            q = r.t_start_cold_s / r.t_start_hot_s
            self.t_limit_c = (q * r.t_ref_c - r.t_amb_c) / (q - 1.0)
            op = solve(self.c, r, 1.0, r.v_ll, r.f_hz, 0.5 * (r.t_amb_c + self.t_limit_c))
            p_lr = op.p_cu1 + op.p_cu2 + op.p_ll
            c_w = r.t_start_cold_s * p_lr / (self.t_limit_c - r.t_amb_c)
            self.c_body = max(mass * 460.0 - c_w * 460.0 / 450.0, 0.2 * mass * 460.0)
            self.c_wind = c_w
        self.g_wb = p_wind_n / (r.winding_gradient * r.rise_k)
        self.g_ba_n = p_loss_n / ((1.0 - r.winding_gradient) * r.rise_k)
        self.t_wind = self.t_body = self.t_cool_c
        self.w = 0.0
        self.friction_nm = 0.0
        self.cooling = 1.0
        self.rotor_r_factor = 1.0
        self.locked = False      # a seized bearing or a jammed machine holds the shaft at rest
        self._zero_outputs()

    def _zero_outputs(self):
        self.amps = 0.0
        self.p_in = self.q_in = 0.0
        self.pf = 0.0
        self.t_shaft = 0.0       # torque the motor gives the load, N m
        self.p_loss = 0.0
        self.losses = {"cu1": 0.0, "fe": 0.0, "cu2": 0.0, "fw": 0.0, "ll": 0.0, "fric": 0.0}
        self.slip = 1.0

    @property
    def rpm(self):
        return self.w * 30.0 / math.pi

    def _params(self):
        if self.rotor_r_factor == 1.0:
            return self.c
        return replace(self.c, r2a=self.c.r2a * self.rotor_r_factor, r2b=self.c.r2b * self.rotor_r_factor)

    def _torque(self, c, w, v_ll, f_hz):
        """(net motor torque at the shaft, operating point, drag power)."""
        w_sync = 4.0 * math.pi * f_hz / self.rating.poles
        s = 1.0 - w / w_sync
        op = solve(c, self.rating, s, v_ll, f_hz, self.t_wind)
        t_drag, p_drag = mech_losses(c, self.rating, w)
        return op.t_em - t_drag, op, p_drag

    def step(self, dt, v_ll, f_hz, load_torque, j_load=None, star=False):
        """Advance dt seconds. load_torque(w) gives the load torque in N m at shaft speed w (rad/s),
        positive against motoring. Returns the shaft power into the load in W (mean over the step).

        star=True: a delta-wound motor connected in star (a star-delta starter). Each winding sees
        1/sqrt(3) of its rated voltage, so the model runs at v_ll / sqrt(3): torque, power and losses
        are 1/3 of a direct-on-line start, as they are in the real machine. The line current of a
        star connection is the winding current, 1/sqrt(3) of the model's delta-equivalent current,
        so `amps` is divided by sqrt(3): the line current is 1/3 of the direct-on-line current."""
        if star:
            v_ll = v_ll / math.sqrt(3.0)
        if j_load is not None:
            self.j = self.rating.j_kgm2 + j_load
        c = self._params()
        fric = lambda w: self.friction_nm * (1.0 if w > 1e-3 else (0.0 if w > -1e-3 else -1.0))
        energized = v_ll > 1.0 and f_hz > 0.1
        # Torque balance J dw/dt = Tm(w) - Tl(w), in substeps. Each substep is implicit on the
        # stabilizing slopes only (a falling motor torque, a rising load torque) and explicit on
        # the rest: a slope that rises with speed (the run-up below breakdown) is an unstable
        # direction, and an implicit step there can diverge. A substep moves the speed by at most
        # 2 % of synchronous speed, so a large dt (0.1 s) still follows a star-delta changeover.
        w_sync = 4.0 * math.pi * max(f_hz, 1.0) / self.rating.poles
        dw_max = 0.02 * w_sync
        t_left, h = dt, dt
        while t_left > 1e-12 and not self.locked:
            h = min(h, t_left)
            w0 = self.w
            if energized:
                tm, _, _ = self._torque(c, w0, v_ll, f_hz)
                dw = max(1e-3, 1e-4 * abs(w0))
                dtm = (self._torque(c, w0 + dw, v_ll, f_hz)[0] - tm) / dw
            else:
                tm = -mech_losses(self.c, self.rating, w0)[0]    # coasting: friction and windage
                dtm = 0.0
            tl = load_torque(w0) + fric(w0)
            dl = (load_torque(w0 + 1e-3) - load_torque(w0)) / 1e-3
            denom = self.j / h + max(0.0, -dtm) + max(0.0, dl)
            w1 = w0 + (tm - tl) / denom
            if abs(w1 - w0) > dw_max and h > 1e-4:
                h *= 0.5                                       # too big a move: halve and retry
                continue
            if not energized and w0 * w1 < 0:                  # a coasting shaft stops, it does not reverse
                w1 = 0.0
            if not energized and abs(w1) < 1e-6 and abs(load_torque(0.0)) <= self.friction_nm + 1e-9:
                w1 = 0.0
            self.w = w1
            t_left -= h
            h = min(2.0 * h, dt)
        if self.locked:
            self.w = 0.0
        p_drag = 0.0
        if not energized:
            p_drag = mech_losses(self.c, self.rating, self.w)[1]
            tm = -mech_losses(self.c, self.rating, self.w)[0]
        # outputs at the new speed, consistent for the energy balance
        if energized:
            tm, op, p_drag = self._torque(c, self.w, v_ll, f_hz)
            self.amps, self.p_in, self.q_in, self.pf = op.amps, op.p_in, op.q_in, op.pf
            if star:
                self.amps /= math.sqrt(3.0)
            self.slip = op.s
            self.losses = {"cu1": op.p_cu1, "fe": op.p_fe, "cu2": op.p_cu2, "fw": p_drag,
                           "ll": op.p_ll, "fric": abs(fric(self.w) * self.w)}
            self.t_shaft = tm - fric(self.w)
        else:
            self._zero_outputs()
            self.losses["fw"] = p_drag
            self.losses["fric"] = abs(fric(self.w) * self.w)
            self.t_shaft = tm - fric(self.w)
            self.slip = 1.0
        self.p_loss = sum(self.losses.values())
        self._thermal(dt)
        return self.t_shaft * self.w

    def _thermal(self, dt):
        """Backward Euler on the two-mass network (stable for any dt). The fan cools with speed.
        cooling < 1 is a fault (a blocked cowl, dirty fins)."""
        w_n = self.rating.n_rpm * math.pi / 30.0
        frac = min(1.0, abs(self.w) / w_n)
        k_cool = self.rating.still_cooling + (1.0 - self.rating.still_cooling) * frac
        g_ba = k_cool * self.cooling * self.g_ba_n
        L = self.losses
        p_w = L["cu1"] + L["cu2"] + L["ll"]
        p_b = L["fe"] + L["fw"] + L["fric"]
        a11 = self.c_wind / dt + self.g_wb
        a12 = -self.g_wb
        a21 = -self.g_wb
        a22 = self.c_body / dt + self.g_wb + g_ba
        b1 = self.c_wind / dt * self.t_wind + p_w
        b2 = self.c_body / dt * self.t_body + p_b + g_ba * self.t_cool_c
        det = a11 * a22 - a12 * a21
        self.t_wind = (b1 * a22 - a12 * b2) / det
        self.t_body = (a11 * b2 - a21 * b1) / det

    def heat_stored(self):
        """Heat in the two masses above the cooling air, J (for the energy balance)."""
        return self.c_wind * (self.t_wind - self.t_cool_c) + self.c_body * (self.t_body - self.t_cool_c)

    def heat_to_air(self):
        """Heat flow from the body to the cooling air now, W."""
        w_n = self.rating.n_rpm * math.pi / 30.0
        frac = min(1.0, abs(self.w) / w_n)
        k_cool = self.rating.still_cooling + (1.0 - self.rating.still_cooling) * frac
        return k_cool * self.cooling * self.g_ba_n * (self.t_body - self.t_cool_c)

    def kinetic_energy(self):
        return 0.5 * self.j * self.w ** 2

    def tags(self):
        """The signals of a real motor feeder and motor: what a meter and a winding sensor give."""
        return {"current_a": self.amps, "power_kw": self.p_in / 1000.0, "pf": self.pf,
                "speed_rpm": self.rpm, "torque_nm": self.t_shaft, "winding_c": self.t_wind}

    def check(self):
        """Physics checks (ideas.md 11.9). Returns a list of messages, empty when sane."""
        out = []
        vals = [self.amps, self.p_in, self.w, self.t_wind, self.p_loss]
        if not all(math.isfinite(v) for v in vals):
            out.append(f"{self.name}: a value is not finite")
        if self.amps < 0 or self.p_loss < -1e-6:
            out.append(f"{self.name}: negative current or loss")
        if min(self.t_wind, self.t_body) < self.t_cool_c - 1e-6:
            out.append(f"{self.name}: a thermal mass is colder than its cooling air")
        if any(v < -1e-6 for v in self.losses.values()):
            out.append(f"{self.name}: a negative loss term")
        return out
