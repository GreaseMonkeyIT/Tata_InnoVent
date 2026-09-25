"""drivers/codec.py: every vendor data type, both word orders, encode and decode."""
import math
import struct

import pytest

from drivers.codec import (ALL_TYPES, decode_bytes, decode_words, encode_bytes, encode_words,
                           n_bytes, n_words, norm_word_order)

SAMPLES = {
    "INT16": [0, 1, -1, 32767, -32768, 1234],
    "UINT16": [0, 1, 65535, 40000],
    "INT32": [0, -1, 2 ** 31 - 1, -(2 ** 31), 123456789, -70000],
    "UINT32": [0, 2 ** 32 - 1, 70000, 3000000000],
    "FLOAT32": [0.0, 1.5, -42.25, 398.75, 1e-3, 3.0e38],
}


@pytest.mark.parametrize("dtype", list(SAMPLES))
@pytest.mark.parametrize("order", ["big", "little"])
def test_round_trip_words_and_bytes(dtype, order):
    for v in SAMPLES[dtype]:
        w = encode_words(v, dtype, order)
        assert len(w) == n_words(dtype) and all(0 <= x <= 0xFFFF for x in w)
        back = decode_words(w, dtype, order)
        b = encode_bytes(v, dtype, order)
        assert len(b) == n_bytes(dtype)
        back_b = decode_bytes(b, 0, dtype, order)
        if dtype == "FLOAT32":
            assert back == pytest.approx(v, rel=1e-6) and back_b == pytest.approx(v, rel=1e-6)
        else:
            assert back == v and back_b == v


def test_known_wire_patterns():
    # FLOAT32 42.3 is 0x42293333: ABCD for big, CDAB for little (word swap)
    assert encode_words(42.3, "FLOAT32", "big") == [0x4229, 0x3333]
    assert encode_words(42.3, "FLOAT32", "little") == [0x3333, 0x4229]
    assert decode_words([0x3333, 0x4229], "FLOAT32", "little") == pytest.approx(42.3, rel=1e-6)
    # INT32 -2 is FFFF FFFE; UINT32 0x00010002 = 65538
    assert encode_words(-2, "INT32", "big") == [0xFFFF, 0xFFFE]
    assert decode_words([0x0002, 0x0001], "UINT32", "little") == 65538
    # a 16-bit type ignores word order
    assert encode_words(-2, "INT16", "little") == [0xFFFE]
    assert decode_words([0xFFFE], "UINT16", "big") == 65534
    assert encode_bytes(-2, "INT16") == b"\xff\xfe"
    assert encode_bytes(1.0, "FLOAT32", "little") == struct.pack(">f", 1.0)[2:] + struct.pack(">f", 1.0)[:2]


def test_bool_and_offsets():
    buf = bytes([0b00000000, 0b10000100, 0x42, 0x29, 0x33, 0x33])
    assert decode_bytes(buf, 1, "BOOL", bit=2) is True
    assert decode_bytes(buf, 1, "BOOL", bit=7) is True
    assert decode_bytes(buf, 0, "BOOL", bit=0) is False
    assert decode_bytes(buf, 2, "FLOAT32") == pytest.approx(42.3, rel=1e-6)
    assert decode_words([1], "BOOL") is True and encode_words(True, "BOOL") == [1]
    with pytest.raises(ValueError):
        decode_bytes(buf, 0, "BOOL", bit=8)
    with pytest.raises(ValueError):
        encode_bytes(True, "BOOL")                       # a bit is written by read-modify-write
    with pytest.raises(ValueError):
        decode_bytes(buf, 4, "FLOAT32")                  # runs past the end
    with pytest.raises(ValueError):
        decode_words([1], "INT32")


def test_scaled_int_rounds_and_out_of_range_is_refused():
    assert encode_words(12.6, "INT16") == [13]
    for dtype, bad in (("INT16", 32768), ("INT16", -32769), ("UINT16", -1), ("UINT16", 65536),
                       ("INT32", 2 ** 31), ("UINT32", -1), ("FLOAT32", 1e39)):
        with pytest.raises(ValueError):
            encode_words(bad, dtype)
    for bad in (math.nan, math.inf, "12", True, None):
        with pytest.raises(ValueError):
            encode_words(bad, "INT16")


def test_word_order_aliases():
    assert norm_word_order("ABCD") == "big" and norm_word_order("cdab") == "little"
    assert norm_word_order("high_first") == "big" and norm_word_order("low_first") == "little"
    assert norm_word_order("BADC") is None and norm_word_order(None) is None
    assert ALL_TYPES == ("BOOL", "INT16", "UINT16", "INT32", "UINT32", "FLOAT32")