"""Register maps: where a vendor's PLC keeps each value (regmaps/README.md).

The drivers still return the same image as before (FLEET.md 3: ix, qx, iw, qw, mw with 64
entries each), so fleet.py, the tag names, and everything downstream are unchanged. What
moved out of the code is the answer to "where on this PLC does image slot %IW0 live, and in
what format?". That answer is now a YAML file per vendor or model:

    vendor: Schneider Electric
    protocol: modbus
    points:
      - slot: "%IW0"                # the image slot the tag table reads (FLEET.<plc>.<m0>.AMPS)
        signal: AMPS                # documentation only
        area: input_register
        address: 101                # as printed in the vendor manual
        base: 1                     # that manual counts from 1
        type: INT16
        scale: 0.1                  # image value = vendor value x scale

A PLC enrolled without a map uses default_map(), which is exactly the old fixed layout, so
the virtual PLCs and OpenPLC behave as before, frame for frame.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from . import N_BITS, N_WORDS, INT16_MIN, INT16_MAX
from .codec import ALL_TYPES, TYPES, n_bytes, n_words, norm_word_order

MODBUS_AREAS = ("discrete_input", "coil", "input_register", "holding_register")
S7_AREAS = ("db", "input", "output", "marker")
BIT_AREAS = ("discrete_input", "coil")
WRITABLE_AREAS = ("coil", "holding_register", "db", "output", "marker")
DIRECTIONS = ("read", "write", "read_write")
PROTOCOLS = {"modbus": "modbus", "s7comm": "s7comm", "s7": "s7comm"}
MODBUS_MAX_REGS, MODBUS_MAX_BITS, S7_MAX_BYTES = 125, 2000, 65535

_TOP_KEYS = {"vendor", "model", "protocol", "notes", "defaults", "read", "points"}
_POINT_KEYS = {"slot", "signal", "area", "address", "base", "db", "offset", "bit", "type",
               "word_order", "scale", "direction", "notes"}
_DEFAULT_KEYS = {"base", "type", "word_order", "scale", "direction", "area", "db"}
_BIT_SLOT = re.compile(r"^%([IQ])X([0-7])\.([0-7])\Z")
_WORD_SLOT = re.compile(r"^%([IQM])W([0-9]{1,2})\Z")
_MAP_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}\Z")


class RegMapError(ValueError):
    """The register map is not valid. The message names every bad field."""


def regmap_dir() -> str:
    """REGMAP_DIR, or the regmaps/ folder next to the tag server."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("REGMAP_DIR") or os.path.join(here, "regmaps")


def parse_slot(slot) -> tuple[str, int]:
    """'%IX0.3' -> ('ix', 3). '%MW20' -> ('mw', 20). The same rule as fleet.parse_address."""
    s = str(slot).strip().upper() if isinstance(slot, str) else ""
    m = _BIT_SLOT.match(s)
    if m:
        return ("ix" if m.group(1) == "I" else "qx"), int(m.group(2)) * 8 + int(m.group(3))
    m = _WORD_SLOT.match(s)
    if m and int(m.group(2)) < N_WORDS:
        return {"I": "iw", "Q": "qw", "M": "mw"}[m.group(1)], int(m.group(2))
    raise ValueError(f"{slot!r} is not an image slot (%IX/%QX 0.0..7.7, %IW/%QW/%MW 0..63)")


@dataclass(frozen=True)
class Point:
    slot: str             # "%IW0"
    slot_area: str        # "iw"
    slot_index: int       # 0
    area: str             # modbus or S7 area
    start: int            # zero-based register/bit (Modbus) or byte offset (S7)
    type: str
    word_order: str = "big"
    scale: float = 1
    direction: str = "read"
    db: int = 0           # S7 data block (area db only)
    bit: int | None = None
    signal: str | None = None

    @property
    def size(self) -> int:
        """Footprint in the span unit: registers or bits (Modbus), bytes (S7)."""
        if self.area in MODBUS_AREAS:
            return 1 if self.area in BIT_AREAS else n_words(self.type)
        return n_bytes(self.type)

    @property
    def reads(self) -> bool:
        return self.direction in ("read", "read_write")

    @property
    def writes(self) -> bool:
        return self.direction in ("write", "read_write")


@dataclass
class Span:
    """One protocol read: a contiguous run of one area (and one DB)."""
    area: str
    db: int
    start: int
    count: int
    points: list = field(default_factory=list)


