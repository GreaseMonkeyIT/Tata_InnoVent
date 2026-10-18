"""The line tracking system (floor/tracking.py): it changes nothing in the plant, and its buffers agree
with the plant flow's own counts."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model.plant import UtilitiesPlant          # noqa: E402
from floor.tracking import Tracker              # noqa: E402


def _plant():
    return UtilitiesPlant(seed=3, t_day0_h=4.0, weather="site")


def test_tracking_leaves_the_plant_tags_unchanged():
    a, b = _plant(), _plant()
    tr = Tracker(b)
    for i in range(36000):                       # 1 h at 0.1 s
        a.step(0.1)
        b.step(0.1)
        tr.step(b.t)
        if i % 100 == 99:
            assert a.tags() == b.tags(), f"tags differ at {a.t:.1f} s"


def test_buffers_follow_the_flow_and_every_part_is_accounted_for():
    p = _plant()
    tr = Tracker(p)
    f, e = p.fflow, p.eflow
    pairs = {"side_stock": lambda: len(f.q_pierce), "plate_stock": lambda: f.plates_queue,
             "pierce_out": lambda: f.q_form, "form_out": lambda: f.q_blast,
             "blast_side": lambda: f.side_ready, "blast_cross": lambda: f.cross_ready,
             "cross_blast": lambda: getattr(f, "q_cross_blast", 0), "ced_out": lambda: f.ced_sets,
             "scan_in": lambda: f.scan_queue, "out_in": lambda: f.out_queue}
    run = dict.fromkeys(pairs, 0)
    for _ in range(36000 * 4):                   # 4 h
        p.step(0.1)
        tr.step(p.t)
        for name, fn in pairs.items():
            # the flow counts a finished part in its next tick: one tick of lag is allowed
            run[name] = run[name] + 1 if len(getattr(tr, name)) != fn() else 0
            assert run[name] <= 1, f"{name} disagrees with the flow at {p.t:.1f} s"
        occ = {s: sum(1 for v in tr.where.values() if v == f"pallet-1:{s}") for s in e.stations}
        if e._moving and e._moving[0]:
            occ[e._moving[0]] += 1                # the flow frees a stop when the pallet arrives
        assert all(occ[s] == int(e.at[s]) for s in e.stations), f"pallet stops disagree at {p.t:.1f} s"
    ships = [ev[2] for ev in tr.events if ev[1] == "ship"]
    assert sum(x.startswith("FR") for x in ships) == f.frames_done > 0
    assert sum(x.startswith("EN") for x in ships) == e.engines_done > 0
    assert tr.unmatched_lifts == 0
    assert tr.check() == []
