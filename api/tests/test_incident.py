"""The incident record (api/incident.py, LOG-092). Pure: fed one snapshot per step."""
from incident import Tracker, driver_facts, drive_normal, reason_line

NORMAL = {"press-1": 43.0, "press-2": 39.0, "compressor-1": 6.6}


def _plant(p1=43.0, p1_trip=False, flow=120.0, chiller_trip=False, comp=6.6):
    return {"devices": {"press-1": {"amps": p1, "temp": 60.0, "tripped": p1_trip, "rail": "psu-a"},
                        "press-2": {"amps": 39.0, "temp": 57.0, "tripped": False, "rail": "psu-a"},
                        "compressor-1": {"amps": comp, "tripped": False, "rail": "psu-b"},
                        "chiller-1": {"amps": 0.2 if chiller_trip else 22.0, "tripped": chiller_trip,
                                      "trip_reason": "overload" if chiller_trip else None, "rail": "psu-b"}},
            "rails": {"psu-a": {"volts": 344.0 if p1 > 60 else 360.0}, "psu-b": {"volts": 363.0}},
            "loop": {"name": "cool-1", "flow": flow, "flow_nominal": 120.0, "pump_health": 1.0},
            "supply": {"name": "incomer-1", "volts": 400.0, "nominal_volts": 400.0, "dipped": False}}


def _g(*roots, cards=()):
    return {"root": [{"pod": r} for r in roots], "incipient": list(cards)}


def _run(tr, t, g, plant, integ=(), blind=False, ledger=()):
    return tr.step(t, g, plant, list(integ), blind, list(ledger), NORMAL)


def test_root_with_a_normal_drive_opens_nothing():
    """forge 2026-09-25: a normal compressor cycle raised a compressor-1 root at 6.6 A."""
    tr = Tracker()
    for t in range(0, 60, 5):
        assert _run(tr, t, _g("compressor-1"), _plant()) is None
    assert tr.view()["active"] is None


def test_origin_driver_hold_trip_and_close():
    tr = Tracker(hold_s=15, calm_s=30)
    hot = _plant(p1=85.0)
    _run(tr, 0, _g("press-1"), hot)
    inc = _run(tr, 5, _g("press-1"), hot)
    assert inc["origin"]["asset"] == "press-1" and "85 A" in inc["origin"]["reason"]
    # a flicker to press-2 (normal drive) never becomes the driver
    for t in (10, 15, 20, 25, 30):
        inc = _run(tr, t, _g("press-2", "press-1"), hot)
    assert inc["driver"]["asset"] == "press-1"
    # press-1 trips: a consequence, never the driver; the incident stays open while it is tripped
    tripped = _plant(p1=0.2, p1_trip=True)
    for t in range(35, 200, 5):
        inc = _run(tr, t, _g(), tripped)
    assert inc is not None and inc["status"] == "active" and inc["tripped"] == ["press-1"]
    assert [p["kind"] for p in inc["phases"]][:3] == ["open", "origin", "trip"]
    # the reset: back to normal, the engine tail still names press-1, then calm for 30 s closes it
    inc = _run(tr, 200, _g("press-1"), _plant())
    assert inc["status"] == "recovering"
    for t in range(205, 235, 5):
        _run(tr, t, _g("press-1"), _plant())
    assert tr.view()["active"] is None and tr.view()["recent"][0]["origin"]["asset"] == "press-1"


def test_driver_shift_keeps_the_origin_and_gives_a_reason():
    """Scenario 2: compressor-1 starts it, chiller-1 drives it after its relay trips."""
    tr = Tracker(hold_s=15)
    stuck = _plant(comp=57.0)
    _run(tr, 0, _g("compressor-1"), stuck)
    _run(tr, 5, _g("compressor-1"), stuck)
    cooled = _plant(comp=57.0, flow=72.0, chiller_trip=True)
    for t in range(10, 40, 5):
        inc = _run(tr, t, _g("chiller-1", "compressor-1"), cooled)
    assert inc["origin"]["asset"] == "compressor-1" and inc["driver"]["asset"] == "chiller-1"
    shift = [p for p in inc["phases"] if p["kind"] == "driver"][0]
    assert "relay tripped" in shift["text"] and "72 of 120 L/min" in shift["text"]
    assert inc["chain"] == ["compressor-1", "chiller-1"]


def test_integrity_card_blind_and_ledger_phases():
    tr = Tracker(calm_s=30)
    f = {"kind": "unsigned_write", "tag": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "clients": ["rogue-ews"]}
    inc = _run(tr, 0, _g(), _plant(), integ=[f])
    assert inc["phases"][-1]["kind"] == "integrity" and "rogue-ews" in inc["phases"][-1]["text"]
    card = {"pod": "tag-server", "class": "leak", "eta_s": 120.0}
    _run(tr, 5, _g(cards=[card]), _plant(), integ=[f])
    _run(tr, 10, _g(), _plant(), integ=[f], blind=True)
    rows = [{"ts": 1e12, "verb": "execute", "actor": "operator", "target": "press-1",
             "evidence": {"to": 55, "plc": "plc-stamping"}},
            {"ts": 1e12 + 1, "verb": "relief", "actor": "visr", "target": "press-1",
             "evidence": {"amps_before": 85.3, "amps_after": 45.0, "volts_before": 344.4, "volts_after": 359.6,
                          "rail": "psu-a"}}]
    inc = _run(tr, 15, _g(), _plant(), integ=[f], blind=True, ledger=rows)
    kinds = [p["kind"] for p in inc["phases"]]
    assert kinds == ["open", "integrity", "card", "blind", "action", "relief"]
    assert "85.3 → 45.0 A" in inc["phases"][-1]["text"]
    for t in range(20, 60, 5):
        _run(tr, t, _g(), _plant())
    assert tr.view()["active"] is None


