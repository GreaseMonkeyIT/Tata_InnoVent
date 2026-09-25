"""Register maps on the wire: the drivers read and write vendor layouts over real Modbus TCP
and S7comm frames (fake PLCs on localhost), and the tag server enrols a PLC with a map."""
import struct

import pytest

from conftest import free_port
from drivers.codec import encode_bytes, encode_words
from drivers.modbus import ModbusDriver
from drivers.regmap import RegMapError, parse_map
from drivers.s7 import S7Driver

# The Modbus fake accepts external writes only in holding registers 1024..1087 (FLEET.md 4.2),
# so the vendor test map keeps its writable points there.
MODBUS_MAP = {
    "vendor": "Acme", "protocol": "modbus",
    "defaults": {"base": 1, "area": "holding_register"},
    "points": [
        {"slot": "%IW0", "address": 1031, "type": "FLOAT32", "word_order": "little", "scale": 10},
        {"slot": "%IW1", "area": "input_register", "address": 11, "type": "INT32"},
        {"slot": "%IW2", "area": "input_register", "address": 13, "type": "UINT16", "scale": 0.5},
        {"slot": "%IX0.0", "area": "discrete_input", "address": 3, "type": "BOOL"},
        {"slot": "%QX0.1", "area": "coil", "address": 5, "type": "BOOL"},
        {"slot": "%MW10", "address": 1041, "type": "FLOAT32", "direction": "read_write"},
        {"slot": "%MW11", "address": 1051, "type": "INT16", "scale": 2, "direction": "read_write"},
    ],
}


def _hr(plc, addr, words):
    plc.ctx.store["h"].setValues(addr, list(words))


def test_modbus_vendor_map_reads_real_frames(fake_modbus):
    _hr(fake_modbus, 1030, encode_words(42.3, "FLOAT32", "little"))       # register 1031, 1-based
    fake_modbus.ctx.store["i"].setValues(10, encode_words(-7, "INT32"))
    fake_modbus.ctx.store["i"].setValues(12, [3000])
    fake_modbus.ctx.store["d"].setValues(2, [1])
    fake_modbus.ctx.store["c"].setValues(4, [1])
    drv = ModbusDriver("127.0.0.1", port=fake_modbus.port, regmap=parse_map(MODBUS_MAP))
    try:
        img = drv.read_image()
        assert img["iw"][0:3] == [423, -7, 1500]
        assert img["ix"][0] is True and img["qx"][1] is True
        assert img["iw"][3] == 0 and img["qx"][0] is False                   # unmapped slots read 0
        assert drv.last_rtt_ms > 0 and drv.last_clamped == []
    finally:
        drv.close()


def test_modbus_vendor_map_writes_32_bit_and_scaled_values(fake_modbus):
    drv = ModbusDriver("127.0.0.1", port=fake_modbus.port, regmap=parse_map(MODBUS_MAP))
    try:
        drv.write_mw(10, 75)                                                  # FC16, 2 registers
        drv.write_mw(11, 40)                                                  # FC06, 40 / scale 2 = 20
        store = fake_modbus.ctx.store["h"]
        assert store.getValues(1040, 2) == encode_words(75.0, "FLOAT32")
        assert store.getValues(1050, 1) == [20]
        assert fake_modbus.denied_writes == 0
        img = drv.read_image()
        assert img["mw"][10] == 75 and img["mw"][11] == 40                    # read back through the map
        with pytest.raises(ValueError, match="not mapped as writable"):
            drv.write_mw(12, 1)
        with pytest.raises(ValueError):
            drv.write_mw(10, 40000)                                           # still a 16-bit image value
    finally:
        drv.close()


class _Resp:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def isError(self):
        return False


class _StubModbus:
    """Records the requests a driver makes; answers reads with short data on demand."""
    def __init__(self, short=False):
        self.calls, self.short = [], short

    def connect(self):
        return True

    def close(self):
        pass

    def write_coil(self, a, v, slave):
        self.calls.append(("FC05", a, v)); return _Resp()

    def write_register(self, a, v, slave):
        self.calls.append(("FC06", a, v)); return _Resp()

    def write_registers(self, a, v, slave):
        self.calls.append(("FC16", a, list(v))); return _Resp()

    def read_coils(self, a, count, slave):
        return _Resp(bits=[False] * count)

    def read_holding_registers(self, a, count, slave):
        return _Resp(registers=[0] * (count - 1 if self.short else count))


def test_modbus_coil_write_and_short_read(monkeypatch):
    m = parse_map({"vendor": "Acme", "protocol": "modbus", "points": [
        {"slot": "%MW8", "area": "coil", "address": 16, "type": "BOOL", "direction": "read_write"},
        {"slot": "%MW9", "area": "holding_register", "address": 5, "type": "UINT32",
         "word_order": "little", "direction": "write"},
        {"slot": "%IW0", "area": "holding_register", "address": 0, "type": "INT16"}]})
    stub = _StubModbus()
    drv = ModbusDriver("h", regmap=m)
    drv._client = stub
    drv.write_mw(8, 1)
    drv.write_mw(8, 0)
    drv.write_mw(9, 70)
    assert stub.calls == [("FC05", 16, True), ("FC05", 16, False), ("FC16", 5, [70, 0])]
    drv._client = _StubModbus(short=True)
    with pytest.raises(IOError, match="short read"):
        drv.read_image()
    assert drv._client is None                                                # a failure drops the link


def test_driver_refuses_a_map_for_the_other_protocol():
    with pytest.raises(ValueError, match="not modbus"):
        ModbusDriver("h", regmap=parse_map({"vendor": "S", "protocol": "s7comm", "points": [
            {"slot": "%IW0", "area": "db", "db": 1, "offset": 0, "type": "INT16"}]}))


