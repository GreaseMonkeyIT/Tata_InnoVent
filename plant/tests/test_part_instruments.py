"""Virtual instruments v0 (instruments.py): biases inside the accuracy class, the same devices on every
day of one factory, noise of a fifth of the accuracy, digital readings left alone."""
import os
import statistics
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model.instruments import Instruments, _accuracy             # noqa: E402


@pytest.mark.parametrize("tag,value,acc", [
    ("press-1.oil_c", 50.0, 0.15 + 0.002 * 50.0),        # Pt100 class A, IEC 60751
    ("ced-1.oven_c", 200.0, max(1.5, 0.004 * 200.0)),    # type K class 1, IEC 60584-1
    ("press-1.current_a", 40.0, 0.2),                     # PMD class 0.5
    ("compressor-1.pressure_barg", 7.0, 0.00075 * 10.0),  # transmitter, 0.075 % of a 10 bar span
])
def test_accuracy_classes(tag, value, acc):
    assert _accuracy(tag, value)[0] == pytest.approx(acc)


def test_bias_is_a_property_of_the_device():
    a, b = Instruments(7, noise=False), Instruments(7, noise=False)
    a.seed_noise(1)
    b.seed_noise(2)                                      # another day, the same factory
    assert a.read("press-1.oil_c", 50.0) == b.read("press-1.oil_c", 50.0)
    c = Instruments(8, noise=False)                      # another factory: other devices
    assert any(c.read(t, 50.0) != a.read(t, 50.0) for t in ("press-1.oil_c", "tr-1.oil_c", "washer-1.tank_c"))


def test_bias_stays_inside_the_class_and_noise_is_a_fifth():
    for f in range(30):
        inst = Instruments(f, noise=False)
        x = inst.read("press-1.current_a", 40.0)
        assert abs(x - 40.0) <= 0.2 + 0.005
    inst = Instruments(3)
    inst.seed_noise(9)
    xs = [inst.read("press-1.current_a", 40.0) for _ in range(5000)]
    assert statistics.pstdev(xs) == pytest.approx(0.2 / 5.0, rel=0.15)


def test_digital_values_pass_unchanged():
    inst = Instruments(0)
    assert inst.read("press-1.phase", "bend") == "bend"
    assert inst.read("x.flag", True) is True
    assert inst.read_column("press-1.phase", ["idle", "bend"], 1) == ["idle", "bend"]


def test_read_column_matches_read_and_keeps_its_own_noise():
    """Without noise a column reads as `read` does, value for value. With noise, an instrument's
    readings depend only on the run and its own tag, not on the other tags read."""
    a = Instruments(4, noise=False, ranges={"press-1.current_a": 72.0})
    vals = [0.0, 1.0, 40.0, 72.0, None, float("nan"), 200.0]
    for t in ("press-1.current_a", "press-1.oil_c", "lmp-1.oil_c", "compressor-1.pressure_barg", "ced-1.tank_gas_m3h"):
        col = a.read_column(t, vals, 3)
        ref = [a.read(t, v) for v in vals]
        assert [round(c, 9) for c in col[:4] + col[6:]] == [round(r, 9) for r in ref[:4] + ref[6:]]
        assert col[4] is None and col[5] != col[5]
    b = Instruments(4)
    x1 = b.read_column("press-1.oil_c", [50.0] * 100, 9)
    b.read_column("press-1.current_a", [40.0] * 100, 9)
    x2 = Instruments(4).read_column("press-1.oil_c", [50.0] * 100, 9)
    assert x1 == x2 and len(set(x1)) > 1
    assert Instruments(4).read_column("press-1.oil_c", [50.0] * 100, 10) != x1


@pytest.mark.parametrize("noise", [True, False])
def test_stream_equals_read_column(monkeypatch, noise):
    """The live reader (2026-10-21): sample by sample over many instruments, it gives the readings of
    `read_column` bit for bit. The columns mix numbers, ints, gaps, nan, text and bools, one tag
    appears late, and the noise blocks run out several times."""
    import random
    from model.instruments import Stream
    monkeypatch.setattr(Stream, "BLOCK", 7)
    tags = ["press-1.current_a", "press-1.oil_c", "ced-1.oven_c", "compressor-1.pressure_barg",
            "ced-1.tank_gas_m3h", "pierce-1.hits", "x.late_kw"]
    ranges = {"press-1.current_a": 72.0, "x.late_kw": 500.0}
    r = random.Random(5)
    pick = (lambda: None, lambda: float("nan"), lambda: True, lambda: "run", lambda: r.randint(0, 900),
            lambda: r.uniform(-20.0, 900.0), lambda: r.gauss(0.0, 2.0))
    n = 40
    cols = {t: [r.choice(pick)() for _ in range(n)] for t in tags}
    cols["x.late_kw"][:25] = [None] * 25
    a = Instruments(2, noise=noise, ranges=ranges)
    ref = {t: a.read_column(t, cols[t], 60001) for t in tags}
    s = Instruments(2, noise=noise, ranges=ranges).stream(tags, 60001)
    got = [s.read([cols[t][k] for t in tags]) for k in range(n)]
    for i, t in enumerate(tags):
        assert [repr(row[i]) for row in got] == [repr(v) for v in ref[t]]
    assert Instruments(2).stream([], 1).read(["x", 1.0]) == ["x", 1.0]
