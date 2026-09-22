"""FLEET.md 8 fixtures: the pure fleet rules (scada/fleet.py). No PLC, no network.

Token check, enrollment parse, tag table, decode and scaling, quality aging, the write
gate, /fleet, /domains, and /metrics/fleet.
"""
import hashlib
import hmac

import pytest

import fleet
from conftest import sample_image

KEY = "0123456789abcdef-test-key"
EXTRA = [{"asset": "SYS", "signal": "PACK_COUNT", "address": "%MW20", "unit": "count"}]


def _body(**over):
    body = {
        "name": "plc-packaging",
        "profile": "generic-iec",
        "protocol": {"kind": "modbus", "host": "plc-packaging.fleet.svc.cluster.local", "port": 502,
                     "rack": None, "slot": None, "db": None},
        "task": {"name": "packaging-cell", "title": "Packaging cell sequencer",
                 "sha256": "ab" * 32, "interval_ms": 100},
        "cell": {"name": "packaging", "rail": "psu-c",
                 "machines": ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1"]},
        "io_extra": list(EXTRA),
        "runtime": {"version": "0.1", "started_at": 1758000000.0},
    }
    body.update(over)
    return body


def _table():
    return fleet.table_for(fleet.parse_enrollment(_body()))


def _snap(aged, connected=True, rtt=3.25):
    reg = fleet.parse_enrollment(_body())
    return {"reg": reg, "table": fleet.table_for(reg), "aged": aged, "enrolled_at": 10.0,
            "last_enroll_at": 40.0, "first_good_at": 11.0, "last_good_at": 100.0,
            "poll_rtt_ms": rtt, "connected": connected, "poll_error": None}


# ------------------------------------------------------------------ tokens --
def test_enroll_token_is_hex_hmac_sha256_of_the_name():
    want = hmac.new(KEY.encode(), b"plc-stamping", hashlib.sha256).hexdigest()
    assert fleet.enroll_token(KEY, "plc-stamping") == want
    assert fleet.check_enroll_token(KEY, "plc-stamping", want) is True


def test_enroll_token_rejects_bad_tokens():
    good = fleet.enroll_token(KEY, "plc-stamping")
    assert fleet.check_enroll_token(KEY, "plc-stamping", good[:-1] + "0") is (good[-1] == "0")
    assert fleet.check_enroll_token(KEY, "plc-stamping", "deadbeef") is False
    assert fleet.check_enroll_token(KEY, "plc-stamping", None) is False
    assert fleet.check_enroll_token(KEY, "plc-stamping", "") is False
    # a token for another PLC does not work, and a non-ASCII token does not raise
    assert fleet.check_enroll_token(KEY, "plc-other", good) is False
    assert fleet.check_enroll_token(KEY, "plc-stamping", "töken") is False


def test_empty_key_disables_enrollment():
    token_for_empty_key = hmac.new(b"", b"plc-stamping", hashlib.sha256).hexdigest()
    assert fleet.check_enroll_token("", "plc-stamping", token_for_empty_key) is False


def test_write_token_fails_closed():
    assert fleet.check_write_token("w-token", "w-token") is True
    assert fleet.check_write_token("w-token", "w-tokeN") is False
    assert fleet.check_write_token("", "") is False
    assert fleet.check_write_token("", None) is False


# ------------------------------------------------------------- enrollment --
def test_parse_enrollment_normalizes_protocols():
    reg = fleet.parse_enrollment(_body())
    assert reg["protocol"] == {"kind": "modbus", "host": "plc-packaging.fleet.svc.cluster.local",
                               "port": 502, "rack": None, "slot": None, "db": None, "unit": 1}
    s7 = fleet.parse_enrollment(_body(name="plc-stamping", protocol={
        "kind": "s7comm", "host": "plc-stamping.fleet.svc.cluster.local"}))
    assert s7["protocol"] == {"kind": "s7comm", "host": "plc-stamping.fleet.svc.cluster.local",
                              "port": 102, "rack": 0, "slot": 1, "db": 1}


@pytest.mark.parametrize("over", [
    {"name": "stamping"},                                        # no plc- prefix
    {"name": "plc-" + "a" * 20},                                 # too long
    {"name": "plc-x\n"},                                         # a trailing newline
    {"protocol": {"kind": "profinet", "host": "h"}},
    {"protocol": {"kind": "modbus", "host": ""}},
    {"protocol": {"kind": "modbus", "host": "h", "port": 70000}},
    {"cell": {"name": "c", "machines": [f"m-{i}" for i in range(9)]}},   # more than 8
    {"cell": {"name": "c", "machines": ["m-1", "m-1"]}},
    {"cell": {"name": "c", "machines": ["bad.name"]}},
    {"io_extra": [{"asset": "SYS", "signal": "X", "address": "%MW64"}]},
    {"io_extra": [{"asset": "SYS", "signal": "X", "address": "%IX8.0"}]},
    {"io_extra": [{"asset": "SYS", "signal": "X", "address": "%MW2", "writable": True}]},
    {"io_extra": [{"asset": "SYS", "signal": "X", "address": "%IW3", "writable": True}]},
    {"io_extra": [{"asset": "SYS", "signal": "SCAN_MS", "address": "%MW30"}]},   # duplicate name
    {"io_extra": [{"asset": "SYS", "signal": "X", "address": "%MW30", "direction": "sideways"}]},
])
def test_parse_enrollment_rejects_bad_bodies(over):
    with pytest.raises(fleet.EnrollError):
        fleet.table_for(fleet.parse_enrollment(_body(**over)))


# -------------------------------------------------------------- tag table --
def test_tag_table_counts_and_names_for_a_3_machine_cell():
    tab = _table()
    by_tag = {r["tag"]: r for r in tab}
    # 3 machines x 8 signals + CELL_ENABLE + 5 system words + 1 io_extra
    assert len(tab) == 3 * 8 + 1 + 5 + 1 == 31
    assert len(by_tag) == len(tab)
    assert sum(1 for r in tab if r["direction"] == "in") == 3 * 5
    assert sum(1 for r in tab if r["direction"] == "out") == 3 * 2 + 1       # RUN, SPEED + PACK_COUNT
    assert sum(1 for r in tab if r["direction"] == "setpoint") == 3 + 1
    assert sum(1 for r in tab if r["direction"] == "system") == 5
    assert sorted(r["tag"] for r in tab if r["writable"]) == [
        "FLEET.PLC_PACKAGING.PACKAGING.CELL_ENABLE",
        "FLEET.PLC_PACKAGING.PACK_CONVEYOR_1.DERATE_PCT",
        "FLEET.PLC_PACKAGING.PACK_LABELER_1.DERATE_PCT",
        "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT",
    ]
    # the wiring convention (FLEET.md 3.1) for machine index k = 2
    k2 = "FLEET.PLC_PACKAGING.PACK_LABELER_1."
    assert by_tag[k2 + "AMPS"]["address"] == "%IW8"
    assert by_tag[k2 + "TEMP"]["address"] == "%IW9"
    assert by_tag[k2 + "VOLTS"]["address"] == "%IW10"
    assert by_tag[k2 + "THROUGHPUT"]["address"] == "%IW11"
    assert by_tag[k2 + "READY"]["address"] == "%IX0.2"
    assert by_tag[k2 + "RUN"]["address"] == "%QX0.2"
    assert by_tag[k2 + "SPEED_PCT"]["address"] == "%QW2"
    assert by_tag[k2 + "DERATE_PCT"]["address"] == "%MW12"
    assert by_tag[k2 + "DERATE_PCT"]["min"] == 0 and by_tag[k2 + "DERATE_PCT"]["max"] == 100
    assert by_tag["FLEET.PLC_PACKAGING.PACKAGING.CELL_ENABLE"]["address"] == "%MW8"
    # the system words (3.2) and io_extra
    assert by_tag["FLEET.PLC_PACKAGING.SYS.SCAN_MS"]["address"] == "%MW0"
    assert by_tag["FLEET.PLC_PACKAGING.SYS.SCAN_MS"]["scale"] == 100
    assert by_tag["FLEET.PLC_PACKAGING.SYS.TASK_CRC"]["address"] == "%MW4"
    extra = by_tag["FLEET.PLC_PACKAGING.SYS.PACK_COUNT"]
    assert (extra["address"], extra["unit"], extra["writable"]) == ("%MW20", "count", False)
    for r in tab:
        assert set(r) >= {"tag", "address", "unit", "direction", "writable"}


def test_tag_names_follow_the_contract_examples():
    tab = fleet.tag_table("plc-stamping", ["press-1", "press-2"], "stamping")
    names = {r["tag"] for r in tab}
    assert "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT" in names
    assert "FLEET.PLC_STAMPING.SYS.SCAN_MS" in names
    assert len(tab) == 2 * 8 + 1 + 5


def test_writable_io_extra_is_a_setpoint_with_a_range():
    tab = fleet.tag_table("plc-x", [], "x", [
        {"asset": "filler-1", "signal": "fill_ms", "address": "%mw30", "unit": "ms",
         "writable": True, "min": 100, "max": 9000}])
    row = {r["tag"]: r for r in tab}["FLEET.PLC_X.FILLER_1.FILL_MS"]
    assert (row["address"], row["direction"], row["writable"]) == ("%MW30", "setpoint", True)
    assert fleet.write_plan(tab, row["tag"], 2500) == (30, 2500)
    with pytest.raises(ValueError):
        fleet.write_plan(tab, row["tag"], 50)


# ------------------------------------------------------------------ decode --
def test_decode_scales_values_and_reads_every_area():
    t = fleet.decode(_table(), sample_image(), ts=100.0)
    p = "FLEET.PLC_PACKAGING."
    assert t[p + "PACK_CONVEYOR_1.AMPS"]["value"] == 9.1
    assert t[p + "PACK_WRAPPER_1.TEMP"]["value"] == 51.3
    assert t[p + "PACK_CONVEYOR_1.VOLTS"]["value"] == 398.7
    assert t[p + "PACK_LABELER_1.VOLTS"]["value"] == -1.5              # signed INT
    assert t[p + "PACK_WRAPPER_1.THROUGHPUT"]["value"] == 55.0
    assert t[p + "PACK_WRAPPER_1.READY"]["value"] == 1.0
    assert t[p + "PACK_LABELER_1.READY"]["value"] == 0.0
    assert t[p + "PACK_CONVEYOR_1.RUN"]["value"] == 1.0
    assert t[p + "PACK_WRAPPER_1.SPEED_PCT"]["value"] == 55.0
    assert t[p + "PACK_WRAPPER_1.DERATE_PCT"]["value"] == 100.0
    assert t[p + "PACKAGING.CELL_ENABLE"]["value"] == 1.0
    assert t[p + "SYS.SCAN_MS"]["value"] == 0.21
    assert t[p + "SYS.SCAN_COUNT"]["value"] == 1234.0
    assert t[p + "SYS.STATE"]["value"] == 1.0
    assert t[p + "SYS.PACK_COUNT"]["value"] == 417.0
    assert t[p + "SYS.PACK_COUNT"]["raw"] == 417
    assert set(t) == {r["tag"] for r in _table()}                     # nothing defined but dead
    assert all(r["quality"] == "GOOD" and r["ts"] == 100.0 for r in t.values())


def test_decode_rejects_a_short_image():
    img = sample_image()
    img["mw"] = img["mw"][:10]
    with pytest.raises(ValueError):
        fleet.decode(_table(), img, ts=1.0)


def test_quality_ages_with_the_plant_rules():
    t = fleet.decode(_table(), sample_image(), ts=100.0)
    assert all(r["quality"] == "GOOD" for r in fleet.requality(t, now=110.0).values())
    assert all(r["quality"] == "STALE" for r in fleet.requality(t, now=115.0).values())
    assert all(r["quality"] == "BAD" for r in fleet.requality(t, now=131.0).values())


# ------------------------------------------------------------------- write --
def test_write_plan_allows_only_writable_tags():
    tab = _table()
    assert fleet.write_plan(tab, "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT", 55) == (11, 55)
    assert fleet.write_plan(tab, "FLEET.PLC_PACKAGING.PACKAGING.CELL_ENABLE", False) == (8, 0)
    for tag in ("FLEET.PLC_PACKAGING.SYS.SCAN_MS", "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.AMPS",
                "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.SPEED_PCT", "FLEET.PLC_PACKAGING.SYS.PACK_COUNT",
                "FLEET.PLC_OTHER.PRESS_1.DERATE_PCT", 42):
        with pytest.raises(fleet.WriteDenied):
            fleet.write_plan(tab, tag, 1)
    assert fleet.write_plan(tab, "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT", 55.0) == (11, 55)
    for bad in (101, -1, "55", None, float("nan"), 55.5):
        with pytest.raises(ValueError):
            fleet.write_plan(tab, "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT", bad)


# ------------------------------------------------------------------ bodies --
def test_fleet_json_shape_and_bad_for_unread_tags():
    tab = _table()
    fresh = fleet.decode(tab, sample_image(), ts=100.0)
    fresh.pop("FLEET.PLC_PACKAGING.SYS.PACK_COUNT")                   # never read
    body = fleet.fleet_json([_snap(fleet.requality(fresh, now=101.0))])
    assert len(body) == 1
    plc = body[0]
    for key in ("name", "profile", "protocol", "task", "cell", "enrolled_at", "first_good_at",
                "last_good_at", "poll_rtt_ms", "connected", "tags"):
        assert key in plc
    assert plc["name"] == "plc-packaging" and plc["connected"] is True
    rows = {r["tag"]: r for r in plc["tags"]}
    assert len(rows) == 31
    derate = rows["FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT"]
    assert derate == {**derate, "address": "%MW11", "unit": "pct", "direction": "setpoint",
                      "writable": True, "value": 100.0, "quality": "GOOD"}
    missing = rows["FLEET.PLC_PACKAGING.SYS.PACK_COUNT"]
    assert missing["quality"] == "BAD" and missing["value"] is None
    assert plc["tags_good"] == 30


def test_domains_lists_machines_then_the_plc():
    regs = [fleet.parse_enrollment(_body()),
            fleet.parse_enrollment(_body(name="plc-stamping", cell={
                "name": "stamping", "rail": "psu-a", "machines": ["press-1", "press-2"]}))]
    assert fleet.domains(regs) == {"domains": {
        "plc:plc-packaging": ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1", "plc-packaging"],
        "plc:plc-stamping": ["press-1", "press-2", "plc-stamping"],
    }}
    assert fleet.domains([]) == {"domains": {}}


def test_metrics_text_names_and_labels():
    tab = _table()
    aged = fleet.requality(fleet.decode(tab, sample_image(), ts=100.0), now=101.0)
    text = fleet.prom_text([_snap(aged)])
    lab = '{namespace="fleet",pod="plc-packaging"}'
    assert f"vplc_scan_time_ms{lab} 0.2100" in text
    assert f"vplc_state{lab} 1\n" in text
    assert f"vplc_overruns_total{lab} 3\n" in text
    assert f"scada_poll_rtt_ms{lab} 3.2500" in text
    assert f"scada_plc_connected{lab} 1\n" in text
    assert f"scada_tags_good{lab} 31\n" in text
    names = {line.split("{")[0] for line in text.strip().splitlines()}
    assert names == {"vplc_scan_time_ms", "vplc_state", "vplc_overruns_total", "scada_poll_rtt_ms",
                     "scada_plc_connected", "scada_tags_good"}
    assert "plant_" not in text                                       # never duplicates plant_*


def test_metrics_text_drops_bad_values_and_rtt_while_disconnected():
    tab = _table()
    aged = fleet.requality(fleet.decode(tab, sample_image(), ts=100.0), now=500.0)   # all BAD
    text = fleet.prom_text([_snap(aged, connected=False)])
    lab = '{namespace="fleet",pod="plc-packaging"}'
    assert text.strip().splitlines() == [f"scada_plc_connected{lab} 0", f"scada_tags_good{lab} 0"]
    assert fleet.prom_text([]) == ""
