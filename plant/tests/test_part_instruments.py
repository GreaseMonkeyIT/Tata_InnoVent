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
