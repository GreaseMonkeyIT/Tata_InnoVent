"""Modbus TCP driver for the SCADA port of `generic-iec` (FLEET.md 4.2).

  discrete inputs (FC02)   0..63       %IX     read
  coils (FC01)             0..63       %QX     read
  input registers (FC04)   0..63       %IW     read
  holding registers (FC03) 0..63       %QW     read
  holding registers        1024..1087  %MW0..63  read (FC03), write (FC06)

One poll is five requests. last_rtt_ms is the wall time of those five exchanges. The
connect time is not part of it.
"""
from __future__ import annotations

import threading
import time

from . import N_BITS, N_WORDS, check_mw, to_int16

try:
    from pymodbus.client import ModbusTcpClient          # pymodbus 3.x, the same pin as plant-sim
except Exception:                                        # pragma: no cover
    ModbusTcpClient = None

MW_BASE = 1024


class ModbusDriver:
    """A pymodbus client that reads the image and writes %MW words."""

    kind = "modbus"

    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 2.0,
                 retries: int = 1):
        self.host = host
        self.port = int(port)
        self.unit = int(unit)
        self.timeout = timeout
        self.retries = retries
        self.last_rtt_ms: float | None = None
        self._client = None
        self._lock = threading.Lock()

    # -- connection --------------------------------------------------------------
    def _conn(self):
        if self._client is None:
            if ModbusTcpClient is None:
                raise RuntimeError("pymodbus is not installed")
            client = ModbusTcpClient(self.host, port=self.port, timeout=self.timeout,
                                     retries=self.retries)
            if not client.connect():
                client.close()
                raise ConnectionError(f"modbus connect {self.host}:{self.port} failed")
            self._client = client
        return self._client

    def _drop(self):
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        self._client = None

    def close(self):
        with self._lock:
            self._drop()

    @staticmethod
    def _ok(resp, what: str):
        if resp is None or resp.isError():
            raise IOError(f"modbus {what} failed: {resp}")
        return resp

    # -- interface ---------------------------------------------------------------
    def read_image(self) -> dict:
        with self._lock:
            try:
                c = self._conn()
                t0 = time.perf_counter()
                di = self._ok(c.read_discrete_inputs(0, count=N_BITS, slave=self.unit), "FC02 0..63")
                co = self._ok(c.read_coils(0, count=N_BITS, slave=self.unit), "FC01 0..63")
                ir = self._ok(c.read_input_registers(0, count=N_WORDS, slave=self.unit), "FC04 0..63")
                qw = self._ok(c.read_holding_registers(0, count=N_WORDS, slave=self.unit), "FC03 0..63")
                mw = self._ok(c.read_holding_registers(MW_BASE, count=N_WORDS, slave=self.unit),
                              "FC03 1024..1087")
                rtt = (time.perf_counter() - t0) * 1000.0
            except Exception:
                self._drop()
                raise
        bits_ix, bits_qx = list(di.bits), list(co.bits)
        words = [list(ir.registers), list(qw.registers), list(mw.registers)]
        if len(bits_ix) < N_BITS or len(bits_qx) < N_BITS or any(len(w) < N_WORDS for w in words):
            with self._lock:
                self._drop()
            raise IOError("modbus short read")
        self.last_rtt_ms = rtt
        return {
            "ix": [bool(b) for b in bits_ix[:N_BITS]],
            "qx": [bool(b) for b in bits_qx[:N_BITS]],
            "iw": [to_int16(v) for v in words[0][:N_WORDS]],
            "qw": [to_int16(v) for v in words[1][:N_WORDS]],
            "mw": [to_int16(v) for v in words[2][:N_WORDS]],
        }

    def write_mw(self, index: int, value: int) -> None:
        index, value = check_mw(index, value)
        with self._lock:
            try:
                c = self._conn()
                self._ok(c.write_register(MW_BASE + index, value & 0xFFFF, slave=self.unit),
                         f"FC06 {MW_BASE + index}")
            except Exception:
                self._drop()
                raise
