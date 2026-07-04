"""Unit suite for the correlation engine core — BUILD_GUIDE P4 step 1-3 fixtures.

Every test plants a known truth and asserts the engine rediscovers it blind.
"""
import numpy as np
import pytest

from engine import detectors
from engine.gate import Witness, accept_edge
from engine.lagcorr import best_directed, lag_profile
from engine.pipeline import run_pass

rng = np.random.default_rng(42)
N = 180  # 15-minute window at 5s


def noise(scale=1.0, n=N):
    return rng.normal(0, scale, n)


def planted_step(onset=100, level=8.0, n=N):
    x = noise()
    x[onset:] += level
    return x


# ---------- A1: changepoints ----------

def test_cusum_finds_planted_onset_within_2_samples():
    x = planted_step(onset=100)
    ons = detectors.cusum_onsets(x)
    assert ons, "no onset detected"
    assert abs(ons[0]["idx"] - 100) <= 2
    assert ons[0]["direction"] == "up"


def test_cusum_silent_on_pure_noise():
    assert detectors.cusum_onsets(noise()) == []


def test_classify_burst_vs_leak():
    burst = noise(0.3)
    burst[80:110] += 6.0  # rises and returns
    leak = noise(0.3)
    leak[60:] += np.linspace(0, 10, N - 60)  # monotonic climb
    assert detectors.classify(burst, 80) == "burst"
    assert detectors.classify(leak, 60) == "leak"


def test_forecast_to_limit_predicts_rising_signal():
    x = np.linspace(0, 50, N) + noise(0.1)
    eta = detectors.forecast_to_limit(x, limit=60.0)
    assert eta is not None and 0 < eta < 300
    assert detectors.forecast_to_limit(noise(), limit=100.0) is None


# ---------- A4: lag correlation ----------

def test_lag_recovered_for_shifted_pair():
    base = noise(0.2)
    base[60:90] += 5.0
    lag_samples = 3  # 15s
    follower = np.roll(base, lag_samples) + noise(0.2)
    d = best_directed(base, follower)
    assert d["forward"] is True, "direction wrong: leader not identified"
    assert d["lag_s"] == 15
    assert abs(d["r"]) > 0.7


def test_direction_flips_when_arguments_swap():
    base = noise(0.2)
    base[60:90] += 5.0
    follower = np.roll(base, 6) + noise(0.2)  # 30s lag
    d = best_directed(follower, base)
    assert d["forward"] is False
    assert d["lag_s"] == 30


# ---------- gate ----------

def _hot_pair():
    a = noise(0.2)
    a[60:90] += 5.0
    b = np.roll(a, 3) + noise(0.2)
    d = best_directed(a, b)
    return a, b, d


def test_gate_rejects_without_physical_witness():
    _, _, d = _hot_pair()
    e = accept_edge("a", "b", d["r"], d["lag_s"], d["profile"], Witness(), {"a": 300.0, "b": 315.0})
    assert e is None


def test_gate_accepts_with_witness_and_ordering():
    _, _, d = _hot_pair()
    w = Witness(ebpf_edges={("a", "b")})
    e = accept_edge("a", "b", d["r"], d["lag_s"], d["profile"], w, {"a": 300.0, "b": 315.0})
    assert e is not None
    assert "ebpf" in e["evidence"] and "stat" in e["evidence"] and "temporal" in e["evidence"]


def test_gate_rejects_wrong_temporal_order():
    _, _, d = _hot_pair()
    w = Witness(ebpf_edges={("a", "b")})
    e = accept_edge("a", "b", d["r"], d["lag_s"], d["profile"], w, {"a": 400.0, "b": 315.0})
    assert e is None


def test_gate_rejects_anticorrelation():
    # a strong NEGATIVE correlation is competition/coincidence, not a causal cascade,
    # so it is rejected even with a witness and correct ordering.
    a = noise(0.2)
    a[60:90] += 5.0
    b = -np.roll(a, 3) + noise(0.2)            # anti-correlated follower
    d = best_directed(a, b)
    assert d["r"] < 0                          # the strongest coupling is negative
    w = Witness(ebpf_edges={("a", "b")})
    e = accept_edge("a", "b", d["r"], d["lag_s"], d["profile"], w, {"a": 300.0, "b": 315.0})
    assert e is None


