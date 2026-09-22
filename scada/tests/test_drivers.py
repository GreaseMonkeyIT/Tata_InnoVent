"""FLEET.md 4.2 and 4.3 fixtures: the protocol drivers against fake PLCs on localhost.

The Modbus fake is a pymodbus server with the 4.2 map. The S7 fake is a python-snap7
server with DB1 in the 4.3 layout. Every exchange is a real protocol frame.
"""
import struct

import pytest

import drivers
from conftest import sample_image
from drivers import make_driver, to_int16
from drivers.modbus import ModbusDriver
from drivers.s7 import S7Driver, build_db, parse_db


def test_int16_conversion():
    assert to_int16(0) == 0
    assert to_int16(32767) == 32767
    assert to_int16(32768) == -32768
    assert to_int16(65535) == -1


def test_make_driver_picks_the_protocol():
    assert isinstance(make_driver({"kind": "modbus", "host": "h", "port": 502}), ModbusDriver)
    s7 = make_driver({"kind": "s7comm", "host": "h", "port": 102, "rack": 0, "slot": 1, "db": 1})
    assert isinstance(s7, S7Driver) and (s7.rack, s7.slot, s7.db) == (0, 1, 1)
    with pytest.raises(ValueError):
        make_driver({"kind": "profinet", "host": "h", "port": 1})


def test_check_mw_rejects_bad_writes_before_the_wire():
    for index, value in ((64, 1), (-1, 1), (10, 40000), (10, -40000), (10, 1.5), ("10", 1)):
        with pytest.raises(ValueError):
            drivers.check_mw(index, value)


def test_s7_db_layout_round_trip():
    img = sample_image()
    img["ix"][13] = True                                    # %IX1.5
    img["qx"][63] = True                                    # %QX7.7
    img["qw"][63] = -32768
    db = build_db(img)
    assert len(db) == 400
    assert db[1] == 0b00100000                              # bit 5 of byte 1
    assert db[8 + 7] == 0b10000000                          # %QX7.7 in byte 15
    assert struct.unpack_from(">h", db, 16 + 2 * 2)[0] == 3987      # %IW2 at byte 20
    assert struct.unpack_from(">h", db, 144 + 2 * 63)[0] == -32768  # %QW63 at byte 270
    assert struct.unpack_from(">h", db, 272 + 2 * 20)[0] == 417     # %MW20 at byte 312
    assert parse_db(db) == img
    with pytest.raises(ValueError):
        parse_db(db[:399])


# ------------------------------------------------------------------ Modbus --
def test_modbus_driver_reads_the_4_2_map(fake_modbus):
    drv = ModbusDriver("127.0.0.1", port=fake_modbus.port)
    try:
        img = drv.read_image()
        assert img == sample_image()
        assert drv.last_rtt_ms is not None and drv.last_rtt_ms > 0
    finally:
        drv.close()


def test_modbus_driver_writes_mw_and_reads_it_back(fake_modbus):
    drv = ModbusDriver("127.0.0.1", port=fake_modbus.port)
    try:
        drv.write_mw(11, 55)
        drv.write_mw(21, -123)                              # negative INT survives the wire
        assert fake_modbus.mw(11) == 55
        assert fake_modbus.mw(21) == -123
        img = drv.read_image()
        assert img["mw"][11] == 55 and img["mw"][21] == -123
        assert img["qw"] == sample_image()["qw"]            # %QW did not move
        assert fake_modbus.denied_writes == 0
    finally:
        drv.close()


def test_modbus_fake_denies_writes_outside_mw(fake_modbus):
    # the fake itself follows 4.2: a write to %QW changes nothing and counts
    from pymodbus.client import ModbusTcpClient
    c = ModbusTcpClient("127.0.0.1", port=fake_modbus.port, timeout=2)
    assert c.connect()
    try:
        c.write_register(2, 99, slave=1)
    finally:
        c.close()
    assert fake_modbus.denied_writes == 1
    drv = ModbusDriver("127.0.0.1", port=fake_modbus.port)
    try:
        assert drv.read_image()["qw"][2] == 0
    finally:
        drv.close()


def test_modbus_driver_raises_when_the_plc_is_gone():
    from conftest import free_port
    drv = ModbusDriver("127.0.0.1", port=free_port(), timeout=0.5, retries=0)
    with pytest.raises(Exception):
        drv.read_image()
    with pytest.raises(Exception):
        drv.write_mw(10, 55)
    drv.close()


# ---------------------------------------------------------------------- S7 --
def test_s7_driver_reads_db1(fake_s7):
    drv = S7Driver("127.0.0.1", port=fake_s7.port, rack=0, slot=1, db=1)
    try:
        assert drv.read_image() == sample_image()
        assert drv.last_rtt_ms is not None and drv.last_rtt_ms > 0
    finally:
        drv.close()


def test_s7_driver_writes_mw_and_a_scan_keeps_it(fake_s7):
    drv = S7Driver("127.0.0.1", port=fake_s7.port)
    try:
        drv.write_mw(10, 55)
        drv.write_mw(63, -2)
        assert fake_s7.mw(10) == 55 and fake_s7.mw(63) == -2
        fake_s7.image["iw"][0] = 95                         # the runtime scans with a new input
        fake_s7.scan()
        img = drv.read_image()
        assert img["mw"][10] == 55 and img["mw"][63] == -2  # external %MW writes survive the scan
        assert img["iw"][0] == 95
    finally:
        drv.close()


def test_s7_driver_reconnects_after_a_failure(fake_s7):
    drv = S7Driver("127.0.0.1", port=fake_s7.port)
    try:
        drv.read_image()
        drv._client.disconnect()                            # the link drops under the driver
        with pytest.raises(Exception):
            drv.read_image()
        assert drv.read_image() == sample_image()           # the next poll connects again
    finally:
        drv.close()
