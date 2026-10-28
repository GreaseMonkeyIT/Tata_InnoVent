"""The site's meters as new tags (meters.py, task 28, 2026-10-28): the energy registers add up to the
incomer's energy with the network losses, the transformer losses are the ones of the load flow, the
cooling branches add up to the loop flow, the fuel and gas registers integrate their flows, and hot-1
burns its rated fuel at rated power. The tags are not in the documents."""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import catalog as C                         # noqa: E402
from model.plant import UtilitiesPlant                 # noqa: E402
from model.wiring import wiring                        # noqa: E402


@pytest.fixture(scope="module")
def run():
    """40 min, with the losses of each step counted alongside the meters."""
    p = UtilitiesPlant(seed=5)
    losses_kwh, gas, fuel = 0.0, 0.0, 0.0
    t0 = None
    for k in range(24000):
        p.step(0.1)
        if k == 11999:
            t0 = p.tags()
            losses_kwh = gas = fuel = 0.0
        if k >= 12000:
            net = p.net
            loss = sum(br.s_loss.real + br.shunt_s().real for br in net.branches)
            losses_kwh += loss / 1000.0 * 0.1 / 3600.0
    return p, t0, p.tags(), losses_kwh


def test_energy_registers_add_up_to_the_incomer(run):
    p, t0, t1, losses = run
    loads = [k for k in t1 if k.endswith(".energy_kwh") and not k.startswith(("incomer-1.", "feeder-"))]
    d = lambda k: t1[k] - t0[k]                       # noqa: E731
    total = sum(d(k) for k in loads) - d("hot-1.energy_export_kwh") + losses
    assert d("incomer-1.energy_kwh") == pytest.approx(total, rel=1e-6)
    assert t1["incomer-1.energy_export_kwh"] == 0.0                    # the site never exports
    assert d("incomer-1.energy_kvah") >= math.hypot(d("incomer-1.energy_kwh"), 0.0)
    assert all(t1[k] >= t0[k] for k in loads)                          # registers only count up


def test_feeders_and_transformer_losses(run):
    p, _, t, _ = run
    for name, bus in p.psu.items():
        br = next(b for b in p.net.branches if b.child is bus)
        assert t[f"{br.name}.p_kw"] == pytest.approx((bus.s_down + br.s_loss).real / 1000.0)
    for tr in (p.tr1, p.tr2):
        r = tr.rating
        assert t[f"{tr.name}.no_load_loss_kw"] == pytest.approx(r.p0_w / 1000.0 * (tr.parent.v_ll / r.v1_ll) ** 2)
        assert t[f"{tr.name}.load_loss_kw"] == pytest.approx(r.pk_w / 1000.0 * tr.k ** 2, rel=0.05)
        assert t[f"{tr.name}.loss_kw"] == pytest.approx(t[f"{tr.name}.load_loss_kw"] + t[f"{tr.name}.no_load_loss_kw"])


def test_flows(run):
    p, t0, t, _ = run
    branches = [k for k in t if k.startswith("cool-1.") and k.endswith("_m3h") and k != "cool-1.flow_m3h"]
    assert len(branches) == len(p.loop.branches)
    assert sum(t[k] for k in branches) == pytest.approx(t["cool-1.flow_m3h"], rel=1e-9)
    assert t["compressor-1.air_m3min"] in (0.0, pytest.approx(p.comp.q_fad * 60.0)) and t["compressor-1.header_m3min"] > 0.0
    assert t["gas-1.flow_m3h"] == p.gas.flow_m3h and t["gas-1.volume_m3"] > t0["gas-1.volume_m3"]
    assert t["hot-1.fuel_l"] >= t0["hot-1.fuel_l"]


def test_hot_test_burns_its_rated_fuel():
    """At rated power the engine burns the 89.1 l/h of its spec sheet (Cummins CFP9E-F85)."""
    p = UtilitiesPlant(seed=5, with_fab=False, flow=False)
    for _ in range(20000):
        p.step(0.1)
        if p.hot.load == 1.0:
            break
    assert p.hot.load == 1.0 and p.tags()["hot-1.fuel_l_h"] == pytest.approx(C.QSL9_F85.fuel_l_h_rated)


def test_meter_tags_are_not_in_the_documents(run):
    p, _, t, _ = run
    doc = {x for d in wiring(p).values() for r in ("feeds", "commands", "measures") for x in d[r]}
    new = {k for k in t if "energy" in k or k.startswith("feeder-") or "loss_kw" in k or k.endswith(
        ("air_m3min", "header_m3min", "volume_m3", "fuel_l_h", "fuel_l"))}
    assert new and not (new & doc)