# ---------- end-to-end: the S1-shaped chain ----------

def chain_vectors():
    """coolmon -> dcim (15s) -> tsdb (30s) -> ccr (30s), plus an innocent bystander."""
    coolmon = noise(0.2)
    coolmon[60:100] += 6.0
    dcim = np.roll(coolmon, 3) * 0.9 + noise(0.25)
    tsdb = np.roll(dcim, 6) * 0.85 + noise(0.25)
    ccr = np.roll(tsdb, 6) * 0.8 + noise(0.25)
    return {
        "coolmon": coolmon, "dcim": dcim, "tsdb": tsdb, "ccr": ccr,
        "edge-ui": noise(),  # must stay out of the story
    }


def s1_witness():
    return Witness(
        ebpf_edges={("tsdb", "ccr")},
        psi_copressure={frozenset(("coolmon", "dcim")), frozenset(("dcim", "tsdb"))},
        shared_relation={frozenset(("coolmon", "dcim")), frozenset(("coolmon", "tsdb"))},
    )


def test_s1_chain_root_cause_is_coolmon():
    out = run_pass(chain_vectors(), s1_witness(), slo_breach=["ccr"])
    assert out["root_cause_ranking"], "no root cause produced"
    assert out["root_cause_ranking"][0]["pod"] == "coolmon"
    assert len(out["edges"]) >= 3, f"chain too short: {out['edges']}"


def test_s1_blast_radius_reaches_ccr():
    out = run_pass(chain_vectors(), s1_witness(), slo_breach=["ccr"])
    blast_pods = {b["pod"] for b in out["blast_radius"]}
    assert "ccr" in blast_pods or "tsdb" in blast_pods


def test_s0_idle_produces_no_edges_and_no_root_cause():
    vecs = {f"pod{i}": noise() for i in range(8)}
    w = Witness(shared_relation={frozenset((f"pod{i}", f"pod{j}")) for i in range(8) for j in range(8) if i < j})
    out = run_pass(vecs, w)
    assert out["edges"] == []
    assert out["root_cause_ranking"] == []


def test_innocent_bystander_never_in_edges():
    out = run_pass(chain_vectors(), s1_witness(), slo_breach=["ccr"])
    for e in out["edges"]:
        assert "edge-ui" not in (e["src"], e["dst"])


# ---------- source attribution: the aggressor writes, the victims stall ----------

def writer_victim_vectors():
    """src hogs the disk (spiky io_write, flat psi); v1/v2 stall (spiky psi, flat write)."""
    spike = np.zeros(N)
    spike[70:110] += 6.0
    psi = {
        "src": noise(0.1),                       # the source barely stalls -> invisible in psi
        "v1": np.roll(spike, 1) + noise(0.2),    # victims stall, lagging the write storm (5s)
        "v2": np.roll(spike, 3) + noise(0.2),    # (15s)
    }
    write = {
        "src": spike + noise(0.2),               # the source writes hard (the hog)
        "v1": noise(0.1),
        "v2": noise(0.1),
    }
    return psi, write


def writer_witness():
    return Witness(shared_relation={
        frozenset(("src", "v1")), frozenset(("src", "v2")), frozenset(("v1", "v2")),
    })


def test_source_attribution_blames_writer_not_staller():
    psi, write = writer_victim_vectors()
    w = writer_witness()
    # psi alone is blind to the source (flat psi) -> a victim gets blamed
    base = run_pass(psi, w, slo_breach=["v1", "v2"])
    assert base["root_cause_ranking"], "expected a victim<->victim edge"
    assert base["root_cause_ranking"][0]["pod"] != "src"
    # with the write signal, the writer is correctly the root
    out = run_pass(psi, w, slo_breach=["v1", "v2"], write_vectors=write)
    assert out["root_cause_ranking"][0]["pod"] == "src"
    src_edges = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("src", "v1") in src_edges and ("src", "v2") in src_edges
    assert any("write" in e["evidence"] for e in out["edges"])


