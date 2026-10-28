"""The OpenModelica cross-check of motor.py (plant/crosscheck/motor_om.py, task 23): the library parameters
are motor.py's circuit, the committed model is the one the script writes, the reference holds the
datasheet's rated point, and, where OpenModelica is installed, the library machine agrees with motor.py."""
import math
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "crosscheck"))

import motor_om as OM                                    # noqa: E402
from model import catalog as C                           # noqa: E402

R = getattr(C, OM.RATING)


def test_library_parameters_are_the_circuit_of_motor_py():
    """Per phase of a delta connection: 3 x the star equivalent, reactances over 2 pi f; without losses no
    core, friction or stray load."""
    c = OM.circuit(R, True)
    q = OM.msl_params(R, c)
    w = 2.0 * math.pi * R.f_hz
    assert q["Rs"] == pytest.approx(3 * c.r1) and q["Lm"] * w == pytest.approx(3 * c.xm)
    assert q["Lrsigma"] * w == pytest.approx(3 * c.x2a) and q["Rr2"] == pytest.approx(3 * c.r2b)
    assert q["PcoreRef"] / q["VcoreRef"] ** 2 / 3 == pytest.approx(1 / (3 * c.rfe))     # G per phase = 1 / (3 Rfe)
    assert q["PfricRef"] == c.p_fw_n and c.sat == 0.0
    ideal = OM.msl_params(R, OM.circuit(R, False))
    assert ideal["PcoreRef"] == ideal["PfricRef"] == ideal["PstrayRef"] == 0.0


def test_reference_holds_the_rated_point():
    ref = OM.reference(R)
    rated = next(x for x in ref["Loss"] if x["slip"] == R.s_n)
    assert rated["I"] == pytest.approx(R.i_a, rel=0.01) and rated["tau_shaft"] == pytest.approx(R.t_n, rel=0.01)
    w_n = R.n_rpm * math.pi / 30.0
    assert OM.t90(ref["start"], w_n) is not None and ref["start"][-1][1] == pytest.approx(w_n, rel=0.002)


def test_committed_model_is_the_generated_one():
    text = OM.modelica(R, OM.msl_params(R, OM.circuit(R, False)), OM.msl_params(R, OM.circuit(R, True)))
    with open(os.path.join(HERE, "..", "crosscheck", "VisrMotorCheck.mo"), encoding="utf-8") as fh:
        assert fh.read() == text


@pytest.mark.skipif(not shutil.which("omc"), reason="OpenModelica is not installed: allow " + "; ".join(OM.HOSTS))
def test_motor_py_against_openmodelica(tmp_path):
    assert OM.main(["--out", str(tmp_path)]) == 0
