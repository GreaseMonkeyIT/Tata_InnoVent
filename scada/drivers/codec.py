"""Vendor data types <-> numbers, for the register maps (regmaps/README.md).

Every vendor stores numbers in 16-bit words (Modbus registers) or bytes (S7). This module
turns those into Python numbers and back, with struct, for every supported type:

  BOOL     1 bit
  INT16    1 word, signed          UINT16   1 word, unsigned
  INT32    2 words, signed         UINT32   2 words, unsigned
  FLOAT32  2 words, IEEE 754 single precision

word_order only matters for the 2-word types:
  big     high word first (ABCD). Siemens S7 and most Modbus devices.
  little  low word first (CDAB), the "word swapped" order some Modbus devices use.
The bytes inside each word are always big-endian, as both protocols define them.

An encode rejects a value the type cannot hold (ValueError); it never wraps silently.
"""
from __future__ import annotations

import math
import struct

# type -> (struct code, number of 16-bit words, integer?, min, max)
TYPES = {
    "INT16":   ("h", 1, True, -(2 ** 15), 2 ** 15 - 1),
    "UINT16":  ("H", 1, True, 0, 2 ** 16 - 1),
    "INT32":   ("i", 2, True, -(2 ** 31), 2 ** 31 - 1),
    "UINT32":  ("I", 2, True, 0, 2 ** 32 - 1),
    "FLOAT32": ("f", 2, False, None, None),
}
ALL_TYPES = ("BOOL",) + tuple(TYPES)
WORD_ORDERS = ("big", "little")
_WORD_ORDER_ALIASES = {"big": "big", "abcd": "big", "high_first": "big",
                       "little": "little", "cdab": "little", "low_first": "little"}


def norm_word_order(v) -> str | None:
    """'big', 'ABCD', 'little', 'CDAB' ... -> 'big' | 'little'. None when unknown."""
    return _WORD_ORDER_ALIASES.get(str(v).strip().lower()) if v is not None else None


def n_words(dtype: str) -> int:
    """Words a value of this type takes (BOOL counts as 1 so it has a footprint)."""
    return 1 if dtype == "BOOL" else TYPES[dtype][1]


def n_bytes(dtype: str) -> int:
    return 1 if dtype == "BOOL" else 2 * TYPES[dtype][1]


def _swap_words(b: bytes) -> bytes:
    """ABCD <-> CDAB for a 4-byte value. 2-byte values are returned unchanged."""
    return b[2:4] + b[0:2] if len(b) == 4 else b


# ------------------------------------------------------------------ bytes --
def decode_bytes(buf, offset: int, dtype: str, word_order: str = "big", bit: int | None = None):
    """Read one value of `dtype` from a byte buffer at `offset` (S7 layout)."""
    if dtype == "BOOL":
        if bit is None or not 0 <= bit <= 7:
            raise ValueError("a BOOL needs a bit 0..7")
        return bool(buf[offset] >> bit & 1)
    code, words, _, _, _ = TYPES[dtype]
    raw = bytes(buf[offset:offset + 2 * words])
    if len(raw) != 2 * words:
        raise ValueError(f"{dtype} at byte {offset} runs past the end of the buffer")
    if word_order == "little":
        raw = _swap_words(raw)
    return struct.unpack(">" + code, raw)[0]


def encode_bytes(value, dtype: str, word_order: str = "big") -> bytes:
    """One value -> its bytes (S7 layout). BOOL is not encoded here (it is a bit, see s7.py)."""
    if dtype == "BOOL":
        raise ValueError("a BOOL is a single bit; write it with a read-modify-write of its byte")
    code, _, is_int, lo, hi = TYPES[dtype]
    value = _checked(value, dtype, is_int, lo, hi)
    raw = struct.pack(">" + code, value)
    return _swap_words(raw) if word_order == "little" else raw


# ------------------------------------------------------------------ words --
def decode_words(words, dtype: str, word_order: str = "big"):
    """16-bit register values (0..65535, as Modbus returns them) -> one value."""
    if dtype == "BOOL":
        return bool(words[0])
    need = TYPES[dtype][1]
    if len(words) < need:
        raise ValueError(f"{dtype} needs {need} registers, got {len(words)}")
    raw = b"".join(struct.pack(">H", int(w) & 0xFFFF) for w in words[:need])
    return decode_bytes(raw, 0, dtype, word_order)


def encode_words(value, dtype: str, word_order: str = "big") -> list[int]:
    """One value -> 16-bit register values (0..65535), ready for FC06 / FC16."""
    if dtype == "BOOL":
        return [1 if value else 0]
    raw = encode_bytes(value, dtype, word_order)
    return [struct.unpack_from(">H", raw, i)[0] for i in range(0, len(raw), 2)]


# ------------------------------------------------------------------ checks --
def _checked(value, dtype, is_int, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{value!r} is not a number")
    if not math.isfinite(value):
        raise ValueError(f"{value!r} is not finite")
    if not is_int:
        if abs(value) > 3.4028234663852886e38:
            raise ValueError(f"{value!r} is outside the FLOAT32 range")
        return float(value)
    iv = int(round(value))
    if not lo <= iv <= hi:
        raise ValueError(f"{value!r} is outside the {dtype} range {lo}..{hi}")
    return iv