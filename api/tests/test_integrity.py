"""SCENARIOS.md 5.2 integrity checks: pure functions, no HTTP. Passes are driven by hand."""
import fleet
import integrity

TAG = "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT"
AMPS = "FLEET.PLC_STAMPING.PRESS_1.AMPS"


def _fleet(value=100, quality="GOOD", started=1000.0, enrolled=2000.0, sha="abc", amps=(42.0, 38.0),
           amps_q="GOOD"):
    tags = [{"tag": TAG, "asset": "press-1", "signal": "DERATE_PCT", "address": "%MW10", "writable": True,
             "min": 0, "max": 100, "scale": 1, "value": value, "quality": quality},
            {"tag": AMPS, "asset": "press-1", "signal": "AMPS", "address": "%IW0", "writable": False,
             "scale": 10, "value": amps[0], "quality": amps_q},
            {"tag": "FLEET.PLC_STAMPING.PRESS_2.AMPS", "asset": "press-2", "signal": "AMPS", "address": "%IW1",
             "writable": False, "scale": 10, "value": amps[1], "quality": "GOOD"}]
    return [{"name": "plc-stamping", "connected": True, "task": {"sha256": sha}, "enrolled_at": enrolled,
             "runtime": {"started_at": started}, "cell": {"name": "stamping", "machines": ["press-1", "press-2"]},
             "tags": tags}]


def _row(verb, status, ts, target="press-1", actor="operator", **ev):
    return {"ts": ts, "actor": actor, "verb": verb, "target": target, "status": status, "evidence": ev}


def _pass(state, now, intents=(), rows=(), **kw):
    return integrity.reconcile(state, _fleet(**kw), list(intents), list(rows), now)


# ------------------------------------------------------ unsigned setpoints --
def test_first_sighting_is_a_baseline():
    s = integrity.new_state()
    assert _pass(s, 0, value=55) == []
    assert _pass(s, 100, value=55) == []
    assert integrity.open_findings(s) == []


def test_change_explained_by_a_write_intent():
    s = integrity.new_state()
    _pass(s, 0)
    intents = [integrity.intent("plc-stamping", TAG, 55, 4.0)]
    assert _pass(s, 5, intents, value=55.0) == []
    assert _pass(s, 100, intents, value=55.0) == []           # accepted, so the grace never runs out
    assert s["setpoints"][f"plc-stamping|{TAG}"]["accepted"] == 55.0


def test_change_explained_by_a_row_that_lands_after_the_change():
    s = integrity.new_state()
    _pass(s, 0)
    assert _pass(s, 5, value=55.0) == []                      # no row yet: a suspect, not a finding
    rows = [_row("execute", "executed", 9.0, plc="plc-stamping", tag=TAG, to=55)]
    assert _pass(s, 10, rows=rows, value=55.0) == []
    assert _pass(s, 60, rows=rows, value=55.0) == []
    restore = rows + [_row("restore", "restored", 70.0, plc="plc-stamping", tag=TAG, to=100)]
    assert _pass(s, 71, rows=restore, value=100.0) == []
    assert integrity.open_findings(s) == []


def test_a_trigger_row_or_a_row_for_another_tag_explains_nothing():
    s = integrity.new_state()
    _pass(s, 0)
    rows = [_row("trigger", "fired", 4.0, target="PS4A", root="press-1", evidence=["rail"]),
            _row("execute", "executed", 4.0, plc="plc-stamping", tag="FLEET.PLC_STAMPING.PRESS_2.DERATE_PCT", to=30),
            _row("execute", "refused: the verdict changed", 4.0, plc="plc-stamping", tag=TAG, to=30)]
    _pass(s, 5, rows=rows, value=30.0)
    events = _pass(s, 41, rows=rows, value=30.0)
    assert [e["status"] for e in events] == ["detected"]


def test_plc_restart_explains_a_reset_to_the_task_default():
    s = integrity.new_state()
    _pass(s, 0, value=55.0)                                   # a held derate is the baseline
    assert _pass(s, 5, value=100.0) == []                     # the value lands before the new runtime
    assert _pass(s, 10, value=100.0, started=1234.0) == []    # the restart explains it
    assert _pass(s, 60, value=100.0, started=1234.0) == []
    assert integrity.open_findings(s) == []


def test_restart_does_not_explain_a_value_that_is_not_the_default():
    s = integrity.new_state()
    _pass(s, 0)
    _pass(s, 5, value=30.0, started=1234.0)
    events = _pass(s, 41, value=30.0, started=1234.0)
    assert [e["status"] for e in events] == ["detected"]


