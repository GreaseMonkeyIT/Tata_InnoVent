from engine.merge import merge_graphs


def _io_graph():
    """A psi_io incident: cooling-monitor -> timescaledb, plus a steady-backbone held edge."""
    return {
        "findings": [
            {"pod": "cooling-monitor", "class": "flap", "onset_s": 70.0, "severity": 0.7},
            {"pod": "timescaledb", "class": "shift", "onset_s": 105.0, "severity": 0.6},
        ],
        "edges": [
            {"src": "cooling-monitor", "dst": "timescaledb", "r": 0.69, "lag_s": 30,
             "evidence": ["write", "pvc", "temporal"], "state": "active", "source": "live"},
        ],
        "root_cause_ranking": [{"pod": "cooling-monitor", "score": 1.0, "onset_s": 70.0}],
        "blast_radius": [{"pod": "timescaledb", "impact": 0.48, "eta_s": 30}],
        "meta": {"pods": 13, "active": 2, "accepted_edges": 1, "held_edges": 0,
                 "edge_memory": 3, "cases": 2, "families": 1,
                 "case_id": "io1", "case_family": "famio", "case_register": "recurrence", "case_sim": 0.94},
    }


def _cpu_findings_only():
    """A psi_cpu disturbance with findings but NO edges (no CPU coupling witness until Stage 2)."""
    return {
        "findings": [{"pod": "analytics-batch", "class": "burst", "onset_s": 60.0, "severity": 0.9}],
        "edges": [],
        "root_cause_ranking": [],
        "blast_radius": [],
        "meta": {"pods": 13, "active": 1, "accepted_edges": 0, "held_edges": 0,
                 "edge_memory": 0, "cases": 0, "families": 0},
    }


def test_merge_tags_edges_and_findings_with_signal():
    out = merge_graphs({"psi_io": _io_graph(), "psi_cpu": _cpu_findings_only()})
    assert {e["signal"] for e in out["edges"]} == {"psi_io"}
    assert {f["signal"] for f in out["findings"]} == {"psi_io", "psi_cpu"}
    assert out["meta"]["signals"] == ["psi_cpu", "psi_io"]


def test_merge_ranks_over_union_and_keeps_io_root():
    out = merge_graphs({"psi_io": _io_graph(), "psi_cpu": _cpu_findings_only()})
    assert out["root_cause_ranking"][0]["pod"] == "cooling-monitor"
    assert out["blast_radius"] and out["blast_radius"][0]["pod"] == "timescaledb"


def test_merge_sums_stats_and_surfaces_active_case():
    out = merge_graphs({"psi_io": _io_graph(), "psi_cpu": _cpu_findings_only()})
    m = out["meta"]
    assert m["cases"] == 2 and m["families"] == 1 and m["edge_memory"] == 3
    assert m["active"] == 3                      # 3 distinct pods across both signals
    assert m["accepted_edges"] == 1
    assert m["case_register"] == "recurrence"    # from the signal owning the root (psi_io)
    assert m["signal"] == "psi_io"               # active signal label


def test_merge_idle_backbone_has_no_root():
    """Steady backbone edges with no findings must not invent a root cause."""
    backbone = {
        "findings": [],
        "edges": [{"src": "cooling-monitor", "dst": "timescaledb", "r": 0.2, "lag_s": 0,
                   "evidence": ["pvc"], "state": "steady", "source": "memory"}],
        "root_cause_ranking": [],
        "blast_radius": [],
        "meta": {"pods": 13, "active": 0, "accepted_edges": 1, "held_edges": 1},
    }
    out = merge_graphs({"psi_io": backbone, "psi_cpu": _cpu_findings_only()})
    assert out["edges"] and out["edges"][0]["signal"] == "psi_io"
    assert out["root_cause_ranking"] == []
    assert out["blast_radius"] == []


def test_merge_cross_signal_keeps_both_edges():
    cpu = {
        "findings": [{"pod": "analytics-batch", "class": "burst", "onset_s": 50.0, "severity": 0.8},
                     {"pod": "vision-qc", "class": "shift", "onset_s": 70.0, "severity": 0.5}],
        "edges": [{"src": "analytics-batch", "dst": "vision-qc", "r": 0.7, "lag_s": 5,
                   "evidence": ["write", "psi"], "state": "active", "source": "live"}],
        "root_cause_ranking": [{"pod": "analytics-batch", "score": 1.0, "onset_s": 50.0}],
        "blast_radius": [{"pod": "vision-qc", "impact": 0.49, "eta_s": 5}],
        "meta": {"pods": 13, "active": 2, "accepted_edges": 1, "edge_memory": 1, "cases": 1, "families": 1},
    }
    out = merge_graphs({"psi_io": _io_graph(), "psi_cpu": cpu})
    sigs = sorted(e["signal"] for e in out["edges"])
    assert sigs == ["psi_cpu", "psi_io"]
    assert {r["pod"] for r in out["root_cause_ranking"]} == {"cooling-monitor", "analytics-batch"}
