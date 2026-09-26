"""The loop drift forecast (api/drift.py, LOG-100). Pure."""
import drift


def _feed(d, rate_c_min, seconds, temps, start=28.0, step=5.0, limits=None, tripped=()):
    cards = []
    for i in range(int(seconds / step) + 1):
        t = i * step
        sup = start + rate_c_min * t / 60.0
        machines = {m: {"temp": v + rate_c_min * t / 60.0, "limit": (limits or {}).get(m, 55.0),
                        "tripped": m in tripped} for m, v in temps.items()}
        cards = d.step(1000.0 + t, sup, machines)
    return cards


def test_a_steady_supply_gives_no_card():
    assert _feed(drift.Drift(), 0.0, 400, {"furnace-1": 42.0}) == []


def test_a_steady_rise_gives_a_card_from_the_rate():
    cards = _feed(drift.Drift(), 1.0, 400, {"furnace-1": 42.0, "press-1": 57.0}, limits={"press-1": 80.0})
    fur = next(c for c in cards if c["pod"] == "furnace-1")
    assert fur["model"] == "drift" and abs(fur["rate_c_min"] - 1.0) < 0.05
    # after 400 s at 1 C/min the furnace is at 48.7 C: (55 - 48.7) / (1/60) = about 380 s
    assert 330 <= fur["eta_s"] <= 430 and fur["limit"] == 55.0
    assert [c["pod"] for c in cards] == ["furnace-1", "press-1"]   # soonest first


def test_no_card_past_the_horizon_or_for_a_tripped_machine():
    cards = _feed(drift.Drift(), 0.2, 400, {"press-1": 57.0}, limits={"press-1": 80.0})
    assert cards == []                                   # (80 - 58.3) / 0.2 per min = 108 min, past 25 min
    assert _feed(drift.Drift(), 1.0, 400, {"furnace-1": 42.0}, tripped=("furnace-1",)) == []


def test_machines_from_tags():
    rows = [{"asset": "cool-1", "signal": "SUPPLY_TEMP", "value": 31.5, "quality": "GOOD"},
            {"asset": "furnace-1", "signal": "TEMP", "value": 46.0, "quality": "GOOD"},
            {"asset": "furnace-1", "signal": "TRIP_LIMIT", "value": 55.0, "quality": "GOOD"},
            {"asset": "furnace-1", "signal": "TRIP", "value": 0.0, "quality": "GOOD"},
            {"asset": "press-1", "signal": "TEMP", "value": 60.0, "quality": "BAD"}]
    supply, machines = drift.machines_from_tags(rows)
    assert supply == 31.5 and machines == {"furnace-1": {"temp": 46.0, "limit": 55.0, "tripped": False}}