def test_load_task_row_explains_a_reset_to_the_default():
    s = integrity.new_state()
    _pass(s, 0, value=55.0)
    rows = [_row("load-task", "loaded", 6.0, target="plc-stamping", task="stamping-line")]
    assert _pass(s, 5, rows=rows, value=100.0) == []
    assert _pass(s, 60, rows=rows, value=100.0) == []


def test_unexplained_change_opens_after_the_grace_and_dedupes():
    s = integrity.new_state()
    _pass(s, 0)
    assert _pass(s, 5, value=30.0) == []
    assert _pass(s, 39, value=30.0) == []
    events = _pass(s, 40, value=30.0)
    assert len(events) == 1
    e = events[0]
    assert (e["verb"], e["target"], e["status"]) == ("unsigned", TAG, "detected")
    assert set(e["evidence"]) == set(integrity.UNSIGNED_EV)
    ev = e["evidence"]
    assert (ev["plc"], ev["asset"], ev["from"], ev["to"], ev["observed_at"], ev["reason"]) == \
        ("plc-stamping", "press-1", 100.0, 30.0, 5, "no ledger row")
    assert _pass(s, 45, value=30.0) == []                     # one open finding per (plc, tag)
    assert _pass(s, 50, value=25.0) == []                     # it moves, still one finding
    [f] = integrity.open_findings(s)
    assert (f["kind"], f["status"], f["tag"], f["to"]) == ("unsigned_write", "open", TAG, 25.0)


def test_a_change_that_goes_back_before_the_grace_never_opens():
    s = integrity.new_state()
    _pass(s, 0)
    _pass(s, 5, value=0.0)                                    # a restart can pass through 0
    assert _pass(s, 10, value=100.0) == []
    assert _pass(s, 60, value=100.0) == []


def test_out_of_range_opens_at_once():
    s = integrity.new_state()
    _pass(s, 0)
    [e] = _pass(s, 5, value=150.0)
    assert e["status"] == "detected" and e["evidence"]["reason"] == "out of range"


def test_finding_closes_on_a_signed_write_or_a_return_to_the_signed_value():
    s = integrity.new_state()
    _pass(s, 0)
    _pass(s, 5, value=30.0)
    _pass(s, 40, value=30.0)
    [e] = _pass(s, 50, [integrity.intent("plc-stamping", TAG, 100, 49.0)], value=100.0)
    assert e["status"] == "cleared" and e["evidence"]["from"] == 30.0 and e["evidence"]["to"] == 100.0
    assert e["evidence"]["reason"] == "write intent"
    assert integrity.open_findings(s) == []
    _pass(s, 60, value=30.0)
    _pass(s, 95, value=30.0)
    [e] = _pass(s, 100, value=100.0)                          # back to the signed value, no row needed
    assert e["status"] == "cleared" and e["evidence"]["reason"] == "returned to the signed value"


def test_stale_rows_neither_move_nor_flag():
    s = integrity.new_state()
    _pass(s, 0)
    for t in range(5, 200, 5):
        assert _pass(s, t, value=30.0, quality="STALE") == []
    assert _pass(s, 205, value=100.0) == []
    assert integrity.open_findings(s) == []


def test_api_restart_takes_the_open_finding_back_from_the_ledger():
    rows = [_row("unsigned", "detected", 40.0, target=TAG, actor="visr", plc="plc-stamping", asset="press-1",
                 tag=TAG, address="%MW10", **{"from": 100.0, "to": 30.0}, observed_at=5.0,
                 reason="no ledger row", clients=["rogue-ews"])]
    s = integrity.new_state()
    assert _pass(s, 500, rows=rows, value=30.0) == []         # no second row
    [f] = integrity.open_findings(s)
    assert f["to"] == 30.0 and f["from"] == 100.0 and f["clients"] == ["rogue-ews"]
    [e] = _pass(s, 505, rows=rows, value=100.0)
    assert e["status"] == "cleared"


def test_caretta_clients_skip_the_tag_server():
    result = [{"metric": {"client_name": "rogue-ews", "server_name": "plc-stamping", "server_port": "102"}},
              {"metric": {"client_name": "tag-server", "server_name": "plc-stamping", "server_port": "102"}},
              {"metric": {"client_name": "api", "server_name": "plc-stamping", "server_port": "8080"}}]
    assert integrity.caretta_clients(result, "plc-stamping") == ["rogue-ews"]


# --------------------------------------------------------- current balance --
PLANT = {"rails": {"psu-a": {"volts": 345.0, "amps": 111.0}},
         "devices": {"press-1": {"rail": "psu-a"}, "press-2": {"rail": "psu-a"},
                     "cnc-1": {"rail": "psu-a"}, "qa-scanner-1": {"rail": "psu-a"}}}