def test_baseline_gate_suppresses_within_normal_and_flags_deviation():
    # 'a' only bumps within its normal band; 'b' makes a clear excursion above it.
    a = noise(0.2); a[60:90] += 1.0      # peak ~1.2
    b = noise(0.2); b[60:90] += 8.0      # peak ~8
    vecs = {"a": a, "b": b}
    w = Witness(shared_relation={frozenset(("a", "b"))})
    # both detected when ungated:
    f0 = {f["pod"] for f in run_pass(vecs, w)["findings"]}
    assert "a" in f0 and "b" in f0
    # with per-pod incident thresholds (3.0), only the real excursion is an incident:
    out = run_pass(vecs, w, baselines={"a": 3.0, "b": 3.0})
    finds = {f["pod"] for f in out["findings"]}
    assert "b" in finds and "a" not in finds
    # an immature baseline (None value) is treated as 'still learning' -> not an incident:
    assert run_pass(vecs, w, baselines={"a": None, "b": None})["findings"] == []


def test_source_attribution_picks_the_dominant_writer():
    # A and B both stall AND both write the same shape -> both directions would correlate;
    # only A out-writes B (100x), so only A may be the source (kills minor-writer false roots).
    shape = np.zeros(N)
    shape[70:110] += 1.0
    psi = {"A": np.roll(shape, 1) + noise(0.2), "B": np.roll(shape, 1) + noise(0.2)}
    write = {"A": shape * 100 + noise(0.2), "B": shape * 1.0 + noise(0.05)}
    w = Witness(shared_relation={frozenset(("A", "B"))})
    out = run_pass(psi, w, slo_breach=["A", "B"], write_vectors=write)
    pairs = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("A", "B") in pairs            # the dominant writer is the source
    assert ("B", "A") not in pairs         # the minor writer is NOT attributed as a source
    assert out["root_cause_ranking"][0]["pod"] == "A"


def test_steady_writer_without_a_storm_is_not_a_source():
    # B's write correlates with A's stall but has NO onset (low-amplitude, steady-ish) ->
    # B is doing its routine job, not storming, so it must NOT be blamed (rule b).
    shape = np.zeros(N)
    shape[70:110] += 1.0
    psi = {"A": shape * 5 + noise(0.3), "B": noise(0.3)}      # A stalls hard, B does not
    write = {"A": noise(0.1), "B": shape * 0.6 + noise(0.3)}  # B's write tracks A but never storms
    assert not [o for o in detectors.cusum_onsets(write["B"]) if abs(o["zpeak"]) >= 3.0]
    w = Witness(shared_relation={frozenset(("A", "B"))})
    out = run_pass(psi, w, slo_breach=["A"], write_vectors=write)
    assert ("B", "A") not in {(e["src"], e["dst"]) for e in out["edges"]}


def test_anticorrelated_writer_is_not_a_source():
    # B storms (clear write onset) but its write is ANTI-correlated with A's stall
    # (write up -> stall down). That is not contention, so no source edge (rule c).
    shape = np.zeros(N)
    shape[70:110] += 1.0
    psi = {"A": 3.0 - np.roll(shape, 1) * 1.0 + noise(0.2), "B": noise(0.2)}  # A's stall DROPS
    write = {"A": noise(0.1), "B": shape * 50 + noise(0.2)}                   # B writes hard
    w = Witness(shared_relation={frozenset(("A", "B"))})
    out = run_pass(psi, w, slo_breach=["A"], write_vectors=write)
    assert ("B", "A") not in {(e["src"], e["dst"]) for e in out["edges"]}


