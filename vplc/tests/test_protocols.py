"""Real protocol frames against the runtime image: the Modbus field and SCADA maps (FLEET.md 4.1, 4.2)
and the S7comm DB1 layout (4.3). Tests bind to free high ports on 127.0.0.1."""
import struct
import time

import pytest
import snap7
from pymodbus.client import ModbusTcpClient

from conftest import free_port, make_runtime, program
from modbus_server import FieldContext, ModbusServers, ScadaContext
from s7_server import S7Db1Server

SRC = program("    a AT %IW0 : INT;\n    ready AT %IX0.0 : BOOL;\n    run AT %QX0.0 : BOOL;\n    spd AT %QW0 : INT;\n"
              "    der AT %MW10 : INT := 100;",
              "run := ready;\nIF run THEN spd := LIMIT(0, der, 100); ELSE spd := 0; END_IF;")


@pytest.fixture
def modbus_rig():
    rt = make_runtime(SRC)
    rt.run()
    fport, sport = free_port(), free_port()
    servers = ModbusServers([(FieldContext(rt), "127.0.0.1", fport), (ScadaContext(rt), "127.0.0.1", sport)])
    servers.start()
    field = ModbusTcpClient("127.0.0.1", port=fport, timeout=3)
    scada = ModbusTcpClient("127.0.0.1", port=sport, timeout=3)
    assert field.connect() and scada.connect()
    yield rt, field, scada
    field.close()
    scada.close()
    servers.stop()


def test_field_port_writes_inputs_and_reads_outputs(modbus_rig):
    rt, field, _ = modbus_rig
    assert not field.write_registers(0, [421], slave=1).isError()      # %IW0
    assert not field.write_coils(0, [True], slave=1).isError()         # %IX0.0
    rt.cycle()
    assert rt.image.iw[0] == 421
    hr = field.read_holding_registers(100, count=1, slave=1)           # %QW0
    co = field.read_coils(100, count=1, slave=1)                       # %QX0.0
    assert hr.registers == [100] and co.bits[0] is True


def test_scada_port_reads_the_image_and_writes_only_mw(modbus_rig):
    rt, field, scada = modbus_rig
    field.write_coils(0, [True], slave=1)
    rt.cycle()
    assert scada.read_coils(0, count=1, slave=1).bits[0] is True                # %QX
    assert scada.read_discrete_inputs(0, count=1, slave=1).bits[0] is True      # %IX
    assert scada.read_holding_registers(0, count=1, slave=1).registers == [100] # %QW
    mw = scada.read_holding_registers(1024, count=5, slave=1).registers         # system words
    assert mw[2] == 1
    assert not scada.write_register(1024 + 10, 60, slave=1).isError()           # %MW10 setpoint
    rt.cycle()
    assert rt.image.qw[0] == 60
    before = rt.denied_writes
    scada.write_register(0, 5, slave=1)                                         # %QW is read-only
    scada.write_coil(0, False, slave=1)
    rt.cycle()
    assert rt.denied_writes >= before + 2
    assert rt.image.qw[0] == 60


def _connect_s7(port):
    client = snap7.client.Client()
    deadline = time.time() + 5
    while True:
        try:
            client.connect("127.0.0.1", 0, 1, tcp_port=port)
            return client
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.1)


def test_s7_db1_layout_and_mw_write():
    rt = make_runtime(SRC, profile="siemens-s7-1200")
    rt.run()
    port = free_port()
    srv = S7Db1Server(rt, "127.0.0.1", port, "plc-test")
    srv.start()
    try:
        rt.field_write("IX", 0, [True])
        rt.field_write("IW", 0, [-5])
        rt.cycle()
        client = _connect_s7(port)
        db = client.db_read(1, 0, 400)
        assert db[0] & 0x01                                    # %IX0.0
        assert db[8] & 0x01                                    # %QX0.0
        assert struct.unpack(">h", bytes(db[16:18]))[0] == -5  # %IW0
        assert struct.unpack(">h", bytes(db[144:146]))[0] == 100  # %QW0
        assert struct.unpack(">h", bytes(db[272 + 4:272 + 6]))[0] == 1  # %MW2 = RUN
        client.db_write(1, 272 + 2 * 10, bytearray(struct.pack(">h", 42)))   # %MW10
        rt.cycle()
        assert rt.image.qw[0] == 42
        client.disconnect()
    finally:
        srv.stop()
