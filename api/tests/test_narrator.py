"""The narrator (api/narrator.py) and the suggestions (api/advice.py), LOG-093. Pure."""
import json

import advice
import narrator

S1 = {"id": "INC-1", "tag": 3, "status": "active",
      "origin": {"asset": "press-1", "reason": "press-1 draws 85 A, 99 % above its normal 43 A, and rail psu-a is at 344 V",
                 "facts": {"kind": "machine", "rail": "psu-a", "amps": 85.0}, "evidence": ["write", "rail", "temporal"]},
      "driver": {"asset": "press-1", "reason": "same", "facts": {"kind": "machine"}, "evidence": ["write", "rail", "temporal"]},
      "chain": ["press-1"], "victims": ["press-2", "cnc-1"], "tripped": [], "integrity": [], "blind": False,
      "cards": [{"pod": "press-1", "class": "trip", "eta_s": 167.0, "value": 64.0, "limit": 78.0, "t_inf": 82.3}]}
PROP = {"id": "p1", "verb": "derate", "asset": "press-1", "plc": "plc-stamping", "to": 55, "reason": "root",
        "cites": {}}


def test_case_file_sections_in_order_with_plain_words():
    cf = narrator.case_file(S1, advice.suggest(S1, [PROP], [], []))
    assert list(cf) == ["headline", "origin", "chain", "evidence", "forecast", "suggestion"]   # no driver: same asset
    assert cf["chain"] == "It reaches press-2, cnc-1."
    text = narrator.render(cf)
    assert text.startswith("press-1 started an incident on rail psu-a.")
    assert "heads for 82 °C and trips at 78 °C in about 167 s" in text
    assert "Suggested: derate press-1 to 55 % through plc-stamping" in text
    for jargon in ("contention", "blast", "ETA", "evidence types"):
        assert jargon not in text


def test_writer_keeps_good_prose_and_rejects_invented_numbers():
    cf = narrator.case_file(S1, advice.suggest(S1, [PROP], [], []))
    good = {k: v.replace("press-1", "Press-1") for k, v in cf.items()}
    sections, source = narrator.write(cf, None, lambda p: json.dumps(good))
    assert source == "llm" and sections["headline"].startswith("Press-1")
    loose = dict(good, headline="Press-1 started an incident on rail psu-a")   # LOG-099: no final period
    sections, source = narrator.write(cf, None, lambda p: json.dumps(loose))
    assert source == "llm" and sections["headline"] == "Press-1 started an incident on rail psu-a."
    bad = dict(good, forecast="press-1 trips in 40 s.")               # 40 is not in the case file
    sections, source = narrator.write(cf, None, lambda p: json.dumps(bad))
    assert source == "mixed" and sections["forecast"] == cf["forecast"]
    assert narrator.write(cf, None, lambda p: "not json")[1] == "template"
    assert narrator.write(cf, None, lambda p: None)[0] == cf


def test_clean_strips_markup():
    """forge 2026-09-26: the model wrote '$t=830.0$s' and backticks into the console."""
    assert narrator.clean("root at $t=830.0$s from `compressor-1` **now**") == "root at t=830.0s from compressor-1 now"


def test_prompt_carries_style_example_previous_and_case():
    cf = narrator.case_file(S1, [])
    p = narrator.prompt(cf, {"headline": "old"})
    assert "Plain words" in p and "EXAMPLE ANSWER" in p and '"old"' in p and "press-1 started" in p


