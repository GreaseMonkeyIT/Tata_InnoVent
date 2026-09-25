"""drivers/regmap.py: YAML maps, validation, the default layout, and the vendor examples."""
import os
import struct

import pytest

import drivers
import fleet
from conftest import sample_image
from drivers import make_driver
from drivers.codec import encode_bytes, encode_words
from drivers.modbus import MW_BASE, ModbusDriver
from drivers.regmap import (RegMapError, default_map, load_map, map_path, parse_map, parse_slot,
                            regmap_dir, vendor_value)
from drivers.s7 import S7Driver, build_db, parse_db

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIEMENS = os.path.join(HERE, "regmaps", "siemens-s7-1200.yaml")
SCHNEIDER = os.path.join(HERE, "regmaps", "schneider-m221.yaml")


def _modbus_regs(image):
    """What a PLC with the old FLEET.md 4.2 map returns, keyed like the plan's spans."""
    return {
        "discrete_input": [1 if b else 0 for b in image["ix"]],
        "coil": [1 if b else 0 for b in image["qx"]],
        "input_register": [v & 0xFFFF for v in image["iw"]],
        ("holding_register", 0): [v & 0xFFFF for v in image["qw"]],
        ("holding_register", MW_BASE): [v & 0xFFFF for v in image["mw"]],
    }


# ------------------------------------------------------------ default map --
def test_default_modbus_map_is_the_old_fixed_layout():
    m = default_map("modbus")
    plan = [(s.area, s.start, s.count) for s in m.spans]
    assert plan == [("discrete_input", 0, 64), ("coil", 0, 64), ("input_register", 0, 64),
                    ("holding_register", 0, 64), ("holding_register", 1024, 64)]  # the same 5 requests
    regs = _modbus_regs(sample_image())
    data = {0: regs["discrete_input"], 1: regs["coil"], 2: regs["input_register"],
            3: regs[("holding_register", 0)], 4: regs[("holding_register", MW_BASE)]}
    image, clamped = m.build_image(data)
    assert image == sample_image() and clamped == []
    assert m.write_point(21).start == MW_BASE + 21 and m.write_point(0).type == "INT16"
    assert len(m.mapped_slots) == 5 * 64


def test_default_s7_map_is_the_old_fixed_layout():
    m = default_map("s7comm", db=7)
    assert [(s.area, s.db, s.start, s.count) for s in m.spans] == [("db", 7, 0, 400)]  # one DB read
    img = sample_image()
    img["ix"][13] = True
    img["qx"][63] = True
    img["qw"][63] = -32768
    image, _ = m.build_image({0: bytes(build_db(img))})
    assert image == img == parse_db(build_db(img))
    assert m.write_point(10).start == 272 + 20


def test_drivers_without_a_map_use_the_default():
    assert ModbusDriver("h").map.spans[4].start == MW_BASE
    s7 = make_driver({"kind": "s7comm", "host": "h", "port": 102, "rack": 0, "slot": 1, "db": 3})
    assert s7.map.spans[0].db == 3 and s7.map.spans[0].count == 400


# ------------------------------------------------------------ vendor files --
def test_schneider_example_decodes_a_sample_buffer():
    m = load_map(SCHNEIDER)
    assert m.protocol == "modbus" and m.vendor == "Schneider Electric"
    plan = {(s.area, s.start): s.count for s in m.spans}
    assert plan[("holding_register", 100)] == 8            # register 101 in a 1-based manual
    data = {}
    for i, s in enumerate(m.spans):
        if s.area == "holding_register" and s.start == 100:
            # 42.30 A (0.01 A), 51.3 degC (0.1), 398.7 V (0.1, UINT16), 55.0 % ; machine 1 the same
            data[i] = [4230, 513, 3987, 550, 1420, 0, 40000, 1000]
        elif s.area == "holding_register" and s.start == 200:
            data[i] = encode_words(12.6, "FLOAT32", "little")    # 12.6 kW, CDAB
        elif s.area == "holding_register":
            data[i] = [100, 75]
        elif s.area == "discrete_input":
            data[i] = [1, 0]
        elif s.start == 0:
            data[i] = [1, 1]
        else:
            data[i] = [1]
    image, clamped = m.build_image(data)
    assert image["iw"][0:8] == [423, 513, 3987, 550, 142, 0, 32767, 1000]
    assert clamped == ["%IW6"]                               # 40000 does not fit an INT16 slot
    assert image["mw"][20] == 13                             # 12.6 kW x scale 1, rounded
    assert image["ix"][0:2] == [True, False] and image["qx"][0:2] == [True, True]
    assert image["mw"][8] == 1 and image["mw"][10:12] == [100, 75]
    # the fleet tag table then gives engineering values, unchanged
    table = fleet.tag_table("plc-m221", ["press-1", "press-2"], "cell")
    tags = fleet.decode(table, image, 0.0)
    assert tags["FLEET.PLC_M221.PRESS_1.AMPS"]["value"] == 42.3
    assert tags["FLEET.PLC_M221.PRESS_1.VOLTS"]["value"] == 398.7