def test_facts_and_normal_checks():
    f = driver_facts("chiller-1", _plant(flow=54.0))
    assert f["kind"] == "cooling" and not drive_normal(f) and "54 of 120" in reason_line(f)
    assert drive_normal(driver_facts("chiller-1", _plant()))
    assert not drive_normal(driver_facts("press-1", _plant(p1=0.2, p1_trip=True), NORMAL))
    assert drive_normal(driver_facts("press-1", _plant(), NORMAL))
    assert not drive_normal(driver_facts("press-1", _plant(p1=85.0), NORMAL))


def test_a_pod_root_is_a_workload_with_its_own_words():
    """LOG-098: a restart made the engine root openplc by pod pressure, and the advice said 'stop it at the machine'."""
    f = driver_facts("openplc", _plant(), NORMAL)
    assert f["kind"] == "workload" and "edge node" in reason_line(f)


def test_first_root_is_the_origin_at_once():
    """LOG-099: the watch showed one pass with an incident but no origin, and the narrator had nothing
    to say. The first driver needs no hold."""
    tr = Tracker(hold_s=15)
    inc = _run(tr, 0, _g("press-1"), _plant(p1=85.0))
    assert inc["origin"]["asset"] == "press-1" and inc["driver"]["asset"] == "press-1"
    assert [p["kind"] for p in inc["phases"]] == ["open", "origin"]


def test_a_leak_card_just_after_scada_comes_back_is_stale():
    """LOG-099, Scenario 6: after the OOM kill the engine kept the tag-server leak card for 15 to 20 s."""
    tr = Tracker(calm_s=30)
    card = {"pod": "tag-server", "class": "leak", "eta_s": 18.0}
    _run(tr, 0, _g(cards=[card]), _plant())
    _run(tr, 5, _g(cards=[card]), _plant(), blind=True)
    inc = _run(tr, 10, _g(cards=[card]), _plant())            # the restarted tag server answers
    assert inc["cards"] == [] and inc["blind"] is False
    inc = _run(tr, 75, _g(cards=[card]), _plant())            # a card a minute later is a new climb
    assert [c["pod"] for c in inc["cards"]] == ["tag-server"]


def test_reason_line_in_the_past_tense():
    f = {"asset": "press-1", "kind": "machine", "amps": 85.0, "normal_amps": 43.0, "rail": "psu-a", "rail_volts": 344.0}
    assert reason_line(f) == "press-1 draws 85 A, 98 % above its normal 43 A, and rail psu-a is at 344 V"
    assert reason_line(f, past=True) == "press-1 drew 85 A, 98 % above its normal 43 A, and rail psu-a was at 344 V"
    c = {"asset": "chiller-1", "kind": "cooling", "flow": 54.0, "flow_nominal": 120.0, "pump_health": 0.45}
    assert reason_line(c, past=True) == "chiller-1 was short of cooling, and loop flow was 54 of 120 L/min (pump health 45 %)"


def test_a_restart_seen_only_as_a_new_start_time_drops_the_stale_leak_card():
    """LOG-099, B6 watch of Scenario 6: the kill and the restart fell between two passes, so no blind
    phase came, and the old leak card stayed 26 s. The tag server's start time changed."""
    tr = Tracker(calm_s=30)
    card = {"pod": "tag-server", "class": "leak", "eta_s": 20.0}
    tr.step(0, _g(cards=[card]), _plant(), [], False, [], NORMAL, scada_started=100.0)
    inc = tr.step(5, _g(cards=[card]), _plant(), [], False, [], NORMAL, scada_started=100.0)
    assert [c["pod"] for c in inc["cards"]] == ["tag-server"]
    inc = tr.step(10, _g(cards=[card]), _plant(), [], False, [], NORMAL, scada_started=206.0)
    assert inc["cards"] == [] and inc["phases"][-1]["kind"] == "restart"
    assert inc["phases"][-1]["text"] == "tag-server restarted, and SCADA answers again"
    inc = tr.step(15, _g(cards=[card]), _plant(), [], False, [], NORMAL, scada_started=206.0)
    assert [p["kind"] for p in inc["phases"]].count("restart") == 1


def test_chiller_reason_lines_for_capacity_undervoltage_and_flow():
    """LOG-100: Scenario 2 leaves the chiller running at its capacity limit, Scenario 7 stops it on
    undervoltage, Scenario 5 starves it of flow."""
    base = {"asset": "chiller-1", "kind": "cooling", "flow": 120.0, "flow_nominal": 120.0, "t_setpoint": 28.0,
            "running": True}
    cap = dict(base, t_supply=36.4, at_capacity=True)
    assert reason_line(cap) == "chiller-1 is at its capacity limit, and the supply water is 36 C against its 28 C setpoint"
    assert drive_normal(cap) is False and drive_normal(dict(base, t_supply=28.3)) is True
    uv = dict(base, t_supply=31.0, running=False, undervoltage=True)
    assert reason_line(uv).startswith("chiller-1 stopped on undervoltage")
    flow = dict(base, t_supply=28.1, flow=54.0, pump_health=0.45)
    assert reason_line(flow) == "chiller-1 is short of cooling, and loop flow is 54 of 120 L/min (pump health 45 %)"
