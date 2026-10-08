"""Tests of the node small model (twin/node_model.py) and a short form of concept check CP-1.

Run:  python -m pytest twin/tests -q      (from the repo root; numpy needed)
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "plant"))

from twin.node_model import FilterBank, NodeModel                    # noqa: E402


def test_filter_bank_step_response():
    fb = FilterBank(1, taus=(10.0,))
    fb.reset([0.0])
    y = None
    for _ in range(10):
        y = fb.step([1.0], 1.0)
    assert y[0] == pytest.approx(1.0 - np.exp(-1.0), rel=1e-9)


def test_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    m = NodeModel(["a", "b"], ["y"], hidden=(5,), taus=(3.0,))
    m._init(4, 1)
    for W, b in m.params:                      # a non-zero linear path too
        W += rng.normal(0, 0.3, W.shape)
    X, Y = rng.normal(size=(20, 4)), rng.normal(size=(20, 1))
    g, _ = m._grads(X, Y)
    eps = 1e-6
    for li in range(len(m.params)):
        W = m.params[li][0]
        i, j = 1, 0
        old = W[i, j]
        W[i, j] = old + eps
        lp = np.mean((m._forward(X)[-1] - Y) ** 2)
        W[i, j] = old - eps
        lm = np.mean((m._forward(X)[-1] - Y) ** 2)
        W[i, j] = old
        assert g[li][0][i, j] == pytest.approx((lp - lm) / (2 * eps), rel=1e-4, abs=1e-8)


def test_linear_relation_extends_past_training_range(tmp_path):
    """y = u + 0.5: trained on u in [0, 1], checked on u = 3. The linear path must carry it."""
    rng = np.random.default_rng(1)
    U = rng.uniform(0.0, 1.0, size=(4000, 1))
    Y = U + 0.5
    m = NodeModel(["u"], ["y"], hidden=(8,), taus=(1.0,))
    m.fit(U, Y, 1.0, epochs=200, patience=30)
    Ut = np.full((50, 1), 3.0)
    pred = m.predict(Ut, 1.0)[-1, 0]
    assert pred == pytest.approx(3.5, abs=0.25)
    assert not m.covered(Ut, 1.0)[-1]
    p = tmp_path / "m.npz"
    m.save(p)
    m2 = NodeModel.load(p)
    assert np.allclose(m2.predict(Ut, 1.0), m.predict(Ut, 1.0))


@pytest.mark.slow
def test_cp1_short_form():
    """CP-1 with 6 h of training: victims stay quiet, faults show on the right signal."""
    from twin.experiments import cp1_compressor as E
    U, Y, _ = E.simulate(6.0, seed=1)
    m = NodeModel(E.INPUTS, E.OUTPUTS)
    m.fit(U, Y, E.SAMPLE, epochs=200, patience=20)
    sd = m.residuals(U, Y, E.SAMPLE)[int(0.8 * len(U)):].std(axis=0)

    def zmean(kw, t0):
        U2, Y2, _ = E.simulate(**kw)
        return (m.residuals(U2, Y2, E.SAMPLE)[int(t0):] / sd).mean(axis=0)

    sag = zmean(dict(hours=1.0, seed=12, sag=(1800.0, 3000.0, 0.90)), 1800)
    assert np.all(np.abs(sag) < 2.0)
    foul = zmean(dict(hours=1.5, seed=15, faults=[(1800.0, lambda c: setattr(c, "cooler_fouling", 1.0))]), 2700)
    assert foul[1] > 4.0 and abs(foul[0]) < 2.0          # element temperature, not current
    fric = zmean(dict(hours=1.0, seed=16, faults=[
        (1800.0, lambda c: setattr(c.motor, "friction_nm", 0.10 * c.motor.rating.t_n))]), 1800)
    assert fric[0] > 4.0 and abs(fric[1]) < 2.0          # current, not temperature


@pytest.mark.slow
def test_cp2_press_short_form():
    """CP-2 with 6 h of training: victims quiet, F1 on the return, F2 stretches the bend, F15 only
    loads the bend (material, not machine)."""
    import numpy as np
    from twin.experiments import cp2_press as E
    from twin.rhythm import OnlinePhase, segment_durations
    U, Y, ph, Yf = E.simulate(6.0, seed=1)
    rp = OnlinePhase.learn(Y[:, 0], E.SAMPLE)
    assert rp is not None

    def inputs(U2, Y2):
        return np.hstack([U2, OnlinePhase(rp.mid, rp.period).run(Y2[:, 0], E.SAMPLE)])
    m = NodeModel(["v", "tw"] + [f"r{i}" for i in range(12)], E.OUTPUTS, hidden=(48, 48),
                  taus=(2.0, 10.0, 60.0, 600.0))
    m.fit(inputs(U, Y), Y, E.SAMPLE, epochs=120, patience=12)
    r = m.residuals(inputs(U, Y), Y, E.SAMPLE)
    sd = r[int(0.8 * len(r)):][ph[int(0.8 * len(r)):] == "return"].std(axis=0) + 1e-9
    d0 = np.median(segment_durations(Yf, rp.mid, E.FAST))

    def ret_z(case):
        U2, Y2, ph2, Yf2 = case
        r2 = m.residuals(inputs(U2, Y2), Y2, E.SAMPLE)[3600:]
        return (r2[ph2[3600:] == "return"] / sd).mean(axis=0)[0], np.median(segment_durations(Yf2[18000:], rp.mid, E.FAST))
    z_sag, d_sag = ret_z(E.simulate(1.0, 12, sag=(1800, 2700, 0.92)))
    z_f1, _ = ret_z(E.simulate(1.0, 14, fault=(1800, lambda p: setattr(p, "friction_extra_n", 160e3))))
    _, d_f2 = ret_z(E.simulate(1.0, 15, fault=(1800, lambda p: setattr(p, "leak_factor", 4.0))))
    z_f15, d_f15 = ret_z(E.simulate(1.0, 16, uts_fixed=680.0))
    assert abs(z_sag) < 1.5 and abs(d_sag - d0) < 0.2
    assert z_f1 > 3.0
    assert d_f2 > d0 + 0.25            # about 1.7 x the normal spread (0.15 s) after 30 min
    assert abs(z_f15) < 1.5 and abs(d_f15 - d0) < 0.2