def test_recent_window_clears_a_stale_storm():
    # Appended LAST on purpose: noise() draws from a shared module RNG, so a test inserted
    # earlier would shift the RNG sequence of the tests after it.
    # 'a' storms in the RECENT tail; 'b' stormed in the OLD part of the ring and has since cooled.
    a = noise(0.2); a[N - 24:] += 8.0      # current storm (last 24 samples)
    b = noise(0.2); b[40:78] += 8.0        # old storm (38 samples), recent tail back to normal
    vecs = {"a": a, "b": b}
    w = Witness(shared_relation={frozenset(("a", "b"))})
    thr = {"a": 3.0, "b": 3.0}
    # full ring (recent=None): the old storm still has enough mass to register as a finding
    assert "b" in {f["pod"] for f in run_pass(vecs, w, baselines=thr)["findings"]}
    # recent gate: only the pod still deviating in the recent tail is a live incident ->
    # a finished storm clears the verdict ~recent samples after it ends (fast reset)
    rec = {f["pod"] for f in run_pass(vecs, w, baselines=thr, recent=24)["findings"]}
    assert "a" in rec and "b" not in rec


def test_same_node_cpu_source_edge_has_no_network_path():
    # ROAD Stage 2 / D-015: a CPU hog leads two co-residents' stall on the SAME NODE (no disk, no
    # network). The source edge forms over same-node coupling; evidence carries `node`, never pvc/ebpf.
    shape = np.zeros(N); shape[70:110] += 1.0
    psi = {"v1": np.roll(shape, 1) + noise(0.2),     # victims stall on cpu
           "v2": np.roll(shape, 1) + noise(0.2),
           "src": noise(0.05)}                       # the hog barely stalls itself
    usage = {"src": shape * 100 + noise(0.2),        # but it out-uses both 100x and leads
             "v1": shape * 1.0 + noise(0.05),
             "v2": shape * 1.0 + noise(0.05)}
    w = Witness(same_node={frozenset(("src", "v1")), frozenset(("src", "v2")), frozenset(("v1", "v2"))})
    out = run_pass(psi, w, slo_breach=["v1", "v2"], write_vectors=usage)
    assert out["root_cause_ranking"][0]["pod"] == "src"
    pairs = {(e["src"], e["dst"]) for e in out["edges"]}
    assert ("src", "v1") in pairs and ("src", "v2") in pairs
    assert all("node" in e["evidence"] and "pvc" not in e["evidence"] and "ebpf" not in e["evidence"]
               for e in out["edges"])


def test_same_node_bare_psi_pair_forms_no_edge():
    # Same-node co-stall WITHOUT a deviating source (no write_vectors) must NOT form an edge --
    # same-node is excluded from gate.couples(), so a victim<->victim cascade never appears.
    shape = np.zeros(N); shape[70:110] += 1.0
    psi = {"a": np.roll(shape, 1) + noise(0.2), "b": shape + noise(0.2)}
    w = Witness(same_node={frozenset(("a", "b"))})
    out = run_pass(psi, w, slo_breach=["a", "b"])
    assert out["edges"] == []


def test_source_edge_requires_a_real_victim_not_a_bystander():
    # The cpu false-edge storm: same-node couples everything, so a hog whose usage happens to
    # correlate with a NON-deviating pod must NOT connect to it. Only a pod that deviates from its
    # OWN baseline is a victim. ('bystander' carries the same shape -> would correlate -> but its
    # band is high enough that the shape is within normal, so it is not an incident.)
    shape = np.zeros(N); shape[70:110] += 1.0
    psi = {"hog": noise(0.05),                         # the hog barely stalls itself
           "realvictim": np.roll(shape, 1) + noise(0.2),
           "bystander": np.roll(shape, 1) + noise(0.2)}  # correlates, but not a deviation (see thr)
    usage = {"hog": shape * 100 + noise(0.2), "realvictim": noise(0.05), "bystander": noise(0.05)}
    w = Witness(same_node={frozenset(("hog", "realvictim")), frozenset(("hog", "bystander")),
                           frozenset(("realvictim", "bystander"))})
    thr = {"hog": 3.0, "realvictim": 0.5, "bystander": 50.0}   # bystander's band swallows the shape
    out = run_pass(psi, w, slo_breach=None, write_vectors=usage, baselines=thr)
    dsts = {e["dst"] for e in out["edges"]}
    assert "realvictim" in dsts        # real victim is connected to the hog
    assert "bystander" not in dsts     # bystander not connected despite same-node + correlation


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