@dataclass
class RegMap:
    protocol: str
    points: list
    vendor: str = ""
    model: str = ""
    source: str = "<default>"
    max_gap: int = 0

    def __post_init__(self):
        self._writes = {p.slot_index: p for p in self.points if p.slot_area == "mw" and p.writes}
        self.spans = plan_reads(self)

    @property
    def mapped_slots(self) -> set:
        return {(p.slot_area, p.slot_index) for p in self.points if p.reads}

    def write_point(self, index: int) -> Point:
        p = self._writes.get(index)
        if p is None:
            raise ValueError(f"%MW{index} is not mapped as writable in {self.source}")
        return p

    def build_image(self, data: dict) -> tuple[dict, list]:
        """{span index: raw data} -> (image, [slots whose value was clamped to INT16])."""
        from .codec import decode_bytes, decode_words
        image = {"ix": [False] * N_BITS, "qx": [False] * N_BITS, "iw": [0] * N_WORDS,
                 "qw": [0] * N_WORDS, "mw": [0] * N_WORDS}
        clamped = []
        for i, span in enumerate(self.spans):
            raw = data[i]
            for p in span.points:
                rel = p.start - span.start
                if self.protocol == "modbus":
                    if p.area in BIT_AREAS:
                        value = bool(raw[rel])
                    else:
                        value = decode_words(raw[rel:rel + n_words(p.type)], p.type, p.word_order)
                else:
                    value = decode_bytes(raw, rel, p.type, p.word_order, p.bit)
                if p.slot_area in ("ix", "qx"):
                    image[p.slot_area][p.slot_index] = bool(value)
                    continue
                v = value * p.scale if p.scale != 1 else value
                iv = int(round(v))
                if not INT16_MIN <= iv <= INT16_MAX:
                    iv = max(INT16_MIN, min(INT16_MAX, iv))
                    clamped.append(p.slot)
                image[p.slot_area][p.slot_index] = iv
        return image, clamped


def vendor_value(p: Point, image_value: int):
    """An image %MW value (what fleet.write_plan produced) -> the value the PLC should hold."""
    v = image_value / p.scale if p.scale != 1 else image_value
    return bool(v) if p.type == "BOOL" else v


# ------------------------------------------------------------------ planning --
def plan_reads(m: RegMap) -> list[Span]:
    """Group the read points into as few protocol reads as the limits allow.

    Points merge into one span when they are in the same area (and DB) and the gap between
    them is at most max_gap units. max_gap 0 reads only what is mapped, which is the safe
    default: some PLCs answer an error for any unmapped address inside a read."""
    order = {a: i for i, a in enumerate(MODBUS_AREAS + S7_AREAS)}
    pts = sorted((p for p in m.points if p.reads), key=lambda p: (order[p.area], p.db, p.start))
    if m.protocol == "modbus":
        limit = lambda area: MODBUS_MAX_BITS if area in BIT_AREAS else MODBUS_MAX_REGS
    else:
        limit = lambda area: S7_MAX_BYTES
    spans: list[Span] = []
    for p in pts:
        cur = spans[-1] if spans else None
        end = p.start + p.size
        if (cur is not None and cur.area == p.area and cur.db == p.db
                and p.start <= cur.start + cur.count + m.max_gap
                and max(end, cur.start + cur.count) - cur.start <= limit(p.area)):
            cur.count = max(cur.count, end - cur.start)
            cur.points.append(p)
        else:
            spans.append(Span(p.area, p.db, p.start, p.size, [p]))
    return spans


