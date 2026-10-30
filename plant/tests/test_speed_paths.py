"""The plant's speed paths give the same values as the plain code, bit for bit (2026-10-16).

- The tower solve (cooling_tower.CoolingTower.cold_water): the Python path skips the bisection
  steps whose outcome a bracket already decides; the compiled path runs the plain bisection.
- The motor circuit (motor._solve_core): the compiled kernel against the Python code.
Without numba the compiled paths are the Python functions, and the tests still pass.

Run:  python -m pytest plant/tests/test_speed_paths.py -q
"""
import random

from plant.model import cooling_tower as CT
from plant.model import motor as M
from plant.model import catalog as C


def plain_bisection(t_hot, t_wb, lg, target):
    lo, hi = t_wb + 1e-4, t_hot
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if CT.merkel(t_hot, mid, t_wb, lg) > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def test_merkel_written_out_equals_merkel():
    rng = random.Random(1)
    for _ in range(3000):
        t_wb = rng.uniform(5.0, 30.0)
        t_hot = t_wb + rng.uniform(0.01, 15.0)
        t_cold = rng.uniform(t_wb, t_hot + 1.0)
        lg = rng.uniform(0.3, 3.0)
        h = CT.h_sat(t_wb)
        ref = CT.merkel(t_hot, t_cold, t_wb, lg)
        assert CT._merkel_h(t_hot, t_cold, h, lg) == ref
        assert CT._merkel_k(t_hot, t_cold, h, lg) == ref


def test_tower_solve_equals_plain_bisection(monkeypatch):
    """Both paths (the compiled plain bisection and the Python bracket replay) give the plain
    bisection's answer, from no start, a far start, a near start and the exact root."""
    rng = random.Random(2)
    tower = CT.CoolingTower("t", C.EVAPCO_AT_14_99)
    for jit in (True, False):
        monkeypatch.setattr(CT, "JIT", jit)
        for _ in range(1500):
            tower.t_wb = rng.uniform(5.0, 30.0)
            t_hot = tower.t_wb + rng.uniform(0.01, 15.0)
            m = rng.uniform(5.0, 40.0)
            air = rng.uniform(5.0, 40.0)
            tower.fouling = rng.choice([0.0, rng.uniform(0.0, 0.5)])
            ref = plain_bisection(t_hot, tower.t_wb, m / air, tower.kav_l(m / air))
            for start in (None, ref + rng.uniform(-2.0, 2.0), ref + rng.uniform(-1e-6, 1e-6), ref):
                tower._last = start
                assert tower.cold_water(t_hot, m, air) == ref


def test_motor_kernel_equals_python():
    rng = random.Random(3)
    ratings = [v for v in vars(C).values() if isinstance(v, M.MotorRating)]
    assert ratings
    for r in ratings:
        c = M.fit(r)[0]
        for _ in range(3000):
            s = rng.choice([rng.uniform(-0.05, 0.1), rng.uniform(0.0, 1.2), 1.0, 1e-12])
            v = rng.uniform(50.0, 460.0)
            f = rng.choice([r.f_hz, rng.uniform(5.0, 55.0)])
            t = rng.choice([None, rng.uniform(10.0, 160.0)])
            a = M._solve_core(c, r, s, v, f, t)
            b = M._solve_core_py(c, r, s, v, f, t)
            assert all(x == y and type(x) is type(y) for x, y in zip(a, b))


def test_net_torque_kernel_equals_python(monkeypatch):
    from plant.model.plant import UtilitiesPlant
    p = UtilitiesPlant(seed=1, weather="site")
    rng = random.Random(4)
    for m in (p.comp.motor, p.press.motor, p.pierce.motor):
        c = m._params()
        for _ in range(2000):
            w, v, f = rng.uniform(-5.0, 200.0), rng.uniform(50.0, 460.0), rng.choice([50.0, rng.uniform(5.0, 55.0)])
            m.t_wind = rng.uniform(10.0, 150.0)
            monkeypatch.setattr(M, "JIT", True)
            a = m._tq(c, w, v, f)
            monkeypatch.setattr(M, "JIT", False)
            assert m._tq(c, w, v, f) == a


def test_network_sweep_kernel_equals_python(monkeypatch):
    """Two plants of the same seed, one with the compiled sweep and one with the Python sweep, give
    the same bus voltages, flows and tags for 20 min."""
    from plant.model import power as P
    from plant.model.plant import UtilitiesPlant
    a = UtilitiesPlant(seed=40000, t_day0_h=3.0, weather="site")
    b = UtilitiesPlant(seed=40000, t_day0_h=3.0, weather="site")
    for k in range(12000):
        monkeypatch.setattr(P, "JIT", True)
        a.step(0.1)
        monkeypatch.setattr(P, "JIT", False)
        b.step(0.1)
        if k % 50 == 0:
            for x, y in zip(a.net.buses(), b.net.buses()):
                assert x.v == y.v and x.s_down == y.s_down
            ta, tb = a.tags(), b.tags()
            assert all(ta[t] == tb[t] or ta[t] != ta[t] for t in ta)


def test_chiller_table_kernel_equals_python(monkeypatch):
    from plant.model import chiller as CH
    r = C.EWAD190AJYNN
    rng = random.Random(5)
    for _ in range(5000):
        lwt, amb = rng.uniform(0.0, 20.0), rng.uniform(15.0, 50.0)
        for tab in (r.cap_kw, r.pin_kw):
            monkeypatch.setattr(CH, "JIT", True)
            x = CH.bilinear(r.lwt_axis, r.amb_axis, tab, lwt, amb)
            monkeypatch.setattr(CH, "JIT", False)
            assert CH.bilinear(r.lwt_axis, r.amb_axis, tab, lwt, amb) == x
