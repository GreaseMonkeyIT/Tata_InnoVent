"""The memory image of FLEET.md section 3, shared by every profile.

Areas and sizes:
  %IX discrete inputs   64 bits   (index = byte*8 + bit), written by the field port
  %QX coils             64 bits   written by the task
  %IW input words       64 INT    written by the field port
  %QW output words      64 INT    written by the task
  %MW memory words      64 INT    written by the task, the runtime (system words), and SCADA

Words hold signed 16-bit values (-32768..32767). The protocol layers convert to unsigned
16-bit register values at the edge.
"""
import re

N_BITS = 64
N_WORDS = 64
SYSTEM_WORDS = 5          # %MW0..%MW4 belong to the runtime (section 3.2)

AREAS = ("IX", "QX", "IW", "QW", "MW")
BIT_AREAS = ("IX", "QX")
INPUT_AREAS = ("IX", "IW")

_ADDR_RE = re.compile(r"^%([IQM])([XW])(\d+)(?:\.(\d+))?$")


class Address:
    """A parsed direct address. area is one of AREAS, index is the flat index in that area."""
    __slots__ = ("area", "index", "text")

    def __init__(self, area, index, text):
        self.area, self.index, self.text = area, index, text

    @property
    def is_bit(self):
        return self.area in BIT_AREAS

    @property
    def is_input(self):
        return self.area in INPUT_AREAS

    def __eq__(self, other):
        return isinstance(other, Address) and (self.area, self.index) == (other.area, other.index)

    def __hash__(self):
        return hash((self.area, self.index))

    def __repr__(self):
        return f"Address({self.text})"


def parse_address(text):
    """Parse '%IX0.3', '%QW5', or '%MW20'. Raise ValueError with a plain message if it is invalid."""
    m = _ADDR_RE.match(text.strip().upper())
    if not m:
        raise ValueError(f"bad address '{text}', use %IXa.b, %QXa.b, %IWn, %QWn or %MWn")
    prefix, size, num, bit = m.group(1), m.group(2), int(m.group(3)), m.group(4)
    area = prefix + size
    if area == "MX":
        raise ValueError(f"bad address '{text}', %MX bits are not in the image, use %MWn")
    if size == "X":
        if bit is None:
            raise ValueError(f"bad address '{text}', a bit address needs byte.bit such as %IX0.3")
        b = int(bit)
        if not 0 <= num <= 7 or not 0 <= b <= 7:
            raise ValueError(f"address '{text}' is out of range, bits run from {prefix}X0.0 to {prefix}X7.7")
        return Address(area, num * 8 + b, f"%{area}{num}.{b}")
    if bit is not None:
        raise ValueError(f"bad address '{text}', a word address has no bit part")
    if not 0 <= num < N_WORDS:
        raise ValueError(f"address '{text}' is out of range, words run from %{area}0 to %{area}63")
    return Address(area, num, f"%{area}{num}")


def wrap16(v):
    """Wrap an integer to signed 16-bit, like an INT."""
    return ((int(v) + 0x8000) & 0xFFFF) - 0x8000


def to_u16(v):
    """Signed word to the unsigned 16-bit register value."""
    return int(v) & 0xFFFF


def from_u16(v):
    """Unsigned 16-bit register value to a signed word."""
    return wrap16(v)


class Image:
    """One copy of the five areas. The scan thread owns the working copy."""
    __slots__ = ("ix", "qx", "iw", "qw", "mw")

    def __init__(self):
        self.ix = [False] * N_BITS
        self.qx = [False] * N_BITS
        self.iw = [0] * N_WORDS
        self.qw = [0] * N_WORDS
        self.mw = [0] * N_WORDS

    def area(self, name):
        return getattr(self, name.lower())

    def copy(self):
        c = Image()
        c.ix, c.qx, c.iw, c.qw, c.mw = list(self.ix), list(self.qx), list(self.iw), list(self.qw), list(self.mw)
        return c

    def clear_outputs(self):
        """Set every %QX and %QW to 0 (STOP and FAULT)."""
        for i in range(N_BITS):
            self.qx[i] = False
        for i in range(N_WORDS):
            self.qw[i] = 0

    def as_dict(self):
        return {"IX": list(self.ix), "QX": list(self.qx), "IW": list(self.iw),
                "QW": list(self.qw), "MW": list(self.mw)}