# ------------------------------------------------------------------ default --
def default_map(kind: str, db: int = 1) -> RegMap:
    """The fixed layout the drivers used before register maps (FLEET.md 4.2 and 4.3)."""
    pts = []
    if kind == "modbus":
        for i in range(N_BITS):
            pts.append(Point(_bit_slot("I", i), "ix", i, "discrete_input", i, "BOOL"))
            pts.append(Point(_bit_slot("Q", i), "qx", i, "coil", i, "BOOL"))
        for n in range(N_WORDS):
            pts.append(Point(f"%IW{n}", "iw", n, "input_register", n, "INT16"))
            pts.append(Point(f"%QW{n}", "qw", n, "holding_register", n, "INT16"))
            pts.append(Point(f"%MW{n}", "mw", n, "holding_register", 1024 + n, "INT16",
                             direction="read_write"))
        return RegMap("modbus", pts, vendor="VISR", model="generic-iec (FLEET.md 4.2)")
    if kind == "s7comm":
        for i in range(N_BITS):
            pts.append(Point(_bit_slot("I", i), "ix", i, "db", i // 8, "BOOL", db=db, bit=i % 8))
            pts.append(Point(_bit_slot("Q", i), "qx", i, "db", 8 + i // 8, "BOOL", db=db, bit=i % 8))
        for n in range(N_WORDS):
            pts.append(Point(f"%IW{n}", "iw", n, "db", 16 + 2 * n, "INT16", db=db))
            pts.append(Point(f"%QW{n}", "qw", n, "db", 144 + 2 * n, "INT16", db=db))
            pts.append(Point(f"%MW{n}", "mw", n, "db", 272 + 2 * n, "INT16", db=db,
                             direction="read_write"))
        return RegMap("s7comm", pts, vendor="VISR", model="siemens-s7-1200 (FLEET.md 4.3)")
    raise RegMapError(f"no default map for protocol {kind!r}")


def _bit_slot(letter: str, i: int) -> str:
    return f"%{letter}X{i // 8}.{i % 8}"


# ------------------------------------------------------------------ loading --
def map_path(name: str) -> str:
    """A map name from the enrollment ('schneider-m221') -> its file in REGMAP_DIR."""
    if not isinstance(name, str) or not _MAP_NAME.match(name):
        raise RegMapError(f"map name {name!r} must match ^[a-z0-9][a-z0-9._-]{{0,63}}$")
    return os.path.join(regmap_dir(), name + ".yaml")


def load_map(path: str) -> RegMap:
    """Read and validate one map file. Raise RegMapError listing every problem."""
    import yaml
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        raise RegMapError(f"{path}: register map file not found") from None
    except yaml.YAMLError as e:
        raise RegMapError(f"{path}: not valid YAML: {e}") from None
    return parse_map(doc, source=path)


def parse_map(doc, source: str = "<map>") -> RegMap:
    """A parsed YAML document -> RegMap. Every error is collected, then raised together."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        raise RegMapError(f"{source}: the file must be a mapping with vendor, protocol, points")
    for k in sorted(set(doc) - _TOP_KEYS):
        errs.append(f"{k}: unknown key (allowed: {', '.join(sorted(_TOP_KEYS))})")
    vendor = doc.get("vendor")
    if not isinstance(vendor, str) or not vendor.strip():
        errs.append("vendor: required, a non-empty string")
    protocol = PROTOCOLS.get(str(doc.get("protocol", "")).strip().lower())
    if protocol is None:
        errs.append(f"protocol: {doc.get('protocol')!r} must be one of modbus, s7comm")
    defaults = doc.get("defaults") or {}
    if not isinstance(defaults, dict):
        errs.append("defaults: must be a mapping")
        defaults = {}
    for k in sorted(set(defaults) - _DEFAULT_KEYS):
        errs.append(f"defaults.{k}: unknown key (allowed: {', '.join(sorted(_DEFAULT_KEYS))})")
    read = doc.get("read") or {}
    max_gap = read.get("max_gap", 0) if isinstance(read, dict) else None
    if isinstance(max_gap, bool) or not isinstance(max_gap, int) or not 0 <= max_gap <= 100:
        errs.append("read.max_gap: must be an integer 0..100")
        max_gap = 0
    raw_points = doc.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        errs.append("points: required, a non-empty list")
        raw_points = []

    points: list[Point] = []
    seen_slots: dict[str, int] = {}
    for i, rp in enumerate(raw_points):
        where = f"points[{i}]"
        if not isinstance(rp, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        p = _parse_point({**defaults, **rp}, where, protocol, errs, set(rp))
        if p is None:
            continue
        key = f"{p.slot_area}{p.slot_index}"
        if key in seen_slots:
            errs.append(f"{where}.slot: {p.slot} is already mapped by points[{seen_slots[key]}]")
            continue
        seen_slots[key] = i
        points.append(p)

    if errs:
        raise RegMapError(f"{source}: {len(errs)} problem(s):\n  - " + "\n  - ".join(errs))
    return RegMap(protocol, points, vendor=vendor.strip(), model=str(doc.get("model") or ""),
                  source=source, max_gap=max_gap)


def _parse_point(d: dict, where: str, protocol, errs: list, own_keys: set):
    n0 = len(errs)
    for k in sorted(own_keys - _POINT_KEYS):
        errs.append(f"{where}.{k}: unknown key (allowed: {', '.join(sorted(_POINT_KEYS))})")
    try:
        slot_area, slot_index = parse_slot(d.get("slot"))
        slot = d["slot"].strip().upper()
    except (ValueError, KeyError) as e:
        errs.append(f"{where}.slot: {e}" if d.get("slot") is not None else f"{where}.slot: required")
        slot_area, slot_index, slot = None, None, None

    dtype = str(d.get("type", "")).strip().upper()
    if dtype not in ALL_TYPES:
        errs.append(f"{where}.type: {d.get('type')!r} must be one of {', '.join(ALL_TYPES)}")
        dtype = None
    word_order = norm_word_order(d.get("word_order", "big"))
    if word_order is None:
        errs.append(f"{where}.word_order: {d.get('word_order')!r} must be big (ABCD) or little (CDAB)")
    scale = d.get("scale", 1)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) \
            or scale == 0:
        errs.append(f"{where}.scale: {scale!r} must be a non-zero number")
        scale = 1
    direction = str(d.get("direction", "read")).strip().lower()
    if direction not in DIRECTIONS:
        errs.append(f"{where}.direction: {d.get('direction')!r} must be one of {', '.join(DIRECTIONS)}")
        direction = "read"
    area = str(d.get("area", "")).strip().lower()

    start, db, bit = None, 0, None
    if protocol == "modbus":
        if area not in MODBUS_AREAS:
            errs.append(f"{where}.area: {d.get('area')!r} must be one of {', '.join(MODBUS_AREAS)}")
        for k in ("db", "offset", "bit"):
            if k in own_keys:
                errs.append(f"{where}.{k}: is an S7 field, not used by modbus")
        base = d.get("base", 0)
        if base not in (0, 1) or isinstance(base, bool):
            errs.append(f"{where}.base: {base!r} must be 0 or 1")
            base = 0
        addr = d.get("address")
        if isinstance(addr, bool) or not isinstance(addr, int) or addr < base:
            errs.append(f"{where}.address: required, an integer >= {base} (base {base})")
        else:
            start = addr - base
        if dtype and area in MODBUS_AREAS:
            if area in BIT_AREAS and dtype != "BOOL":
                errs.append(f"{where}.type: a {area} holds one bit, so the type must be BOOL")
            if area not in BIT_AREAS and dtype == "BOOL":
                errs.append(f"{where}.type: BOOL needs area coil or discrete_input, not {area}")
            if start is not None and dtype != "BOOL" and start + n_words(dtype) - 1 > 65535:
                errs.append(f"{where}.address: {dtype} at {addr} runs past register 65535")
    elif protocol == "s7comm":
        if area not in S7_AREAS:
            errs.append(f"{where}.area: {d.get('area')!r} must be one of {', '.join(S7_AREAS)}")
        for k in ("address", "base"):
            if k in own_keys:
                errs.append(f"{where}.{k}: is a modbus field; S7 uses offset (a byte number)")
        off = d.get("offset")
        if isinstance(off, bool) or not isinstance(off, int) or off < 0:
            errs.append(f"{where}.offset: required, a byte offset >= 0")
        else:
            start = off
        if area == "db":
            db = d.get("db")
            if isinstance(db, bool) or not isinstance(db, int) or not 1 <= db <= 65535:
                errs.append(f"{where}.db: area db needs a data block number 1..65535")
                db = 0
        elif "db" in own_keys:
            errs.append(f"{where}.db: only area db has a data block number")
        if dtype == "BOOL":
            bit = d.get("bit")
            if isinstance(bit, bool) or not isinstance(bit, int) or not 0 <= bit <= 7:
                errs.append(f"{where}.bit: a BOOL needs bit 0..7")
                bit = None
        elif "bit" in own_keys:
            errs.append(f"{where}.bit: only a BOOL has a bit")

    if direction in ("write", "read_write"):
        if slot_area is not None and slot_area != "mw":
            errs.append(f"{where}.direction: only %MW slots are written (write_mw); {slot} is read only")
        if area and area not in WRITABLE_AREAS:
            errs.append(f"{where}.direction: area {area} cannot be written")
    if len(errs) > n0:
        return None
    sig = d.get("signal")
    return Point(slot, slot_area, slot_index, area, start, dtype, word_order, scale, direction,
                 db, bit, str(sig) if sig is not None else None)