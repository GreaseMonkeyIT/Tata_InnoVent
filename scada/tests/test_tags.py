"""2F.2 fixtures: the SCADA tag layer (scada/tags.py) — decode/scale, derived tags, quality
aging, and the engine-parity contract that makes the aggregator repoint a pure URL swap.
Plain pytest, no PLC: `python -m pytest tests` from scada/.
"""
import tags
from tags import COOLED, MACHINES, decode, engine_parity_metrics, prom_text, requality, tag_table


def _regs():
    """A plausible MW0..31 block per plc/REGISTER_MAP.md (values x10 / x100)."""
    r = [0] * 32
    r[0:4] = [581, 559, 506, 653]                 # temps: 58.1, 55.9, 50.6, 65.3 C
    r[4], r[5] = 1182, 100                        # flow 118.2 L/min, pump 1.00
    r[6], r[7] = 3610, 3728                       # rails 361.0 / 372.8 V
    r[8:16] = [421, 378, 287, 72, 182, 67, 304, 220]        # amps x10
    r[24:32] = [992, 987, 844, 858, 1000, 1000, 1000, 1000]  # throughput x10
    return r


def test_decode_scales_measured_tags():
    t = decode(_regs(), [False, False, False, False], ts=100.0)
    assert t["PLANT.PRESS_1.TEMP"]["value"] == 58.1
    assert t["PLANT.COOL_1.FLOW"]["value"] == 118.2
    assert t["PLANT.COOL_1.PUMP_HEALTH"]["value"] == 1.0
    assert t["PLANT.PSU_A.VOLTS"]["value"] == 361.0
    assert t["PLANT.PRESS_1.AMPS"]["value"] == 42.1
    assert t["PLANT.CHILLER_1.THROUGHPUT"]["value"] == 100.0
    assert all(r["quality"] == "GOOD" for r in t.values())


def test_derived_tags_follow_topology():
    t = decode(_regs(), None, ts=100.0)
    # a machine sees ITS rail's voltage (the per-victim sag signal)
    assert t["PLANT.CNC_1.VOLTS"]["value"] == t["PLANT.PSU_A.VOLTS"]["value"]
    assert t["PLANT.FURNACE_1.VOLTS"]["value"] == t["PLANT.PSU_B.VOLTS"]["value"]
    # heat = k * I with the per-machine calibration (press 0.55, furnace 1.0)
    assert abs(t["PLANT.PRESS_1.HEAT"]["value"] - 0.55 * 42.1) < 1e-9
    assert abs(t["PLANT.FURNACE_1.HEAT"]["value"] - 1.0 * 30.4) < 1e-9
    assert t["PLANT.COOL_1.TRIP_LIMIT"]["value"] == 78.0


def test_trip_coils_map_to_cooled_machines():
    t = decode(_regs(), [False, True, False, True], ts=1.0)
    assert t["PLANT.PRESS_1.TRIP"]["value"] == 0.0
    assert t["PLANT.PRESS_2.TRIP"]["value"] == 1.0
    assert t["PLANT.FURNACE_1.TRIP"]["value"] == 1.0
    assert COOLED == ["press-1", "press-2", "cnc-1", "furnace-1"]


def test_quality_ages_good_stale_bad():
    t = decode(_regs(), [False] * 4, ts=100.0)
    assert all(r["quality"] == "GOOD" for r in requality(t, now=105.0).values())
    stale = requality(t, now=115.0)                  # 15s > stale_s=10
    assert all(r["quality"] == "STALE" for r in stale.values())
    bad = requality(t, now=200.0)                    # 100s > bad_s=30
    assert all(r["quality"] == "BAD" for r in bad.values())


def test_prom_text_exports_engine_parity_series_and_drops_bad():
    t = decode(_regs(), [False] * 4, ts=100.0)
    text = prom_text(t)
    present = {line.split("{")[0] for line in text.strip().splitlines()}
    # every metric the aggregator queries for the engine must be served — the cutover contract
    assert engine_parity_metrics() <= present
    # labels identical to the sim's exposition (namespace/pod name plant ASSETS)
    assert 'plant_bus_voltage_volts{namespace="plant",pod="psu-a"}' in text
    assert 'plant_temp_celsius{namespace="plant",pod="press-1"}' in text
    assert 'plant_current_draw_amps{namespace="plant",pod="compressor-1"}' in text
    # BAD tags are never exported — a gap, not a lie
    assert prom_text(requality(t, now=500.0)).strip() == ""


def test_tag_table_is_complete_and_addressed():
    tab = tag_table()
    by_tag = {r["tag"]: r for r in tab}
    # 4 temps + flow + pump + 2 rails + 8 amps + 8 throughput + 4 trips = 28 measured
    assert sum(1 for r in tab if r["kind"] == "measured") == 28
    # 8 derived volts + 4 derived heat + trip limit = 13 derived
    assert sum(1 for r in tab if r["kind"] == "derived") == 13
    assert by_tag["PLANT.PRESS_1.TEMP"]["address"] == "%MW0"
    assert by_tag["PLANT.PSU_B.VOLTS"]["address"] == "%MW7"
    assert by_tag["PLANT.CHILLER_1.THROUGHPUT"]["address"] == "%MW31"
    assert by_tag["PLANT.FURNACE_1.TRIP"]["address"] == "%QX0.3"
    # decode emits a value for every tag in the table (nothing defined but dead)
    t = decode(_regs(), [False] * 4, ts=1.0)
    assert set(t) == set(by_tag)


def test_machine_order_is_the_register_contract():
    # order MUST match plant/sim/main.py DEVICES — pinned so a reorder breaks loudly
    assert [m[0] for m in MACHINES] == ["press-1", "press-2", "cnc-1", "qa-scanner-1",
                                        "conveyor-1", "compressor-1", "furnace-1", "chiller-1"]
