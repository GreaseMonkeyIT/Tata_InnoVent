"""Modbus TCP servers bound to the runtime image (FLEET.md 4.1 and 4.2). pymodbus 3.6.9.

Field port (plant-sim is the client, any unit id):
  holding registers   0..63  -> %IW0..63   the sim writes (and may read back)
  coils               0..63  -> %IX 0..63  the sim writes (and may read back)
  holding registers 100..163 -> %QW0..63   read only
  coils             100..163 -> %QX 0..63  read only

SCADA port (generic-iec):
  discrete inputs (FC02)       0..63     -> %IX        read
  coils (FC01)                 0..63     -> %QX        read
  input registers (FC04)       0..63     -> %IW        read
  holding registers (FC03)     0..63     -> %QW        read
  holding registers (FC03/06/16) 1024..1087 -> %MW0..63 read, write

A write outside the writable ranges changes nothing, answers Modbus exception 02 (illegal data
address), and adds 1 to the runtime's denied_writes counter.
"""
import asyncio
import threading

from pymodbus.datastore import ModbusBaseSlaveContext, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from image import from_u16, to_u16

MW_BASE = 1024
FIELD_OUT_BASE = 100
WRITE_FCS = (5, 6, 15, 16, 22)          # FC23 checks its write part in setValues


def _within(address, count, base, size=64):
    return count >= 1 and base <= address and address + count <= base + size


class _ImageContext(ModbusBaseSlaveContext):
    """Common helpers. Subclasses define the map."""

    def __init__(self, runtime):
        self.rt = runtime

    def reset(self):
        """The image belongs to the runtime. A Modbus reset changes nothing."""

    def __str__(self):
        return self.__class__.__name__


class FieldContext(_ImageContext):
    """The remote-I/O emulation that plant-sim drives."""

    def validate(self, fc_as_hex, address, count=1):
        kind = self.decode(fc_as_hex)
        if kind not in ("h", "c"):
            return False
        if _within(address, count, 0):
            return True
        if _within(address, count, FIELD_OUT_BASE):
            if fc_as_hex in WRITE_FCS:
                self.rt.count_denied()
                return False
            return True
        if fc_as_hex in WRITE_FCS:
            self.rt.count_denied()
        return False

    def getValues(self, fc_as_hex, address, count=1):
        kind = self.decode(fc_as_hex)
        if kind == "h":
            if address >= FIELD_OUT_BASE:
                return [to_u16(v) for v in self.rt.field_read("QW", address - FIELD_OUT_BASE, count)]
            return [to_u16(v) for v in self.rt.field_read("IW", address, count)]
        if address >= FIELD_OUT_BASE:
            return list(self.rt.field_read("QX", address - FIELD_OUT_BASE, count))
        return list(self.rt.field_read("IX", address, count))

    def setValues(self, fc_as_hex, address, values):
        kind = self.decode(fc_as_hex)
        if not _within(address, len(values), 0):
            self.rt.count_denied()
            return
        if kind == "h":
            self.rt.field_write("IW", address, [from_u16(v) for v in values])
        else:
            self.rt.field_write("IX", address, values)


class ScadaContext(_ImageContext):
    """The SCADA view of the image for the Modbus profiles."""

    def validate(self, fc_as_hex, address, count=1):
        kind = self.decode(fc_as_hex)
        if fc_as_hex in WRITE_FCS:
            if kind == "h" and _within(address, count, MW_BASE):
                return True
            self.rt.count_denied()
            return False
        if kind in ("d", "c", "i"):
            return _within(address, count, 0)
        return _within(address, count, 0) or _within(address, count, MW_BASE)

    def getValues(self, fc_as_hex, address, count=1):
        kind = self.decode(fc_as_hex)
        if kind == "d":
            return list(self.rt.read("IX", address, count))
        if kind == "c":
            return list(self.rt.read("QX", address, count))
        if kind == "i":
            return [to_u16(v) for v in self.rt.read("IW", address, count)]
        if address >= MW_BASE:
            return [to_u16(v) for v in self.rt.read("MW", address - MW_BASE, count)]
        return [to_u16(v) for v in self.rt.read("QW", address, count)]

    def setValues(self, fc_as_hex, address, values):
        if self.decode(fc_as_hex) == "h" and _within(address, len(values), MW_BASE):
            self.rt.write_mw(address - MW_BASE, [from_u16(v) for v in values])
            return
        self.rt.count_denied()


class ModbusServers:
    """Run one or more Modbus TCP servers on a private asyncio loop in a background thread."""

    def __init__(self, bindings):
        """bindings: list of (slave_context, host, port)."""
        self.bindings = bindings
        self._thread = None
        self._loop = None
        self._servers = []
        self._ready = threading.Event()
        self._error = None
        self._stop = None

    def start(self, timeout=10.0):
        self._thread = threading.Thread(target=self._run, name="modbus", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("the Modbus servers did not start in time")
        if self._error:
            raise RuntimeError(self._error)

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            self._error = f"Modbus server loop failed: {e}"
            self._ready.set()

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        for ctx, host, port in self.bindings:
            server = ModbusTcpServer(ModbusServerContext(slaves=ctx, single=True), address=(host, port))
            if not await server.listen():
                self._error = f"cannot listen for Modbus on {host}:{port}"
                for s in self._servers:
                    await s.shutdown()
                self._ready.set()
                return
            self._servers.append(server)
        self._ready.set()
        await self._stop.wait()
        for s in self._servers:
            await s.shutdown()

    def stop(self, timeout=5.0):
        if self._loop is not None and self._stop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