def test_suggestions_cover_every_scenario_shape():
    # Scenario 5: derates for the controllable presses first, then advice for the pump and the furnace
    s5 = {"origin": {"asset": "chiller-1", "facts": {"kind": "cooling", "flow": 54.0, "flow_nominal": 120.0,
                                                    "chiller_tripped": False}},
          "driver": {"asset": "chiller-1", "facts": {"kind": "cooling"}}, "tripped": ["furnace-1"],
          "cards": [{"pod": "press-2", "class": "trip", "eta_s": 90.0, "limit": 78.0},
                    {"pod": "furnace-1", "class": "trip", "eta_s": 0.0, "limit": 78.0}], "integrity": []}
    got = advice.suggest(s5, [dict(PROP, asset="press-2", reason="forecast", cites={"eta_s": 90.0})], [], [])
    assert got[0]["executable"] and got[0]["verb"] == "derate" and got[0]["target"] == "press-2"
    verbs = {(s["verb"], s["target"]) for s in got}
    assert ("inspect", "chiller-1") in verbs and ("inspect", "furnace-1") in verbs
    assert not any(s["verb"] == "stop" and s["target"] == "furnace-1" for s in got)      # tripped: no action
    # Scenario 4A: the signed restore is executable, the rogue client gets isolated
    s4a = {"integrity": [{"kind": "unsigned_write", "tag": "X.PRESS_1.DERATE_PCT", "clients": ["rogue-ews"]}],
           "tripped": [], "cards": []}
    got = advice.suggest(s4a, [], [{"asset": "press-1", "value": 30.0, "signed": False, "plc": "plc-stamping"}],
                         [{"asset": "press-1", "plc": "plc-stamping", "reason": "unsigned write"}])
    assert got[0]["verb"] == "restore" and got[0]["executable"]
    assert ("isolate", "rogue-ews") in {(s["verb"], s["target"]) for s in got}
    # Scenario 3 and Scenario 6
    s3 = {"origin": {"asset": "hmi-gw", "facts": {"kind": "network", "segment": "field-1"}}, "tripped": [], "cards": []}
    assert advice.suggest(s3, [], [], [])[0]["verb"] == "isolate"
    s6 = {"cards": [{"pod": "tag-server", "class": "leak", "eta_s": 120.0}], "tripped": [], "blind": True}
    got = advice.suggest(s6, [], [], [])
    assert got[0]["verb"] == "restart" and not got[0]["executable"] and got[-1]["target"] == "scada"


def test_clean_keeps_tag_names():
    assert narrator.clean("PRESS_1.DERATE_PCT changed") == "PRESS_1.DERATE_PCT changed"


def test_workload_advice_is_to_check_the_pod():
    inc = {"origin": {"asset": "openplc", "facts": {"kind": "workload"}}, "tripped": [], "cards": []}
    s = advice.suggest(inc, [], [], [])
    assert s[0]["verb"] == "inspect" and "pod" in s[0]["text"] and "machine" not in s[0]["text"]


def test_origin_goes_to_the_past_tense_once_its_readings_are_history():
    """LOG-099: after press-1 tripped the text still said "press-1 draws 85 A"."""
    facts = {"asset": "press-1", "kind": "machine", "amps": 85.0, "normal_amps": 43.0, "rail": "psu-a",
             "rail_volts": 344.0}
    inc = dict(S1, origin=dict(S1["origin"], facts=facts), tripped=["press-1"], cards=[])
    cf = narrator.case_file(inc, [])
    assert cf["origin"] == "At the start, press-1 drew 85 A, 98 % above its normal 43 A, and rail psu-a was at 344 V."
    inc = dict(inc, tripped=[], status="recovering")
    cf = narrator.case_file(inc, [])
    assert cf["headline"].endswith("The plant recovers.") and cf["origin"].startswith("At the start, press-1 drew")
    assert cf["chain"] == "It reached press-2, cnc-1."
    assert narrator.case_file(S1, [])["origin"].startswith("press-1 draws 85 A")      # still true: present


