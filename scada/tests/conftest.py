"""Fake PLCs for the fleet tests. Both speak the real protocol on a free localhost port.

fake_modbus: a pymodbus server with the FLEET.md 4.2 map. A write outside %MW
  (holding 1024..1087) changes nothing and counts in denied_writes.
fake_s7: a python-snap7 server with DB1, 400 bytes, in the FLEET.md 4.3 layout. scan()
  rewrites every byte outside %MW from the image, like a runtime scan.

Both fakes only store and serve an image. They run no task and compute no physics.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import threading

import pytest

from drivers import N_BITS, N_WORDS, to_int16
from drivers.s7 import MW_AT, build_db

logging.getLogger("snap7").setLevel(logging.CRITICAL)
logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def sample_image() -> dict:
    """A plausible image for a 3-machine cell with engineering values x10."""
    img = {"ix": [False] * N_BITS, "qx": [False] * N_BITS, "iw": [0] * N_WORDS,
           "qw": [0] * N_WORDS, "mw": [0] * N_WORDS}
    # machine 0: 9.1 A, not cooled, 398.7 V, 100.0 pct
    img["iw"][0:4] = [91, 0, 3987, 1000]
    # machine 1: 14.2 A, 51.3 degC, 398.2 V, 55.0 pct
    img["iw"][4:8] = [142, 513, 3982, 550]
    # machine 2: 4.0 A, not cooled, -1.5 V (a signed INT check), 0 pct
    img["iw"][8:12] = [40, 0, -15, 0]
    img["ix"][0:3] = [True, True, False]                # READY 1, 1, 0 (machine 2 tripped)
    img["qx"][0:3] = [True, True, False]                # RUN
    img["qw"][0:3] = [100, 55, 0]                       # SPEED_PCT
    img["mw"][0:5] = [21, 1234, 1, 3, 20561]            # 0.21 ms, scans, RUN, overruns, crc
    img["mw"][8] = 1                                    # CELL_ENABLE
    img["mw"][10:13] = [100, 100, 100]                  # DERATE_PCT defaults
    img["mw"][20] = 417                                 # io_extra PACK_COUNT
    return img


# ------------------------------------------------------------------ Modbus --
class FakeModbusPLC:
    def __init__(self):
        from pymodbus.datastore import (ModbusSequentialDataBlock, ModbusServerContext,
                                        ModbusSlaveContext)

        fake = self

        class MapContext(ModbusSlaveContext):
            def setValues(self, fc_as_hex, address, values):
                in_mw = fc_as_hex in (6, 16) and 1024 <= address and address + len(values) <= 1088
                if not in_mw:
                    fake.denied_writes += 1
                    return
                super().setValues(fc_as_hex, address, values)

        self.denied_writes = 0
        self.ctx = MapContext(di=ModbusSequentialDataBlock(0, [0] * N_BITS),
                              co=ModbusSequentialDataBlock(0, [0] * N_BITS),
                              ir=ModbusSequentialDataBlock(0, [0] * N_WORDS),
                              hr=ModbusSequentialDataBlock(0, [0] * 1088), zero_mode=True)
        self.server_ctx = ModbusServerContext(slaves=self.ctx, single=True)
        self.port = free_port()
        self._loop = asyncio.new_event_loop()
        self._server = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        from pymodbus.server import ModbusTcpServer

        async def serve():
            self._server = ModbusTcpServer(self.server_ctx, address=("127.0.0.1", self.port))
            task = asyncio.ensure_future(self._server.serve_forever())
            while not self._server.transport and not task.done():
                await asyncio.sleep(0.01)                # serve_forever opens the listen socket
            self._ready.set()
            await task

        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(serve())
        except Exception:
            pass

    def start(self):
        self._thread.start()
        assert self._ready.wait(5), "fake modbus server did not start"
        return self

    def stop(self):
        if self._server is not None:
            fut = asyncio.run_coroutine_threadsafe(self._server.shutdown(), self._loop)
            try:
                fut.result(5)
            except Exception:
                pass
        self._thread.join(5)

    def load(self, image: dict):
        """Put an image in the datastore (the runtime side, not a Modbus write)."""
        store = self.ctx.store
        store["d"].setValues(0, [1 if b else 0 for b in image["ix"]])
        store["c"].setValues(0, [1 if b else 0 for b in image["qx"]])
        store["i"].setValues(0, [v & 0xFFFF for v in image["iw"]])
        store["h"].setValues(0, [v & 0xFFFF for v in image["qw"]])
        store["h"].setValues(1024, [v & 0xFFFF for v in image["mw"]])

    def mw(self, index: int) -> int:
        return to_int16(self.ctx.store["h"].getValues(1024 + index, 1)[0])


@pytest.fixture
def fake_modbus():
    plc = FakeModbusPLC().start()
    plc.load(sample_image())
    try:
        yield plc
    finally:
        plc.stop()


# ---------------------------------------------------------------------- S7 --
class FakeS7PLC:
    def __init__(self):
        import snap7
        from snap7.type import SrvArea

        self.image = sample_image()
        self.db = build_db(self.image)
        self.server = snap7.Server(log=False)
        self.server.register_area(SrvArea.DB, 1, self.db)
        self.port = free_port()

    def start(self):
        self.server.start_to("127.0.0.1", self.port)    # binds and listens before it returns
        return self

    def stop(self):
        self.server.stop()

    def scan(self):
        """Rewrite bytes 0..271 from the image. %MW bytes keep external writes."""
        fresh = build_db(self.image)
        self.db[0:MW_AT] = fresh[0:MW_AT]

    def mw(self, index: int) -> int:
        return struct.unpack_from(">h", self.db, MW_AT + 2 * index)[0]


@pytest.fixture
def fake_s7():
    plc = FakeS7PLC().start()
    try:
        yield plc
    finally:
        plc.stop()
