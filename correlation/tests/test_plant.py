"""2C' plant-family fixtures — the domain-witness discipline (LOG-033).

Every test plants a known plant-physics truth and asserts the engine rediscovers it blind:
- co-deviation WITHOUT a shared declared domain forms NO edge (the regression case);
- a declared rail pair with the aggressor's current_draw leading forms the SOURCE edge with
  honest evidence ("rail", never "pvc"); root = the load-carrier (role asymmetry, no coin-flip);
- the thermal ramp projects to the trip threshold (the PS5 card) via the generalized forecaster.
"""
import numpy as np

from engine.forecast import incipient_findings
from engine.gate import Witness
from engine.pipeline import run_pass

rng = np.random.default_rng(7)
N = 180  # 15-minute window at 5s


def noise(scale=1.0, n=N):
    return rng.normal(0, scale, n)


def sag_step(onset=100, level=14.0):
    """A rail-sag vector as build_inputs ingests it (sag = nominal - volts): a ~39 V steady
    band that steps up when the rail is dragged down."""
    x = noise(0.4) + 39.0
    x[onset:] += level
    return x


def rail_witness(*pods):
    pairs = {frozenset((a, b)) for i, a in enumerate(pods) for b in pods[i + 1:]}
    return Witness(shared_relation=pairs, relation_kind="rail")


def test_no_shared_domain_no_edge():
    """Two entities co-deviate simultaneously but share NO declared domain -> no edge, ever.
    (Deviation is still real, so both remain FINDINGS — safe by construction.)"""
    v = {"press-1": sag_step(), "conveyor-1": sag_step()}  # different rails: empty witness
    out = run_pass(v, Witness(relation_kind="rail"))
    assert out["edges"] == []
    assert len(out["findings"]) == 2


def test_rail_pair_source_edge_with_honest_evidence():
    """press-1's current_draw ramps and leads its rail-mates' sag -> SOURCE edges press-1 -> victims
    carrying 'write'+'rail' evidence; the root is the load-carrier; nothing wears a 'pvc' chip."""
    onset = 100
    v = {
        "press-1": sag_step(onset=onset),
        "cnc-1": sag_step(onset=onset + 2),
        "qa-scanner-1": sag_step(onset=onset + 2),
    }
    draw = {"press-1": noise(0.3) + 42.0}
    draw["press-1"][onset - 1:] += 38.0  # the amps step leads the sag
    w = rail_witness("press-1", "cnc-1", "qa-scanner-1")
    out = run_pass(v, w, write_vectors=draw)

    src_edges = [e for e in out["edges"] if "write" in e["evidence"]]
    assert src_edges, f"no source edge formed: {out['edges']}"
    assert all(e["src"] == "press-1" for e in src_edges)
    assert any("rail" in e["evidence"] for e in src_edges)
    assert not any("pvc" in e["evidence"] for e in out["edges"])
    roots = out["root_cause_ranking"]
    assert roots and roots[0]["pod"] == "press-1"


def test_trip_forecast_generalized():
    """coolant_temp climbing toward the 78C trip threshold yields an incipient 'trip' card with
    a finite ETA; a flat neighbour on the same loop stays silent."""
    ramp = 58.0 + noise(0.2)
    ramp[60:] = 58.0 + np.linspace(0.0, 18.0, N - 60)  # climbing toward the trip
    vecs = {"press-1": ramp, "furnace-1": 58.0 + noise(0.2)}
    limits = {"press-1": 78.0, "furnace-1": 78.0}  # the service broadcasts the loop-wide limit
    out = incipient_findings(vecs, limits, horizon_s=900, min_frac=0.5,
                             signal="coolant_temp", cls="trip")
    assert [f["pod"] for f in out] == ["press-1"]
    assert out[0]["class"] == "trip" and out[0]["signal"] == "coolant_temp"
    assert 0 < out[0]["eta_s"] < 900
