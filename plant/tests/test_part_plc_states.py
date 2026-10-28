"""pallet-1 and cold-1 PLC state tags and nodes (task 25, 2026-10-28): the transfer's line state is the one
that sets its drives' power, its section states follow the engines on the line, its count the engines
unloaded; the cold test stand's state and drive speed follow its test; both are nodes of the documents
with their state as the PLC input."""
import collections
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import catalog as C                         # noqa: E402
from model.plant import UtilitiesPlant                 # noqa: E402
from model.wiring import instrument_ranges, wiring     # noqa: E402


@pytest.fixture(scope="module")
def hour():
    p = UtilitiesPlant(seed=5)
    rows = []
    for k in range(36000):
        p.step(0.1)
        if k % 10 == 9:
            rows.append(p.tags())
    return p, rows


def test_pallet_state_is_what_sets_its_power(hour):
    p, rows = hour
    r = C.TS5_LINE
    seen = collections.Counter((t["pallet-1.state"], t["pallet-1.power_kw"]) for t in rows)
    assert {s for s, _ in seen} == {"move", "idle"}
    for s, kw in seen:
        assert kw == pytest.approx(r.sections * r.section_kw * (0.8 if s == "move" else 0.3))
    moving = [t for t in rows if t["pallet-1.state"] == "move"]
    assert moving and all("move" in [t[f"pallet-1.section_{k}"] for k in range(1, 9)] for t in moving)
    still = [t for t in rows if t["pallet-1.state"] == "idle"]
    assert all("move" not in [t[f"pallet-1.section_{k}"] for k in range(1, 9)] for t in still)
    assert rows[-1]["pallet-1.engines"] == p.eflow.engines_done >= 1


def test_pallet_jam_holds_its_sections():
    p = UtilitiesPlant(seed=5)
    while not p.pallet.moving:
        p.step(0.1)
    p.pallet.jam = True
    for _ in range(50):
        p.step(0.1)
    t = p.tags()
    assert t["pallet-1.state"] == "jam" and "jam" in [t[f"pallet-1.section_{k}"] for k in range(1, 9)]
    assert t["pallet-1.power_kw"] == pytest.approx(C.TS5_LINE.sections * C.TS5_LINE.section_kw * 0.9)


def test_cold_test_state_speed_and_result(hour):
    p, rows = hour
    states = {t["cold-1.state"] for t in rows}
    assert states == {"idle", "rig", "test"}
    for t in rows:
        testing = t["cold-1.state"] == "test"
        assert (t["cold-1.speed_rpm"] == p.cold.test_rpm) is testing and (t["cold-1.power_kw"] > 0.0) is testing
    seq = [t["cold-1.state"] for t in rows]
    first_test = seq.index("test")
    assert seq[first_test - 1] == "rig"                       # an engine is rigged before it is turned
    assert rows[-1]["cold-1.tested"] >= 1 and rows[-1]["cold-1.last_torque_nm"] > 0.0


def test_documents_give_both_nodes(hour):
    p, rows = hour
    w = wiring(p)
    assert w["pallet-1"]["commands"] == ["pallet-1.state"] and w["pallet-1"]["measures"] == ["pallet-1.power_kw"]
    assert w["cold-1"]["commands"] == ["cold-1.state"]
    assert set(w["cold-1"]["measures"]) == {"cold-1.power_kw", "cold-1.torque_nm", "cold-1.speed_rpm"}
    tags = set(rows[-1])
    for n in ("pallet-1", "cold-1"):
        assert set(w[n]["feeds"] + w[n]["commands"] + w[n]["measures"]) <= tags and w[n]["asset"] == n
    rng = instrument_ranges(p)
    assert rng["pallet-1.power_kw"] == pytest.approx(C.TS5_LINE.sections * C.TS5_LINE.section_kw)
    assert max(t["cold-1.torque_nm"] for t in rows) < rng["cold-1.torque_nm"]
