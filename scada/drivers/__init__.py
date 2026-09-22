"""Protocol drivers for the virtual PLC fleet (FLEET.md 8).

Every driver has the same interface:

  read_image() -> dict     read the full memory image (FLEET.md 3) in one poll
  write_mw(index, value)   write one %MW word as a signed 16-bit INT
  close()                  drop the connection. The next call connects again.
  last_rtt_ms              wall time of the last read_image exchange in ms (measured)

The image is a dict of five lists, 64 entries each:
  ix, qx  -> bool   (index = byte*8 + bit)
  iw, qw, mw -> int (signed 16-bit)

A driver serializes its own protocol exchanges with a lock, so the poll thread and an
HTTP write can share one driver. A failed exchange drops the connection and raises.
"""
from __future__ import annotations

N_BITS = 64
N_WORDS = 64
AREAS = ("ix", "qx", "iw", "qw", "mw")
INT16_MIN = -32768
INT16_MAX = 32767


def to_int16(u: int) -> int:
    """A 16-bit register value (0..65535) as a signed INT (-32768..32767)."""
    u = int(u) & 0xFFFF
    return u - 0x10000 if u >= 0x8000 else u


def check_mw(index, value) -> tuple[int, int]:
    """Validate a %MW write before it goes on the wire. Raise ValueError when bad."""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < N_WORDS:
        raise ValueError(f"%MW index {index!r} is outside 0..{N_WORDS - 1}")
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, int) or not INT16_MIN <= value <= INT16_MAX:
        raise ValueError(f"value {value!r} is not a 16-bit INT")
    return index, value


def make_driver(protocol: dict):
    """Return the driver for an enrolled protocol block {kind, host, port, ...}."""
    kind = protocol.get("kind")
    if kind == "modbus":
        from .modbus import ModbusDriver
        return ModbusDriver(protocol["host"], port=protocol["port"],
                            unit=protocol.get("unit") or 1)
    if kind == "s7comm":
        from .s7 import S7Driver
        return S7Driver(protocol["host"], port=protocol["port"], rack=protocol.get("rack") or 0,
                        slot=protocol.get("slot") if protocol.get("slot") is not None else 1,
                        db=protocol.get("db") or 1)
    raise ValueError(f"unknown protocol kind {kind!r}")
