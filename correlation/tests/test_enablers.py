"""Engine fixes from the 17-18 September box soak (SCENARIOS.md 4.2 and 4.3).

The rules under test:
- a window that the aggregator has only partly refilled never teaches a baseline (the zero lock);
- a low gate quantile keeps a normal duty cycle quiet and still flags a stuck-on load;
- a plant entity keeps its full name in memory keys (qa-scanner-1, not "qa");
- a family can name two sources, and each member takes the first one it exports;
- a source-only member of a plant domain (the chiller) gets a witness pair;
- a steady coolant temperature inside its learned band gets no trip card;
- a plant member that is not deviating cannot lead a bare correlation edge.
"""
import importlib
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from engine.forecast import incipient_findings
from engine.gate import Witness
from engine.pipeline import run_pass
from engine.state import GraphMemory, MemoryConfig, set_verbatim_names, stable_workload

rng = np.random.default_rng(1918)   # private stream: other suites keep their draws


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("MEMORY_DB", str(tmp_path_factory.mktemp("mem") / "l3-memory.db"))
    mp.setenv("ENGINE_SIGNALS", "psi_io,bus_voltage,coolant_temp")
    mp.setenv("PLANT_SOURCES", "bus_voltage:current_draw,coolant_temp:heat_load|cooling_shortfall")
    mp.setenv("PLANT_DOMAINS", "rail:psu-a=press-1,press-2,cnc-1,qa-scanner-1,psu-a;"
                               "loop:cool-1=press-1,press-2,cnc-1,furnace-1,chiller-1,cool-1")
    mp.delenv("DOMAIN_SOURCES", raising=False)
    sys.modules.pop("service", None)
    mod = importlib.import_module("service")
    yield mod
    mp.undo()
    sys.modules.pop("service", None)


def _window(pods, signal, n_samples, value=1.0):
    """An aggregator /window holding n_samples at 5 s steps for each pod, newest last."""
    t_end = time.time()
    out = {}
    for pod in pods:
        out[f"plant/{pod}/{signal}"] = [
            {"ts": datetime.fromtimestamp(t_end - 5 * (n_samples - 1 - k), timezone.utc).isoformat(),
             "value": value + 0.01 * rng.standard_normal()}
            for k in range(n_samples)]
    return out


def test_a_part_filled_window_reports_low_coverage(service):
    vecs, _breach, coverage = service.build_inputs(_window(["press-1"], "coolant_temp", 30), [])
    assert "press-1" in vecs["coolant_temp"]               # the pass still sees the pod
    assert coverage["coolant_temp"]["press-1"] < service.MIN_COVERAGE
    vecs, _breach, coverage = service.build_inputs(_window(["press-1"], "coolant_temp", 180), [])
    assert coverage["coolant_temp"]["press-1"] >= service.MIN_COVERAGE


def test_observe_learns_only_from_the_vectors_it_is_given(tmp_path):
    mem = GraphMemory(tmp_path / "m.db", MemoryConfig(signal="coolant_temp"))
    padded = np.concatenate([np.zeros(150), np.full(30, 58.0)])     # a refilled ring: mostly zeros
    for k in range(20):
        mem.observe({"edges": [], "findings": []}, {"press-1": padded}, ts=float(k), baseline_vectors={})
    assert mem.baseline_threshold("press-1") is None            # nothing learned from the padding
    full = 58.0 + 0.05 * rng.standard_normal(180)
    for k in range(20):
        mem.observe({"edges": [], "findings": []}, {"press-1": full}, ts=100.0 + k)
    thr = mem.baseline_threshold("press-1")
    assert thr is not None and 57.5 < thr < 59.5


def _duty(on_share, tail=24, off=26.9, on=45.0):
    """A rail-sag vector whose last `tail` samples are ON for on_share of the time, with a step onset."""
    v = off + 0.1 * rng.standard_normal(180)
    n_on = int(round(on_share * tail))
    v[-n_on:] = on + 0.1 * rng.standard_normal(n_on)
    return v