# ---------------------------------------------------------------------- S7 --
@pytest.fixture
def vendor_s7():
    import snap7
    from snap7.type import SrvArea
    srv = snap7.Server(log=False)
    areas = {"db10": bytearray(40), "db20": bytearray(10), "mk": bytearray(16)}
    srv.register_area(SrvArea.DB, 10, areas["db10"])
    srv.register_area(SrvArea.DB, 20, areas["db20"])
    srv.register_area(SrvArea.MK, 0, areas["mk"])
    port = free_port()
    srv.start_to("127.0.0.1", port)
    try:
        yield port, areas
    finally:
        srv.stop()


S7_MAP = {
    "vendor": "Siemens", "protocol": "s7comm", "defaults": {"area": "db", "db": 10},
    "points": [
        {"slot": "%IW0", "offset": 0, "type": "FLOAT32", "scale": 10},
        {"slot": "%IW1", "offset": 4, "type": "INT32"},
        {"slot": "%IW2", "area": "marker", "offset": 2, "type": "INT16"},
        {"slot": "%IX0.3", "area": "marker", "offset": 0, "bit": 3, "type": "BOOL"},
        {"slot": "%MW8", "db": 20, "offset": 0, "bit": 1, "type": "BOOL", "direction": "read_write"},
        {"slot": "%MW10", "db": 20, "offset": 2, "type": "FLOAT32", "direction": "read_write"},
    ],
}


def test_s7_vendor_map_reads_dbs_and_markers(vendor_s7):
    port, a = vendor_s7
    a["db10"][0:4] = encode_bytes(398.7, "FLOAT32")
    a["db10"][4:8] = encode_bytes(-100000 // 10, "INT32")
    a["mk"][2:4] = encode_bytes(-5, "INT16")
    a["mk"][0] = 0b00001000
    drv = S7Driver("127.0.0.1", port=port, regmap=parse_map(S7_MAP))
    try:
        img = drv.read_image()
        assert img["iw"][0:3] == [3987, -10000, -5]
        assert img["ix"][3] is True and img["ix"][0] is False
        assert drv.last_rtt_ms > 0
    finally:
        drv.close()


def test_s7_vendor_map_writes_float_and_one_bit(vendor_s7):
    port, a = vendor_s7
    a["db20"][0] = 0b10000100                                                 # neighbours of bit 1
    drv = S7Driver("127.0.0.1", port=port, regmap=parse_map(S7_MAP))
    try:
        drv.write_mw(10, 60)
        assert struct.unpack(">f", bytes(a["db20"][2:6]))[0] == 60.0
        drv.write_mw(8, 1)
        assert a["db20"][0] == 0b10000110                                     # only bit 1 changed
        drv.write_mw(8, 0)
        assert a["db20"][0] == 0b10000100
        img = drv.read_image()
        assert img["mw"][10] == 60 and img["mw"][8] == 0
    finally:
        drv.close()


def test_scaled_value_that_overflows_is_clamped_and_reported(vendor_s7):
    port, a = vendor_s7
    a["db10"][0:4] = encode_bytes(5000.0, "FLOAT32")                          # x10 = 50000 > 32767
    drv = S7Driver("127.0.0.1", port=port, regmap=parse_map(S7_MAP))
    try:
        assert drv.read_image()["iw"][0] == 32767
        assert drv.last_clamped == ["%IW0"]
    finally:
        drv.close()


# ------------------------------------------------------- tag server e2e --
from test_fleet_http import _all_good, _call, _dev, _enroll_body, _wait, server  # noqa: E402,F401


def test_enroll_with_a_vendor_map_end_to_end(server, fake_modbus, tmp_path, monkeypatch):
    monkeypatch.setenv("REGMAP_DIR", str(tmp_path))
    (tmp_path / "acme-press.yaml").write_text(
        "vendor: Acme\nprotocol: modbus\npoints:\n"
        "  - {slot: '%IW0', area: holding_register, address: 1060, type: FLOAT32, scale: 10}\n"
        "  - {slot: '%MW10', area: holding_register, address: 1070, type: FLOAT32, direction: read_write}\n")
    _hr(fake_modbus, 1060, encode_words(42.3, "FLOAT32"))
    name = "plc-acme"
    body = _enroll_body(name, "modbus", fake_modbus.port, machines=["press-1"])
    body["protocol"]["map"] = "acme-press"
    code, out, _ = _call(server + "/enroll", "POST", body, _dev(name))
    assert code == 200 and out["enrolled"] is True
    plc = _wait(lambda: _all_good(server, name))
    tags = {t["tag"]: t["value"] for t in plc["tags"]}
    assert tags["FLEET.PLC_ACME.PRESS_1.AMPS"] == 42.3                       # same tag name, vendor layout


def test_enroll_with_a_bad_map_is_refused(server, tmp_path, monkeypatch):
    monkeypatch.setenv("REGMAP_DIR", str(tmp_path))
    (tmp_path / "broken.yaml").write_text("vendor: Acme\nprotocol: modbus\npoints:\n"
                                          "  - {slot: '%IW0', area: holding_register, type: INT16}\n")
    for mapname, needle in (("broken", "points[0].address"), ("missing", "not found")):
        body = _enroll_body("plc-bad", "modbus", 1502)
        body["protocol"]["map"] = mapname
        code, out, _ = _call(server + "/enroll", "POST", body, _dev("plc-bad"))
        assert code == 400 and out["enrolled"] is False and needle in out["error"]