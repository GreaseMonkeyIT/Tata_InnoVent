"""FLEET.md 8: the SCADA view of the virtual PLC fleet, pure and unit-testable.

tagserver.py owns the I/O: poll threads, protocol drivers, the historian, and HTTP. This
module owns the rules, so all of them are testable without a PLC:

  - the enrollment token check (hex HMAC-SHA256 of FLEET_ENROLL_KEY over the PLC name)
  - the parse of an enrollment body (FLEET.md 6)
  - the tag table from the cell wiring convention (3.1), the system words (3.2),
    CELL_ENABLE, and io_extra
  - the decode of a read image into scaled tag values
  - quality aging (the GOOD, STALE, BAD rules of tags.py)
  - the write gate (writable tags only, range checks)
  - the /fleet, /domains, and /metrics/fleet bodies

Tag names are FLEET.<PLC>.<ASSET>.<SIGNAL>, upper case, and "-" becomes "_".
Every value is measured: decode runs only on a successful protocol read.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re

import tags

NS = "fleet"
NAME_RE = re.compile(r"^plc-[a-z0-9]([-a-z0-9]{0,16}[a-z0-9])?\Z")    # \Z: no trailing newline
PART_RE = re.compile(r"^[A-Za-z0-9_-]{1,63}\Z")
PROTOCOL_KINDS = {"modbus": 502, "s7comm": 102}         # kind -> default SCADA port
DIRECTIONS = ("in", "out", "setpoint", "system")
MAX_MACHINES = 8
N_BITS = 64
N_WORDS = 64
INT16_MIN, INT16_MAX = -32768, 32767
SYSTEM_MW = range(0, 5)                                  # %MW0..%MW4, the runtime writes them
CELL_ENABLE_MW = 8
DERATE_MW_BASE = 10

_BIT_RE = re.compile(r"^%([IQ])X([0-7])\.([0-7])\Z")
_WORD_RE = re.compile(r"^%([IQM])W([0-9]{1,2})\Z")


class EnrollError(ValueError):
    """The enrollment body is not valid. The server answers 400."""


class WriteDenied(Exception):
    """The tag is unknown or not writable. The server answers 403."""


# ------------------------------------------------------------------ tokens --
def enroll_token(key: str, name: str) -> str:
    """The device token a PLC must send: hex HMAC-SHA256(key, name)."""
    return hmac.new(key.encode(), name.encode(), hashlib.sha256).hexdigest()


def check_enroll_token(key: str, name: str, token: str | None) -> bool:
    """True only when the key is set and the token matches. An empty key fails closed."""
    if not key or not name or not token:
        return False
    return hmac.compare_digest(enroll_token(key, name).encode(), token.encode())


def check_write_token(expected: str, token: str | None) -> bool:
    """Constant-time compare of X-Scada-Token. An empty expected token fails closed."""
    if not expected or not token:
        return False
    return hmac.compare_digest(expected.encode(), token.encode())


# ---------------------------------------------------------------- names ---
def norm(part) -> str:
    return str(part).upper().replace("-", "_")


def tag_name(plc: str, asset: str, signal: str) -> str:
    return f"FLEET.{norm(plc)}.{norm(asset)}.{norm(signal)}"


def parse_address(address) -> tuple[str, int]:
    """'%IX0.3' -> ('ix', 3). '%MW20' -> ('mw', 20). Raise EnrollError outside the image."""
    if not isinstance(address, str):
        raise EnrollError(f"address {address!r} is not a string")
    a = address.strip().upper()
    m = _BIT_RE.match(a)
    if m:
        return ("ix" if m.group(1) == "I" else "qx"), int(m.group(2)) * 8 + int(m.group(3))
    m = _WORD_RE.match(a)
    if m and int(m.group(2)) < N_WORDS:
        return {"I": "iw", "Q": "qw", "M": "mw"}[m.group(1)], int(m.group(2))
    raise EnrollError(f"address {address!r} is outside the image (%IX/%QX 0.0..7.7, %IW/%QW/%MW 0..63)")


# ------------------------------------------------------------- enrollment --
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v, what: str, lo: int, hi: int, default=None) -> int:
    if v is None and default is not None:
        return default
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise EnrollError(f"{what} must be an integer in {lo}..{hi}")
    return v


def _str(v, what: str, max_len: int = 200, required: bool = False):
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v or len(v) > max_len:
        raise EnrollError(f"{what} must be a non-empty string of at most {max_len} characters")
    return v


def parse_enrollment(body) -> dict:
    """Validate and normalize a POST /enroll body (FLEET.md 6). Raise EnrollError."""
    if not isinstance(body, dict):
        raise EnrollError("the body must be a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise EnrollError("name must match ^plc-[a-z0-9]([-a-z0-9]{0,16}[a-z0-9])?$")
    profile = _str(body.get("profile"), "profile", 64, required=True)

    p = body.get("protocol")
    if not isinstance(p, dict):
        raise EnrollError("protocol must be an object")
    kind = p.get("kind")
    if kind not in PROTOCOL_KINDS:
        raise EnrollError(f"protocol.kind must be one of {sorted(PROTOCOL_KINDS)}")
    protocol = {
        "kind": kind,
        "host": _str(p.get("host"), "protocol.host", 253, required=True),
        "port": _int(p.get("port"), "protocol.port", 1, 65535, PROTOCOL_KINDS[kind]),
    }
    if kind == "s7comm":
        protocol["rack"] = _int(p.get("rack"), "protocol.rack", 0, 7, 0)
        protocol["slot"] = _int(p.get("slot"), "protocol.slot", 0, 31, 1)
        protocol["db"] = _int(p.get("db"), "protocol.db", 1, 65535, 1)
    else:
        protocol["rack"] = protocol["slot"] = protocol["db"] = None
        protocol["unit"] = _int(p.get("unit"), "protocol.unit", 0, 255, 1)

    t = body.get("task") or {}
    if not isinstance(t, dict):
        raise EnrollError("task must be an object")
    interval = t.get("interval_ms")
    if interval is not None and not _num(interval):
        raise EnrollError("task.interval_ms must be a number")
    task = {"name": _str(t.get("name"), "task.name"), "title": _str(t.get("title"), "task.title"),
            "sha256": _str(t.get("sha256"), "task.sha256", 128), "interval_ms": interval}

    c = body.get("cell") or {}
    if not isinstance(c, dict):
        raise EnrollError("cell must be an object")
    machines = c.get("machines") or []
    if not isinstance(machines, list) or len(machines) > MAX_MACHINES:
        raise EnrollError(f"cell.machines must be a list of at most {MAX_MACHINES} names")
    for m in machines:
        if not isinstance(m, str) or not PART_RE.match(m):
            raise EnrollError(f"cell.machines entry {m!r} is not a valid machine name")
    if len(set(machines)) != len(machines):
        raise EnrollError("cell.machines has a duplicate name")
    cell_name = c.get("name")
    if cell_name is not None and (not isinstance(cell_name, str) or not PART_RE.match(cell_name)):
        raise EnrollError(f"cell.name {cell_name!r} is not a valid cell name")
    cell = {"name": cell_name, "rail": _str(c.get("rail"), "cell.rail", 63), "machines": list(machines)}

    io_extra = body.get("io_extra") or []
    if not isinstance(io_extra, list):
        raise EnrollError("io_extra must be a list")
    runtime = body.get("runtime") if isinstance(body.get("runtime"), dict) else None

    return {"name": name, "profile": profile, "protocol": protocol, "task": task, "cell": cell,
            "io_extra": io_extra, "runtime": runtime}


# -------------------------------------------------------------- tag table --
def _row(plc, asset, signal, address, unit, direction, scale=1, writable=False, lo=None, hi=None):
    area, index = parse_address(address)
    is_bool = area in ("ix", "qx")
    return {"tag": tag_name(plc, asset, signal), "asset": asset, "signal": norm(signal),
            "address": address, "area": area, "index": index,
            "type": "BOOL" if is_bool else "INT", "unit": unit, "scale": scale,
            "direction": direction, "writable": writable, "min": lo, "max": hi}


def _extra_row(plc: str, e, i: int) -> dict:
    where = f"io_extra[{i}]"
    if not isinstance(e, dict):
        raise EnrollError(f"{where} must be an object")
    asset, signal = e.get("asset"), e.get("signal")
    for label, v in (("asset", asset), ("signal", signal)):
        if not isinstance(v, str) or not PART_RE.match(v):
            raise EnrollError(f"{where}.{label} {v!r} is not a valid name")
    area, index = parse_address(e.get("address"))
    scale = e.get("scale", 1)
    if not _num(scale) or scale <= 0:
        raise EnrollError(f"{where}.scale must be a positive number")
    writable = e.get("writable", False) is True
    if writable and (area != "mw" or index in SYSTEM_MW):
        raise EnrollError(f"{where}: only %MW5..%MW63 can be writable")
    if area == "mw" and index in SYSTEM_MW:
        default_dir = "system"
    elif area in ("ix", "iw"):
        default_dir = "in"
    elif area == "mw" and writable:
        default_dir = "setpoint"
    else:
        default_dir = "out"
    direction = e.get("direction", default_dir)
    if direction not in DIRECTIONS:
        raise EnrollError(f"{where}.direction must be one of {list(DIRECTIONS)}")
    lo, hi = None, None
    if writable:
        lo = e.get("min", INT16_MIN / scale)
        hi = e.get("max", INT16_MAX / scale)
        if not _num(lo) or not _num(hi) or lo > hi:
            raise EnrollError(f"{where}.min and max must be numbers with min <= max")
    unit = e.get("unit", "")
    address = e["address"].strip().upper()
    return _row(plc, asset, signal, address, str(unit), direction, scale, writable, lo, hi)


def tag_table(name: str, machines: list[str], cell_name: str | None = None,
              io_extra: list | None = None) -> list[dict]:
    """The tag table of one PLC. Machine index k is the position in `machines`.

    Per machine (FLEET.md 3.1): AMPS, TEMP, VOLTS, THROUGHPUT, READY, RUN, SPEED_PCT,
    DERATE_PCT. Per PLC: CELL_ENABLE (asset = the cell name, or CELL when the cell has no
    name) and the system words SYS.SCAN_MS, SCAN_COUNT, STATE, OVERRUNS, TASK_CRC (3.2).
    Then io_extra. Raise EnrollError on a bad entry or a duplicate tag name."""
    if len(machines) > MAX_MACHINES:
        raise EnrollError(f"a cell has at most {MAX_MACHINES} machines")
    t: list[dict] = []
    for k, m in enumerate(machines):
        t.append(_row(name, m, "AMPS", f"%IW{4 * k}", "A", "in", 10))
        t.append(_row(name, m, "TEMP", f"%IW{4 * k + 1}", "degC", "in", 10))
        t.append(_row(name, m, "VOLTS", f"%IW{4 * k + 2}", "V", "in", 10))
        t.append(_row(name, m, "THROUGHPUT", f"%IW{4 * k + 3}", "pct", "in", 10))
        t.append(_row(name, m, "READY", f"%IX0.{k}", "bool", "in"))
        t.append(_row(name, m, "RUN", f"%QX0.{k}", "bool", "out"))
        t.append(_row(name, m, "SPEED_PCT", f"%QW{k}", "pct", "out"))
        t.append(_row(name, m, "DERATE_PCT", f"%MW{DERATE_MW_BASE + k}", "pct", "setpoint",
                      writable=True, lo=0, hi=100))
    t.append(_row(name, cell_name or "CELL", "CELL_ENABLE", f"%MW{CELL_ENABLE_MW}", "bool",
                  "setpoint", writable=True, lo=0, hi=1))
    t.append(_row(name, "SYS", "SCAN_MS", "%MW0", "ms", "system", 100))
    t.append(_row(name, "SYS", "SCAN_COUNT", "%MW1", "count", "system"))
    t.append(_row(name, "SYS", "STATE", "%MW2", "enum", "system"))
    t.append(_row(name, "SYS", "OVERRUNS", "%MW3", "count", "system"))
    t.append(_row(name, "SYS", "TASK_CRC", "%MW4", "crc15", "system"))
    for i, e in enumerate(io_extra or []):
        t.append(_extra_row(name, e, i))
    seen: set[str] = set()
    for r in t:
        if r["tag"] in seen:
            raise EnrollError(f"duplicate tag name {r['tag']}")
        seen.add(r["tag"])
    return t


def table_for(reg: dict) -> list[dict]:
    """The tag table for a parsed enrollment."""
    return tag_table(reg["name"], reg["cell"]["machines"], reg["cell"]["name"], reg["io_extra"])


def registration_key(reg: dict, table: list[dict]) -> str:
    """Two enrollments with the same key are one registration: a heartbeat keeps the poll
    thread and first_good_at. A new task hash, protocol, or tag set gives a new key."""
    return json.dumps([reg["protocol"], reg["task"]["sha256"], [r["tag"] for r in table]],
                      sort_keys=True)


# ------------------------------------------------------------------ decode --
def check_image(image) -> None:
    if not isinstance(image, dict):
        raise ValueError("the image must be a dict")
    for area in ("ix", "qx", "iw", "qw", "mw"):
        if len(image.get(area) or []) < (N_BITS if area in ("ix", "qx") else N_WORDS):
            raise ValueError(f"image area {area} is short")


def decode(table: list[dict], image: dict, ts: float) -> dict[str, dict]:
    """A read image -> {tag: {value, quality, ts, asset, signal, raw}}.

    decode runs only on a successful read, so every record is GOOD at `ts`. When a poll
    fails, the server keeps the previous map and requality() ages it."""
    check_image(image)
    out: dict[str, dict] = {}
    for r in table:
        raw = image[r["area"]][r["index"]]
        if r["type"] == "BOOL":
            raw = 1 if raw else 0
            value = float(raw)
        else:
            raw = int(raw)
            value = raw / r["scale"]
        out[r["tag"]] = {"value": value, "quality": "GOOD", "ts": ts, "asset": r["asset"],
                         "signal": r["signal"], "raw": raw}
    return out


def requality(recs: dict[str, dict], now: float, stale_s: float = 10.0,
              bad_s: float = 30.0) -> dict[str, dict]:
    """The plant tag rules, unchanged: GOOD, then STALE after stale_s, then BAD after bad_s."""
    return tags.requality(recs, now, stale_s, bad_s)


# ------------------------------------------------------------------- write --
def write_plan(table: list[dict], tag, value) -> tuple[int, int]:
    """Check one write. Return (%MW index, raw INT).

    Raise WriteDenied when the tag is unknown or not writable (the server answers 403).
    Raise ValueError when the value is not a number or is out of range (400)."""
    row = next((r for r in table if r["tag"] == tag), None)
    if row is None:
        raise WriteDenied(f"tag {tag!r} is not a tag of this PLC")
    if not row["writable"] or row["area"] != "mw" or row["index"] in SYSTEM_MW:
        raise WriteDenied(f"tag {tag} is not writable")
    if isinstance(value, bool):
        value = int(value)
    if not _num(value):
        raise ValueError("value must be a finite number")
    if row["min"] is not None and value < row["min"]:
        raise ValueError(f"value {value} is below the minimum {row['min']} of {tag}")
    if row["max"] is not None and value > row["max"]:
        raise ValueError(f"value {value} is above the maximum {row['max']} of {tag}")
    raw = int(round(value * row["scale"]))
    if abs(value * row["scale"] - raw) > 1e-6:
        # refuse instead of rounding: the PLC must hold exactly the value the caller asked for
        raise ValueError(f"value {value} does not match the resolution 1/{row['scale']} of {tag}")
    if not INT16_MIN <= raw <= INT16_MAX:
        raise ValueError(f"value {value} does not fit a 16-bit INT at scale {row['scale']}")
    return row["index"], raw


# ------------------------------------------------------------------ bodies --
_TAG_KEYS = ("tag", "asset", "signal", "address", "type", "unit", "scale", "direction",
             "writable", "min", "max")


def tag_rows(table: list[dict], aged: dict[str, dict]) -> list[dict]:
    """The tags of one PLC for /fleet. A tag with no read yet is BAD with value null."""
    rows = []
    for r in table:
        rec = aged.get(r["tag"])
        row = {k: r[k] for k in _TAG_KEYS}
        row["value"] = rec["value"] if rec else None
        row["quality"] = rec["quality"] if rec else "BAD"
        row["ts"] = rec["ts"] if rec else None
        rows.append(row)
    return rows


def plc_json(snap: dict) -> dict:
    """One /fleet entry from a registry snapshot.

    snap keys: reg, table, aged, enrolled_at, last_enroll_at, first_good_at, last_good_at,
    poll_rtt_ms, connected, poll_error."""
    reg = snap["reg"]
    rows = tag_rows(snap["table"], snap["aged"])
    return {
        "name": reg["name"], "profile": reg["profile"], "protocol": reg["protocol"],
        "task": reg["task"], "cell": reg["cell"],
        "enrolled_at": snap["enrolled_at"], "first_good_at": snap["first_good_at"],
        "last_good_at": snap["last_good_at"], "poll_rtt_ms": snap["poll_rtt_ms"],
        "connected": snap["connected"], "tags": rows,
        "tags_good": sum(1 for r in rows if r["quality"] == "GOOD"),
        "last_enroll_at": snap["last_enroll_at"], "poll_error": snap["poll_error"],
        "runtime": reg["runtime"],
    }


def fleet_json(snaps: list[dict]) -> list[dict]:
    return [plc_json(s) for s in sorted(snaps, key=lambda s: s["reg"]["name"])]


def domains(regs: list[dict]) -> dict:
    """{"domains": {"plc:<name>": [machines..., "<name>"]}} for the engine (FLEET.md 9)."""
    return {"domains": {f"plc:{r['name']}": [*r["cell"]["machines"], r["name"]]
                        for r in sorted(regs, key=lambda r: r["name"])}}


def _fmt(v) -> str:
    return str(v) if isinstance(v, int) else f"{v:.4f}"


def prom_text(snaps: list[dict]) -> str:
    """The /metrics/fleet exposition. Labels: namespace="fleet", pod="<PLC name>".

    vplc_scan_time_ms, vplc_state, and vplc_overruns_total come from the system words and
    leave the text when their tag is BAD (a gap, not a lie). scada_poll_rtt_ms is the last
    measured poll and leaves the text while the PLC is disconnected. scada_plc_connected and
    scada_tags_good are always present."""
    lines: list[str] = []
    for s in sorted(snaps, key=lambda s: s["reg"]["name"]):
        name = s["reg"]["name"]
        lab = f'{{namespace="{NS}",pod="{name}"}}'
        aged = s["aged"]

        def sys_rec(signal):
            rec = aged.get(tag_name(name, "SYS", signal))
            return rec if rec and rec.get("quality") != "BAD" else None

        for metric, signal, as_int in (("vplc_scan_time_ms", "SCAN_MS", False),
                                       ("vplc_state", "STATE", True),
                                       ("vplc_overruns_total", "OVERRUNS", True)):
            rec = sys_rec(signal)
            if rec is not None:
                v = rec["value"]
                lines.append(f"{metric}{lab} {_fmt(int(v) if as_int else float(v))}")
        if s["connected"] and s["poll_rtt_ms"] is not None:
            lines.append(f"scada_poll_rtt_ms{lab} {_fmt(float(s['poll_rtt_ms']))}")
        lines.append(f"scada_plc_connected{lab} {1 if s['connected'] else 0}")
        good = sum(1 for r in s["table"] if (aged.get(r["tag"]) or {}).get("quality") == "GOOD")
        lines.append(f"scada_tags_good{lab} {good}")
    return "\n".join(lines) + "\n" if lines else ""
