"""LOG-088: api/metrics.py renders the verdict as Prometheus series. Pure unit tests, no FastAPI.
test_metrics_api.py tests the GET /metrics route."""
import re

import metrics

LINE = re.compile(r'^[a-z_]+(\{([a-z_]+="([^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+$')

GRAPH = {
    "root": [{"pod": "press-1", "score": 0.92, "onset_s": 12.0}],
    "edges": [{"src": "press-1", "dst": "cnc-1", "r": 0.81, "signal": "bus_voltage"}],
    "blast_radius": [{"pod": "cnc-1", "impact": 0.5, "eta_s": 30.0}],
    "findings": [{"pod": "press-1", "class": "write"}, {"pod": "cnc-1", "class": "rail"}],
    "incipient": [{"pod": "furnace-1", "signal": "coolant_temp", "eta_s": 76.3, "headroom_frac": 0.12}],
    "meta": {"case_register": "recurrence"},
}


def _series(text):
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def test_every_line_parses_and_each_series_has_help_and_type():
    text = metrics.exposition(GRAPH, [{"kind": "unsigned_write"}], [{"asset": "press-1", "plc": "plc-stamping", "value": 55}])
    for line in _series(text):
        assert LINE.match(line), line
        name = line.split("{")[0].split(" ")[0]
        assert f"# HELP {name} " in text and f"# TYPE {name} gauge" in text


def test_verdict_values_reach_the_series():
    text = metrics.exposition(GRAPH, [], [{"asset": "press-1", "plc": "plc-stamping", "value": 55}])
    s = _series(text)
    assert "visr_engine_up 1" in s
    assert "visr_root_active 1" in s
    assert 'visr_root_score{asset="press-1"} 0.92' in s
    assert "visr_findings 2" in s
    assert 'visr_finding{asset="cnc-1",class="rail"} 1' in s
    assert 'visr_forecast_eta_seconds{asset="furnace-1",signal="coolant_temp"} 76.3' in s
    assert 'visr_forecast_headroom_ratio{asset="furnace-1",signal="coolant_temp"} 0.12' in s
    assert 'visr_edge_r{src="press-1",dst="cnc-1",signal="bus_voltage"} 0.81' in s
    assert 'visr_blast_eta_seconds{asset="cnc-1"} 30.0' in s
    assert 'visr_case_match{register="recurrence"} 1' in s
    assert 'visr_derate_pct{asset="press-1",plc="plc-stamping"} 55' in s


def test_plant_assets_never_use_the_pod_label():
    text = metrics.exposition(GRAPH, [], [])
    assert 'pod="' not in text


def test_steady_plant_gives_zero_counts_and_no_verdict_series():
    steady = {"root": [], "edges": [], "blast_radius": [], "findings": [], "incipient": [], "meta": {}}
    s = _series(metrics.exposition(steady, [], []))
    assert "visr_root_active 0" in s and "visr_findings 0" in s
    assert 'visr_integrity_open{kind="unsigned_write"} 0' in s
    assert 'visr_integrity_open{kind="current_balance"} 0' in s
    assert not [l for l in s if l.startswith(("visr_root_score", "visr_finding{", "visr_case_match", "visr_derate_pct"))]


def test_engine_down_gives_only_engine_up_zero():
    s = _series(metrics.exposition(None, None, None))
    assert s == ["visr_engine_up 0"]


def test_integrity_findings_are_counted_by_kind():
    s = _series(metrics.exposition(GRAPH, [{"kind": "unsigned_write"}, {"kind": "unsigned_write"},
                                           {"kind": "current_balance"}], []))
    assert 'visr_integrity_open{kind="unsigned_write"} 2' in s
    assert 'visr_integrity_open{kind="current_balance"} 1' in s


def test_label_values_are_escaped_and_bad_numbers_are_dropped():
    g = {"root": [{"pod": 'a"b\\c\nd', "score": 1.0}, {"pod": "x", "score": None}, {"pod": "y", "score": True}]}
    s = _series(metrics.exposition(g, [], []))
    assert 'visr_root_score{asset="a\\"b\\\\c\\nd"} 1.0' in s
    assert not [l for l in s if 'asset="x"' in l or 'asset="y"' in l]