def _base(press1=42.0, q="GOOD"):
    vals = {"press-1": press1, "press-2": 38.0, "cnc-1": 25.0, "qa-scanner-1": 6.0}
    return [{"tag": f"PLANT.{a.upper().replace('-', '_')}.AMPS", "asset": a, "signal": "AMPS", "kind": "measured",
             "value": v, "quality": q if a == "cnc-1" else "GOOD"} for a, v in vals.items()]


def _plant(feeder):
    return {**PLANT, "rails": {"psu-a": {"volts": 345.0, "amps": feeder}}}


def test_balanced_rail_stays_silent():
    s = integrity.new_state()
    for t in range(0, 60, 5):
        assert integrity.balance(s, PLANT, _fleet(), _base(), t) == []


def test_replayed_channel_opens_after_three_passes_and_names_the_controller_channel():
    s = integrity.new_state()
    # PS4B: press-1 really draws 61 A. The feeder and the base channel say so, the PLC channel replays 42 A.
    lying = dict(plant=_plant(130.0), fleet=_fleet(amps=(42.0, 38.0)), base=_base(press1=61.0))
    for t in (0, 5):
        assert integrity.balance(s, lying["plant"], lying["fleet"], lying["base"], t) == []
    [e] = integrity.balance(s, lying["plant"], lying["fleet"], lying["base"], 10)
    assert (e["verb"], e["target"], e["status"]) == ("balance", "psu-a", "mismatch")
    assert e["evidence"] == {"rail": "psu-a", "feeder_amps": 130.0, "reported_amps": 111.0, "gap_amps": 19.0,
                             "channel": AMPS}
    [f] = integrity.open_findings(s)
    assert (f["kind"], f["asset"], f["plc"]) == ("current_balance", "press-1", "plc-stamping")
    assert integrity.balance(s, lying["plant"], lying["fleet"], lying["base"], 15) == []    # dedupe
    for t in (20, 25):
        assert integrity.balance(s, PLANT, _fleet(), _base(), t) == []
    [e] = integrity.balance(s, PLANT, _fleet(), _base(), 30)
    assert e["status"] == "cleared" and integrity.open_findings(s) == []


def test_gap_with_agreeing_channels_names_the_rail_only():
    s = integrity.new_state()
    for t in (0, 5, 10):
        events = integrity.balance(s, _plant(125.0), _fleet(), _base(), t)
    assert events[0]["evidence"]["channel"] is None


def test_a_transient_or_a_stale_input_never_opens():
    s = integrity.new_state()
    seq = [(_plant(130.0), _base()), (_plant(130.0), _base()), (PLANT, _base()),
           (_plant(130.0), _base()), (_plant(130.0), _base()), (_plant(130.0), _base(q="STALE")),
           (_plant(130.0), _base()), (_plant(130.0), _base())]
    for t, (p, b) in enumerate(seq):
        assert integrity.balance(s, p, _fleet(), b, t * 5) == [], t


# ------------------------------------------------------------ act-loop hooks --
def test_blocked_assets_and_proposals():
    unsigned = {"kind": "unsigned_write", "plc": "plc-stamping", "asset": "press-1", "tag": TAG}
    rail_only = {"kind": "current_balance", "rail": "psu-a", "channel": None}
    cell = {"kind": "unsigned_write", "plc": "plc-stamping", "asset": "stamping",
            "tag": "FLEET.PLC_STAMPING.STAMPING.CELL_ENABLE"}
    assert integrity.blocked([rail_only], _fleet()) == []
    assert integrity.blocked([unsigned], _fleet()) == [
        {"asset": "press-1", "plc": "plc-stamping", "reason": f"unsigned write on {TAG}"}]
    assert [b["asset"] for b in integrity.blocked([cell], _fleet())] == ["press-1", "press-2"]
    graph = {"root": [{"pod": "press-1"}],
             "edges": [{"src": "press-1", "dst": "psu-a", "r": 0.8, "evidence": ["rail"]}]}
    assert len(fleet.proposals(graph, _fleet(), PLANT, 55)) == 1
    assert fleet.proposals(graph, _fleet(), PLANT, 55, blocked={"press-1"}) == []


def test_signed_derate():
    item = {"asset": "press-1", "plc": "plc-stamping", "tag": TAG, "value": 55.0}
    rows = [_row("execute", "executed", 1.0, plc="plc-stamping", tag=TAG, to=55)]
    assert integrity.signed(item, rows, []) is True
    assert integrity.signed({**item, "value": 30.0}, rows, []) is False
    assert integrity.signed(item, [], []) is False
    finding = {"kind": "unsigned_write", "plc": "plc-stamping", "tag": TAG}
    assert integrity.signed(item, rows, [finding]) is False
