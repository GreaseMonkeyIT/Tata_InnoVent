"""S7comm driver. Where each value lives comes from a register map (drivers/regmap.py).

Without a map the driver uses default_map("s7comm", db), the FLEET.md 4.3 layout of
`siemens-s7-1200`: one DB read of 400 bytes (python-snap7 3.x, a pure Python ISO-on-TCP client):

  bytes 0..7      %IX     bit b of byte a = %IXa.b
  bytes 8..15     %QX     bit b of byte 8+a = %QXa.b
  bytes 16..143   %IW0..63  INT big-endian, word n at byte 16 + 2n
  bytes 144..271  %QW0..63  INT big-endian, word n at byte 144 + 2n
  bytes 272..399  %MW0..63  INT big-endian, word n at byte 272 + 2n

A vendor map (regmaps/*.yaml) can put any slot in any DB, or in the input (I), output (Q) or
marker (M) area, as BOOL, INT16, UINT16, INT32, UINT32 or FLOAT32. Each contiguous run is one
read. A %MW write is a DB or area write of the value's bytes; a BOOL write reads its byte,
sets the bit and writes the byte back.

Real S7-1200/1500: the PLC must allow PUT/GET, and each DB read must have optimized block
access turned off, or the byte offsets do not exist. last_rtt_ms is the wall time of the
read exchanges of one poll. The connect time is not part of it.
"""
from __future__ import annotations

import struct
import threading
import time

from . import N_BITS, N_WORDS, check_mw
from .codec import encode_bytes
from .regmap import RegMap, default_map, vendor_value

DB_SIZE = 400
IX_AT, QX_AT, IW_AT, QW_AT, MW_AT = 0, 8, 16, 144, 272


def parse_db(data) -> dict:
    """400 DB1 bytes -> the image dict (default layout). Raise ValueError on a short buffer."""
    if len(data) < DB_SIZE:
        raise ValueError(f"S7 DB read returned {len(data)} bytes, expected {DB_SIZE}")
    buf = bytes(data[:DB_SIZE])

    def bits(at):
        return [bool(buf[at + i // 8] >> (i % 8) & 1) for i in range(N_BITS)]

    def words(at):
        return list(struct.unpack_from(f">{N_WORDS}h", buf, at))

    return {"ix": bits(IX_AT), "qx": bits(QX_AT), "iw": words(IW_AT), "qw": words(QW_AT),
            "mw": words(MW_AT)}


def build_db(image: dict) -> bytearray:
    """The image dict -> 400 DB1 bytes. The inverse of parse_db (used by test fakes)."""
    buf = bytearray(DB_SIZE)
    for at, area in ((IX_AT, "ix"), (QX_AT, "qx")):
        for i, on in enumerate(image[area][:N_BITS]):
            if on:
                buf[at + i // 8] |= 1 << (i % 8)
    for at, area in ((IW_AT, "iw"), (QW_AT, "qw"), (MW_AT, "mw")):
        struct.pack_into(f">{N_WORDS}h", buf, at, *image[area][:N_WORDS])
    return buf


def _snap7_area(area: str):
    from snap7.type import Area
    return {"input": Area.PE, "output": Area.PA, "marker": Area.MK, "db": Area.DB}[area]


class S7Driver:
    """A python-snap7 client that reads the image and writes %MW words through a register map."""

    kind = "s7comm"

    def __init__(self, host: str, port: int = 102, rack: int = 0, slot: int = 1, db: int = 1,
                 regmap: RegMap | None = None):
        self.host = host
        self.port = int(port)
        self.rack = int(rack)
        self.slot = int(slot)
        self.db = int(db)
        self.map = regmap or default_map("s7comm", self.db)
        if self.map.protocol != "s7comm":
            raise ValueError(f"{self.map.source} is a {self.map.protocol} map, not s7comm")
        self.last_rtt_ms: float | None = None
        self.last_clamped: list[str] = []
        self._client = None
        self._lock = threading.Lock()

    # -- connection --------------------------------------------------------------
    def _conn(self):
        if self._client is None:
            import snap7                                 # imported here: only S7 PLCs need it
            client = snap7.Client()
            client.connect(self.host, self.rack, self.slot, self.port)
            self._client = client
        return self._client

    def _drop(self):
        try:
            if self._client is not None:
                self._client.disconnect()
        except Exception:
            pass
        self._client = None

    def close(self):
        with self._lock:
            self._drop()

    def _read(self, c, area: str, db: int, start: int, size: int) -> bytes:
        if area == "db":
            data = c.db_read(db, start, size)
        else:
            data = c.read_area(_snap7_area(area), 0, start, size)
        if len(data) < size:
            raise IOError(f"S7 {area} read at {start} returned {len(data)} of {size} bytes")
        return bytes(data[:size])

    def _write(self, c, area: str, db: int, start: int, data: bytes) -> None:
        if area == "db":
            c.db_write(db, start, bytearray(data))
        else:
            c.write_area(_snap7_area(area), 0, start, bytearray(data))

    # -- interface ---------------------------------------------------------------
    def read_image(self) -> dict:
        with self._lock:
            try:
                c = self._conn()
                data = {}
                t0 = time.perf_counter()
                for i, sp in enumerate(self.map.spans):
                    data[i] = self._read(c, sp.area, sp.db, sp.start, sp.count)
                rtt = (time.perf_counter() - t0) * 1000.0
                image, clamped = self.map.build_image(data)
            except Exception:
                self._drop()
                raise
        self.last_rtt_ms = rtt
        self.last_clamped = clamped
        return image

    def write_mw(self, index: int, value: int) -> None:
        index, value = check_mw(index, value)
        p = self.map.write_point(index)
        v = vendor_value(p, value)
        payload = None if p.type == "BOOL" else encode_bytes(v, p.type, p.word_order)
        with self._lock:
            try:
                c = self._conn()
                if payload is not None:
                    self._write(c, p.area, p.db, p.start, payload)
                else:                                    # one bit: read-modify-write its byte
                    b = self._read(c, p.area, p.db, p.start, 1)[0]
                    b = (b | (1 << p.bit)) if v else (b & ~(1 << p.bit) & 0xFF)
                    self._write(c, p.area, p.db, p.start, bytes([b]))
            except Exception:
                self._drop()
                raise
            