"""S7comm driver for the SCADA port of `siemens-s7-1200` (FLEET.md 4.3).

One poll is one DB read of 400 bytes (python-snap7 3.x, a pure Python ISO-on-TCP client):

  bytes 0..7      %IX     bit b of byte a = %IXa.b
  bytes 8..15     %QX     bit b of byte 8+a = %QXa.b
  bytes 16..143   %IW0..63  INT big-endian, word n at byte 16 + 2n
  bytes 144..271  %QW0..63  INT big-endian, word n at byte 144 + 2n
  bytes 272..399  %MW0..63  INT big-endian, word n at byte 272 + 2n

A %MW write is a 2-byte DB write at byte 272 + 2n. last_rtt_ms is the wall time of the
DB read exchange. The connect time is not part of it.
"""
from __future__ import annotations

import struct
import threading
import time

from . import N_BITS, N_WORDS, check_mw

DB_SIZE = 400
IX_AT, QX_AT, IW_AT, QW_AT, MW_AT = 0, 8, 16, 144, 272


def parse_db(data) -> dict:
    """400 DB1 bytes -> the image dict. Raise ValueError on a short buffer."""
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


class S7Driver:
    """A python-snap7 client that reads DB1 and writes %MW words."""

    kind = "s7comm"

    def __init__(self, host: str, port: int = 102, rack: int = 0, slot: int = 1, db: int = 1):
        self.host = host
        self.port = int(port)
        self.rack = int(rack)
        self.slot = int(slot)
        self.db = int(db)
        self.last_rtt_ms: float | None = None
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

    # -- interface ---------------------------------------------------------------
    def read_image(self) -> dict:
        with self._lock:
            try:
                c = self._conn()
                t0 = time.perf_counter()
                data = c.db_read(self.db, 0, DB_SIZE)
                rtt = (time.perf_counter() - t0) * 1000.0
                image = parse_db(data)
            except Exception:
                self._drop()
                raise
        self.last_rtt_ms = rtt
        return image

    def write_mw(self, index: int, value: int) -> None:
        index, value = check_mw(index, value)
        with self._lock:
            try:
                c = self._conn()
                c.db_write(self.db, MW_AT + 2 * index, bytearray(struct.pack(">h", value)))
            except Exception:
                self._drop()
                raise
