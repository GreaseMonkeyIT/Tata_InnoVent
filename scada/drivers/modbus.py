"""Modbus TCP driver. Where each value lives comes from a register map (drivers/regmap.py).

Without a map the driver uses default_map("modbus"), the FLEET.md 4.2 layout of `generic-iec`:

  discrete inputs (FC02)   0..63       %IX     read
  coils (FC01)             0..63       %QX     read
  input registers (FC04)   0..63       %IW     read
  holding registers (FC03) 0..63       %QW     read
  holding registers        1024..1087  %MW0..63  read (FC03), write (FC06)

That is five requests per poll, the same frames as before register maps existed. A vendor
map (regmaps/*.yaml) can put any slot at any address, in INT16, UINT16, INT32, UINT32,
FLOAT32 or BOOL, either word order, with a scale. The read plan groups mapped addresses into
as few requests as Modbus allows (125 registers or 2000 bits each).

last_rtt_ms is the wall time of the read requests of one poll. The connect time is not part of it.
"""
from __future__ import annotations

import threading
import time

from . import check_mw
from .codec import encode_words
from .regmap import RegMap, default_map, vendor_value

try:
    from pymodbus.client import ModbusTcpClient          # pymodbus 3.x, the same pin as plant-sim
except Exception:                                        # pragma: no cover
    ModbusTcpClient = None

MW_BASE = 1024                                           # the default map's %MW base (FLEET.md 4.2)

_READ = {"discrete_input": ("read_discrete_inputs", "FC02", "bits"),
         "coil": ("read_coils", "FC01", "bits"),
         "input_register": ("read_input_registers", "FC04", "registers"),
         "holding_register": ("read_holding_registers", "FC03", "registers")}


class ModbusDriver:
    """A pymodbus client that reads the image and writes %MW words through a register map."""

    kind = "modbus"

    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 2.0,
                 retries: int = 1, regmap: RegMap | None = None):
        self.host = host
        self.port = int(port)
        self.unit = int(unit)
        self.timeout = timeout
        self.retries = retries
        self.map = regmap or default_map("modbus")
        if self.map.protocol != "modbus":
            raise ValueError(f"{self.map.source} is a {self.map.protocol} map, not modbus")
        self.last_rtt_ms: float | None = None
        self.last_clamped: list[str] = []                # slots clamped to INT16 on the last read
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
                data = {}
                t0 = time.perf_counter()
                for i, sp in enumerate(self.map.spans):
                    fn, fc, attr = _READ[sp.area]
                    what = f"{fc} {sp.start}..{sp.start + sp.count - 1}"
                    resp = self._ok(getattr(c, fn)(sp.start, count=sp.count, slave=self.unit), what)
                    values = list(getattr(resp, attr))
                    if len(values) < sp.count:
                        raise IOError(f"modbus short read ({what})")
                    data[i] = values[:sp.count]
                rtt = (time.perf_counter() - t0) * 1000.0
            except Exception:
                self._drop()
                raise
        image, clamped = self.map.build_image(data)
        self.last_rtt_ms = rtt
        self.last_clamped = clamped
        return image

    def write_mw(self, index: int, value: int) -> None:
        index, value = check_mw(index, value)
        p = self.map.write_point(index)
        v = vendor_value(p, value)
        if p.area == "coil":
            op = lambda c: self._ok(c.write_coil(p.start, bool(v), slave=self.unit), f"FC05 {p.start}")
        else:
            words = encode_words(v, p.type, p.word_order)    # ValueError before the wire
            if len(words) == 1:
                op = lambda c: self._ok(c.write_register(p.start, words[0], slave=self.unit),
                                        f"FC06 {p.start}")
            else:
                op = lambda c: self._ok(c.write_registers(p.start, words, slave=self.unit),
                                        f"FC16 {p.start}..{p.start + len(words) - 1}")
        with self._lock:
            try:
                op(self._conn())
            except Exception:
                self._drop()
                raise