def test_siemens_example_decodes_a_sample_buffer():
    m = load_map(SIEMENS)
    assert m.protocol == "s7comm" and m.vendor == "Siemens"
    db10 = bytearray(36)
    for off, v in ((0, 42.3), (4, 71.3), (8, 398.7), (12, 100.0), (16, 14.2), (20, 51.3),
                   (24, 398.2), (28, 55.0)):
        db10[off:off + 4] = encode_bytes(v, "FLOAT32")
    db10[32:34] = encode_bytes(100, "INT16")
    db10[34:36] = encode_bytes(55, "INT16")
    by_key = {("db", 10, 0): bytes(db10), ("db", 20, 0): b"\x01",
              ("db", 20, 2): encode_bytes(80.0, "FLOAT32") + encode_bytes(100.0, "FLOAT32"),
              ("input", 0, 0): b"\x03", ("output", 0, 0): b"\x01"}
    data = {i: by_key[(s.area, s.db, s.start)] for i, s in enumerate(m.spans)}
    image, clamped = m.build_image(data)
    assert clamped == []
    assert image["iw"][0:8] == [423, 713, 3987, 1000, 142, 513, 3982, 550]
    assert image["qw"][0:2] == [100, 55]
    assert image["ix"][0:2] == [True, True] and image["qx"][0:2] == [True, False]
    assert image["mw"][8] == 1 and image["mw"][10:12] == [80, 100]
    p = m.write_point(10)
    assert (p.db, p.start, p.type) == (20, 2, "FLOAT32") and vendor_value(p, 60) == 60


# ------------------------------------------------------------ validation --
GOOD = {"vendor": "Acme", "protocol": "modbus",
        "points": [{"slot": "%IW0", "area": "holding_register", "address": 1, "type": "INT16"}]}


def _err(doc) -> str:
    with pytest.raises(RegMapError) as e:
        parse_map(doc, "bad.yaml")
    return str(e.value)


def test_a_minimal_map_parses():
    m = parse_map(GOOD)
    assert m.points[0].start == 1 and m.points[0].direction == "read"


def test_bad_maps_name_every_bad_field():
    msg = _err({"vendor": "", "protocol": "profinet", "points": [], "colour": "red"})
    assert "bad.yaml" in msg and "vendor:" in msg and "protocol:" in msg
    assert "points: required" in msg and "colour: unknown key" in msg
    msg = _err({"vendor": "Acme", "protocol": "modbus", "points": [
        {"slot": "%IW99", "area": "holding_register", "address": 1, "type": "INT16"},
        {"slot": "%IW1", "area": "holding_register", "address": 1, "type": "FLOAT64"},
        {"slot": "%IW2", "area": "coil", "address": 1, "type": "INT16"},
        {"slot": "%IW3", "area": "holding_register", "adress": 1, "type": "INT16"},
        {"slot": "%IW4", "area": "holding_register", "address": 0, "base": 1, "type": "INT16"},
        {"slot": "%IW5", "area": "holding_register", "address": 5, "type": "INT32", "word_order": "BADC"},
        {"slot": "%IW6", "area": "holding_register", "address": 6, "type": "INT16", "scale": 0},
        {"slot": "%IW7", "area": "input_register", "address": 7, "type": "INT16", "direction": "read_write"},
        {"slot": "%MW9", "area": "input_register", "address": 9, "type": "INT16", "direction": "write"},
        {"slot": "%IW8", "area": "holding_register", "address": 65535, "type": "FLOAT32"},
        {"slot": "%IW0", "area": "holding_register", "address": 1, "type": "INT16", "db": 3},
    ]})
    for needle in ("points[0].slot", "points[1].type", "points[2].type", "points[3].adress: unknown key",
                   "points[3].address: required", "points[4].address", "points[5].word_order",
                   "points[6].scale", "points[7].direction: only %MW", "points[8].direction: area input_register",
                   "points[9].address: FLOAT32 at 65535", "points[10].db: is an S7 field"):
        assert needle in msg, needle
    assert "problem(s)" in msg