def _rail_pass(vec, gate_q):
    pods = {"compressor-1": vec, "psu-b": vec.copy()}
    w = Witness(ebpf_edges=set(), psi_copressure=set(), shared_relation={frozenset(pods)}, same_node=set(),
                relation_kind="rail")
    thr = 26.9 + 3.5 * 0.13                                   # the OFF-level band seen on the box
    return run_pass(pods, w, window=36, baselines={p: thr for p in pods}, recent=24, gate_q=gate_q)


def test_a_normal_duty_cycle_is_quiet_with_a_low_gate_quantile():
    window_on = _duty(12 / 24)                                # a 60 s ON window inside the 2 min tail
    assert _rail_pass(window_on, 90)["findings"]              # the old gate flags every ON window
    assert not _rail_pass(window_on, 35)["findings"]          # the low quantile keeps it quiet


def test_a_stuck_on_load_still_clears_a_low_gate_quantile():
    stuck = _duty(24 / 24)                                    # ON for the whole tail: PS2
    assert _rail_pass(stuck, 35)["findings"]


def test_plant_entities_keep_their_names_in_memory():
    set_verbatim_names({"qa-scanner-1", "pack-wrapper-1"})
    try:
        assert stable_workload("qa-scanner-1") == "qa-scanner-1"
        assert stable_workload("pack-wrapper-1") == "pack-wrapper-1"
        assert stable_workload("tag-server-85bb7f8497-5zc4s") == "tag-server"   # pods still collapse
    finally:
        set_verbatim_names(set())


def test_a_family_takes_the_first_source_each_member_exports(service):
    assert service.sources_of("coolant_temp") == ["heat_load", "cooling_shortfall"]
    heat, short = np.full(180, 23.0), np.full(180, 0.4)
    merged = service.source_vectors("coolant_temp", {
        "heat_load": {"press-1": heat},
        "cooling_shortfall": {"chiller-1": short, "press-1": np.zeros(180)},
    })
    assert merged["press-1"] is heat                          # heat_load wins for a machine
    assert merged["chiller-1"] is short                       # the chiller has only its shortfall


def test_a_source_only_member_gets_a_loop_pair(service):
    victims = {"press-1": np.zeros(180), "furnace-1": np.zeros(180)}
    sources = {"chiller-1": np.zeros(180)}
    pairs = service._witness_for("coolant_temp", {**sources, **victims}).shared_relation
    assert frozenset(("chiller-1", "press-1")) in pairs
    assert frozenset(("chiller-1", "furnace-1")) in pairs


def test_a_steady_temperature_inside_its_band_gets_no_trip_card():
    steady = 65.0 + 0.05 * rng.standard_normal(180)           # furnace-1 at rest: 83 % of 78 C
    steady[-24:] += np.linspace(0.0, 0.3, 24)                 # a small drift the fit could extend
    ramp = np.concatenate([np.full(150, 58.0), np.linspace(58.0, 74.0, 30)])
    vecs, limits = {"furnace-1": steady, "press-1": ramp}, {"furnace-1": 78.0, "press-1": 78.0}
    floors = {"furnace-1": 65.2, "press-1": 58.3}
    out = incipient_findings(vecs, limits, min_frac=0.5, signal="coolant_temp", cls="trip", floors=floors)
    assert [f["pod"] for f in out] == ["press-1"]             # only the real ramp gets a card


def test_a_quiet_plant_member_cannot_lead_a_bare_edge():
    t = np.arange(180, dtype=float)
    wander = 65.0 + 0.3 * np.sin(t / 20.0)                   # furnace-1: a slow wander, above a tight band
    follower = 58.0 + 0.3 * np.sin((t - 3) / 20.0)           # press-2: the same shape, not deviating
    vecs = {"furnace-1": wander, "press-2": follower}
    w = Witness(ebpf_edges=set(), psi_copressure=set(), shared_relation={frozenset(vecs)}, same_node=set(),
                relation_kind="loop")
    base = {"furnace-1": 64.0, "press-2": 70.0}               # only furnace-1 sits above its band
    loose = run_pass(vecs, w, window=36, baselines=base, recent=24)
    strict = run_pass(vecs, w, window=36, baselines=base, recent=24, bare_src_must_deviate=True)
    assert all(e["src"] == "furnace-1" for e in strict["edges"])
    assert not any(r["pod"] == "press-2" for r in strict["root_cause_ranking"])
    assert loose["findings"] == strict["findings"]            # the rule changes edges, never findings