def test_every_phase_has_a_headline():
    """LOG-099: Scenario 4A had an empty text after the reset, and Scenarios 2 and 3 for one pass."""
    inc = {"id": "INC-2", "tag": 3, "status": "active", "origin": None, "driver": None, "tripped": [],
           "cards": [], "integrity": [], "blind": False,
           "phases": [{"n": 1, "kind": "open", "text": "an integrity check failed"},
                      {"n": 2, "kind": "integrity", "text": "integrity: ...", "facts": {"kind": "unsigned_write"}},
                      {"n": 3, "kind": "action", "text": "fault-shell restored press-1 to 100 %"}]}
    cf = narrator.case_file(inc, [])
    assert cf["headline"] == "The setpoint change with no signed record is cleared."
    assert cf["actions"] == "Done so far: fault-shell restored press-1 to 100 %."
    assert list(cf) == ["headline", "actions"]
    inc["phases"] = [{"n": 1, "kind": "open"}, {"n": 2, "kind": "card", "facts": {"asset": "tag-server", "cls": "leak"}},
                     {"n": 3, "kind": "blind"}, {"n": 4, "kind": "blind"}]
    assert narrator.case_file(inc, [])["headline"] == "tag-server is back under its memory limit."


def test_no_stop_advice_once_the_plant_recovers():
    """LOG-099: after the Scenario 4B reset the console still said "unload or stop press-1"."""
    inc = {"status": "recovering", "origin": {"asset": "press-1", "facts": {"kind": "machine"}},
           "driver": {"asset": "press-1", "facts": {"kind": "machine"}}, "tripped": [], "cards": [], "integrity": []}
    assert advice.suggest(inc, [], [], []) == []
    assert advice.suggest(dict(inc, status="active"), [], [], [])[0]["verb"] == "stop"


def test_the_driver_line_goes_to_the_past_tense_once_the_plant_recovers():
    """LOG-099, B6 watch of Scenario 2: after the reset the text still said "loop flow is 71 of 120 L/min"."""
    inc = {"id": "INC-3", "tag": 7, "status": "recovering", "tripped": [], "cards": [], "integrity": [],
           "origin": {"asset": "compressor-1", "reason": "compressor-1 draws 57 A", "facts": {"asset": "compressor-1",
                      "kind": "machine", "amps": 57.0}},
           "driver": {"asset": "chiller-1", "reason": "chiller-1's overload relay tripped, and loop flow is 71 of 120 L/min",
                      "was": "chiller-1's overload relay tripped, and loop flow was 71 of 120 L/min"}}
    cf = narrator.case_file(inc, [])
    assert cf["driver"] == "After that, chiller-1 drove it: chiller-1's overload relay tripped, and loop flow was 71 of 120 L/min."
    assert narrator.case_file(dict(inc, status="active"), [])["driver"].startswith("chiller-1 drives it now:")


def test_a_restart_after_a_leak_card_has_its_own_headline():
    inc = {"id": "INC-4", "tag": 3, "status": "active", "origin": None, "driver": None, "tripped": [], "cards": [],
           "integrity": [], "blind": False,
           "phases": [{"n": 1, "kind": "open"}, {"n": 2, "kind": "card", "facts": {"asset": "tag-server", "cls": "leak"}},
                      {"n": 3, "kind": "restart", "facts": {"asset": "tag-server"}}]}
    assert narrator.case_file(inc, [])["headline"] == "tag-server reached its memory limit and restarted. SCADA answers again."


def test_capacity_advice_points_at_the_heat_source_and_drift_cards_read_as_a_rate():
    inc = {"status": "active", "tripped": [], "cards": [], "integrity": [],
           "origin": {"asset": "compressor-1", "facts": {"kind": "machine"}},
           "driver": {"asset": "chiller-1", "facts": {"kind": "cooling", "t_supply": 36.0, "t_setpoint": 28.0,
                                                      "running": True, "flow": 120.0, "flow_nominal": 120.0}}}
    texts = [s["text"] for s in advice.suggest(inc, [], [], [])]
    assert any(t.startswith("remove the extra heat at compressor-1 first") for t in texts)
    card = {"pod": "furnace-1", "class": "trip", "eta_s": 540.0, "limit": 55.0, "model": "drift", "rate_c_min": 0.8}
    cf = narrator.case_file(dict(S1, cards=[card]), [])
    assert "furnace-1 trips at 55 °C in about 540 s at the present rise of 0.80 °C per minute" in cf["forecast"]