def test_s7_validation_and_duplicate_slots():
    msg = _err({"vendor": "Siemens", "protocol": "s7comm", "points": [
        {"slot": "%IW0", "area": "db", "offset": 0, "type": "INT16"},
        {"slot": "%IX0.0", "area": "input", "offset": 0, "type": "BOOL"},
        {"slot": "%IW1", "area": "db", "db": 1, "address": 4, "type": "INT16"},
        {"slot": "%IW2", "area": "marker", "db": 1, "offset": 4, "type": "INT16"},
        {"slot": "%IW3", "area": "db", "db": 1, "offset": 6, "type": "INT16", "bit": 2},
        {"slot": "%IW4", "area": "db", "db": 1, "offset": 8, "type": "INT16"},
        {"slot": "%IW4", "area": "db", "db": 1, "offset": 10, "type": "INT16"},
    ]})
    for needle in ("points[0].db", "points[1].bit", "points[2].address: is a modbus field",
                   "points[2].offset", "points[3].db: only area db", "points[4].bit: only a BOOL",
                   "points[6].slot: %IW4 is already mapped by points[5]"):
        assert needle in msg, needle


def test_bad_files_and_names(tmp_path):
    p = tmp_path / "broken.yaml"
    p.write_text("vendor: [unclosed\n")
    with pytest.raises(RegMapError, match="not valid YAML"):
        load_map(str(p))
    with pytest.raises(RegMapError, match="not found"):
        load_map(str(tmp_path / "missing.yaml"))
    p.write_text("- just\n- a list\n")
    with pytest.raises(RegMapError, match="must be a mapping"):
        load_map(str(p))
    for bad in ("../etc/passwd", "Schneider", "", "a/b", ".hidden"):
        with pytest.raises(RegMapError):
            map_path(bad)


def test_make_driver_loads_the_named_map(tmp_path, monkeypatch):
    monkeypatch.setenv("REGMAP_DIR", str(tmp_path))
    assert regmap_dir() == str(tmp_path)
    (tmp_path / "acme.yaml").write_text(
        "vendor: Acme\nprotocol: modbus\npoints:\n"
        "  - {slot: '%IW0', area: holding_register, address: 40, type: FLOAT32, scale: 10}\n")
    drv = make_driver({"kind": "modbus", "host": "h", "port": 502, "map": "acme"})
    assert drv.map.vendor == "Acme" and drv.map.spans[0].start == 40
    with pytest.raises(RegMapError, match="cannot drive"):
        make_driver({"kind": "s7comm", "host": "h", "port": 102, "map": "acme"})
    with pytest.raises(RegMapError, match="not found"):
        make_driver({"kind": "modbus", "host": "h", "port": 502, "map": "nope"})


def test_enrollment_accepts_and_checks_protocol_map():
    body = {"name": "plc-a", "profile": "p", "protocol": {"kind": "modbus", "host": "h", "map": "schneider-m221"}}
    assert fleet.parse_enrollment(body)["protocol"]["map"] == "schneider-m221"
    assert "map" not in fleet.parse_enrollment({**body, "protocol": {"kind": "modbus", "host": "h"}})["protocol"]
    with pytest.raises(fleet.EnrollError, match="protocol.map"):
        fleet.parse_enrollment({**body, "protocol": {"kind": "modbus", "host": "h", "map": "../x"}})


def test_read_planning_limits_and_gaps():
    pts = [{"slot": f"%IW{i}", "area": "holding_register", "address": a, "type": "INT16"}
           for i, a in enumerate((0, 1, 3, 200))]
    m = parse_map({**GOOD, "points": pts})
    assert [(s.start, s.count) for s in m.spans] == [(0, 2), (3, 1), (200, 1)]   # max_gap 0
    m = parse_map({**GOOD, "read": {"max_gap": 1}, "points": pts})
    assert [(s.start, s.count) for s in m.spans] == [(0, 4), (200, 1)]
    many = [{"slot": f"%IW{i}", "area": "holding_register", "address": 2 * i, "type": "FLOAT32"}
            for i in range(64)]                                     # 128 contiguous registers
    m = parse_map({**GOOD, "points": many})
    assert [s.count for s in m.spans] == [124, 4]                   # split at the 125 limit
    assert all(s.count <= 125 for s in m.spans)


def test_parse_slot():
    assert parse_slot("%ix1.5") == ("ix", 13) and parse_slot("%MW63") == ("mw", 63)
    for bad in ("%IW64", "%IX8.0", "%XW1", 5, None):
        with pytest.raises(ValueError):
            parse_slot(bad)