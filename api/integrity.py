"""Integrity checks (SCENARIOS.md 5.2). Pure functions, no HTTP, unit-testable like fleet.py.

Two checks compare what the SCADA view reports with what the plant does:

- Unsigned setpoint change (PS4A). A writable PLC setpoint changed, and no write intent, no signed
  ledger row, and no PLC restart or task load explains the change.
- Current balance (PS4B). The controller-reported currents on a rail do not add up to the
  independent feeder meter of that rail.

main.py owns the I/O. It reads tag-server /fleet and /tags and plant-sim /state, calls these
functions once per pass, and appends the ledger rows that they return. The state is a plain dict,
so a test can drive the passes by hand. These are invariant checks, not causal inference.
"""
from __future__ import annotations

GRACE_S = 35.0          # above the 30 s vPLC heartbeat, so the runtime of a restarted PLC lands first
PRE_S = 60.0            # an intent or a ledger row can come up to 60 s before the change
POST_S = 15.0           # or up to 15 s after it (the API appends its row after the write ack)
BALANCE_PASSES = 3
MIN_GAP_A = 2.0
GAP_FRAC = 0.03
INTENT_TTL_S = 600.0
# The value that a PLC restart or a task load writes. A signal with no entry here has no known
# default, so a restart or a load explains any value of it.
TASK_DEFAULTS = {"DERATE_PCT": 100.0, "CELL_ENABLE": 1.0}
SIGNED = {"execute": "executed", "restore": "restored"}     # verb -> the status of a signed write
REINIT_VERBS = ("load-task", "run", "fleet-create")
UNSIGNED_EV = ("plc", "asset", "tag", "address", "from", "to", "observed_at", "reason", "clients")
BALANCE_EV = ("rail", "feeder_amps", "reported_amps", "gap_amps", "channel")


def new_state() -> dict:
    return {"setpoints": {}, "plcs": {}, "rails": {}, "seeded": False, "seed_unsigned": {},
            "checked_at": None, "last_ok": None, "blind": []}


def intent(plc: str, tag: str, to, ts: float) -> dict:
    """What the API is about to write. main.py records one just before each SCADA write."""
    return {"plc": plc, "tag": tag, "to": to, "ts": ts}


def prune_intents(intents: list, now: float, ttl: float = INTENT_TTL_S) -> list:
    return [i for i in intents if now - i["ts"] <= ttl]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _good(row) -> bool:
    return bool(row) and row.get("quality") == "GOOD" and _num(row.get("value"))


def _tol(row) -> float:
    scale = row.get("scale")
    return 0.5 / scale if _num(scale) and scale > 0 else 0.5


def _same(a, b, tol: float) -> bool:
    return _num(a) and _num(b) and abs(float(a) - float(b)) <= tol


def _bad_status(status) -> bool:
    s = str(status or "")
    return s == "denied" or s.startswith(("error", "refused"))


class _Lazy:
    """The ledger rows, read once and only when a check needs them. `src` is a list or a callable."""

    def __init__(self, src):
        self.src, self.rows = src, None

    def get(self) -> list:
        if self.rows is None:
            self.rows = list((self.src() if callable(self.src) else self.src) or [])
        return self.rows


# ------------------------------------------------------ unsigned setpoints --
def _write_reason(plc, tag, value, t, tol, intents, ledger) -> str | None:
    """A write intent or a signed execute/restore row for this value, near the change time t."""
    lo, hi = t - PRE_S, t + POST_S
    for i in intents or []:
        if i["plc"] == plc and i["tag"] == tag and _same(i["to"], value, tol) and lo <= i["ts"] <= hi:
            return "write intent"
    for r in ledger.get():
        ev = r.get("evidence") or {}
        if (SIGNED.get(r.get("verb")) == r.get("status") and ev.get("plc") == plc and ev.get("tag") == tag
                and _same(ev.get("to"), value, tol) and lo <= (r.get("ts") or 0) <= hi):
            return f"ledger {r['verb']}"
    return None


def _reinit_reason(plc, signal, value, t, tol, plc_state, ledger) -> str | None:
    """A reset to the task default, together with a restart, a new registration, or a load row."""
    default = TASK_DEFAULTS.get(signal)
    if default is not None and not _same(value, default, tol):
        return None
    # Restarts and registrations are seen by polling, so they can land up to GRACE_S late.
    for ts, why in (plc_state or {}).get("events") or []:
        if t - PRE_S <= ts <= t + GRACE_S:
            return why
    for r in ledger.get():
        if (r.get("verb") in REINIT_VERBS and r.get("target") == plc and not _bad_status(r.get("status"))
                and t - PRE_S <= (r.get("ts") or 0) <= t + POST_S):
            return f"{r['verb']} row"
    return None


def _track_plc(state, entry, now):
    """Note a PLC restart (runtime.started_at) or a new registration (enrolled_at, task hash)."""
    name = entry.get("name")
    rt = entry.get("runtime") or {}
    ident = {"started_at": rt.get("started_at"), "enrolled_at": entry.get("enrolled_at"),
             "sha": (entry.get("task") or {}).get("sha256")}
    p = state["plcs"].get(name)
    if p is None:
        state["plcs"][name] = {"ident": ident, "events": []}
        return
    old = p["ident"]
    if ident["started_at"] is not None and ident["started_at"] != old["started_at"]:
        p["events"].append((now, "plc restart"))
    elif ident["enrolled_at"] != old["enrolled_at"] or ident["sha"] != old["sha"]:
        p["events"].append((now, "new registration"))
    p["ident"] = ident
    p["events"] = [e for e in p["events"] if now - e[0] <= PRE_S + GRACE_S]


def _out_of_range(row, value) -> bool:
    lo, hi = row.get("min"), row.get("max")
    return (_num(lo) and value < lo) or (_num(hi) and value > hi)


def _unsigned_event(f, status, **over) -> dict:
    ev = {k: f.get(k) for k in UNSIGNED_EV}
    ev.update(over)
    return {"verb": "unsigned", "target": f["tag"], "status": status, "evidence": ev}


def _open_unsigned(sp, plc, row, value, reason, now) -> dict:
    f = {"id": f"unsigned_write/{plc}/{row['tag']}", "kind": "unsigned_write", "status": "open",
         "plc": plc, "asset": row.get("asset"), "tag": row["tag"], "address": row.get("address"),
         "from": sp["accepted"], "to": value, "observed_at": sp["since"] or now, "reason": reason,
         "clients": [], "opened_at": now}
    sp["finding"], sp["since"] = f, None
    return _unsigned_event(f, "detected")


def _close_unsigned(sp, value, reason, now) -> dict:
    f = sp["finding"]
    sp["finding"], sp["since"], sp["accepted"] = None, None, value
    return _unsigned_event(f, "cleared", **{"from": f["to"], "to": value, "observed_at": now, "reason": reason})


def _check_setpoint(state, plc, row, intents, ledger, now, grace_s) -> dict | None:
    key = f"{plc}|{row['tag']}"
    value, tol = float(row["value"]), _tol(row)
    sp = state["setpoints"].get(key)
    if sp is None:
        # The first sighting is a baseline. After an API restart the ledger can still hold an open
        # finding for this value: take it back without a second row.
        sp = state["setpoints"][key] = {"accepted": value, "last": value, "changed_at": now,
                                        "since": None, "finding": None}
        seed = state["seed_unsigned"].pop(key, None)
        if seed and _same(seed["evidence"].get("to"), value, tol):
            ev = seed["evidence"]
            sp["accepted"] = ev.get("from") if _num(ev.get("from")) else value
            sp["finding"] = {"id": f"unsigned_write/{plc}/{row['tag']}", "kind": "unsigned_write",
                             "status": "open", **{k: ev.get(k) for k in UNSIGNED_EV},
                             "to": value, "opened_at": seed.get("ts") or now}
            return None
        if _out_of_range(row, value):
            return _open_unsigned(sp, plc, row, value, "out of range", now)
        return None
    if not _same(value, sp["last"], tol):
        sp["last"], sp["changed_at"] = value, now
    signal = row.get("signal")

    def explained():
        return (_write_reason(plc, row["tag"], value, sp["changed_at"], tol, intents, ledger)
                or _reinit_reason(plc, signal, value, sp["changed_at"], tol, state["plcs"].get(plc), ledger))

    f = sp["finding"]
    if f is not None:
        # Only a recent change can have an explanation, so a held value does not read the ledger.
        why = explained() if now - sp["changed_at"] <= grace_s else None
        if why:
            return _close_unsigned(sp, value, why, now)
        if _same(value, sp["accepted"], tol):
            return _close_unsigned(sp, value, "returned to the signed value", now)
        f["to"] = value
        return None
    if _same(value, sp["accepted"], tol):
        sp["since"] = None                      # a suspect change went back before the grace ran out
        return None
    if sp["since"] is None:
        sp["since"] = sp["changed_at"]
    why = explained()
    if why:
        sp["accepted"], sp["since"] = value, None
        return None
    if _out_of_range(row, value):
        return _open_unsigned(sp, plc, row, value, "out of range", now)
    if now - sp["since"] >= grace_s:
        return _open_unsigned(sp, plc, row, value, "no ledger row", now)
    return None


def seed(state, rows):
    """After an API restart, take back the findings that the ledger still shows as open."""
    last_u, last_b = {}, {}
    for r in rows or []:
        ev = r.get("evidence") or {}
        if r.get("actor") != "visr":
            continue
        if r.get("verb") == "unsigned" and ev.get("plc") and ev.get("tag"):
            last_u[f"{ev['plc']}|{ev['tag']}"] = r
        elif r.get("verb") == "balance" and ev.get("rail"):
            last_b[ev["rail"]] = r
    state["seed_unsigned"] = {k: r for k, r in last_u.items() if r.get("status") == "detected"}
    for rail, r in last_b.items():
        if r.get("status") == "mismatch" and rail not in state["rails"]:
            ev = r["evidence"]
            state["rails"][rail] = {"over": BALANCE_PASSES, "under": 0, "finding": {
                "id": f"current_balance/{rail}", "kind": "current_balance", "status": "open",
                **{k: ev.get(k) for k in BALANCE_EV}, "plc": ev.get("plc"), "asset": ev.get("asset"),
                "opened_at": r.get("ts")}}
    state["seeded"] = True


def reconcile(state, scada_fleet, intents, ledger, now, grace_s: float = GRACE_S) -> list[dict]:
    """One pass of the unsigned-setpoint check over tag-server /fleet. Returns the ledger rows to
    append, as {verb, target, status, evidence}. `ledger` is a list or a callable that reads it.
    Only writable rows with quality GOOD count. A STALE or BAD row neither moves nor flags."""
    led = _Lazy(ledger)
    if not state["seeded"]:
        seed(state, led.get())
    events = []
    for entry in scada_fleet or []:
        plc = entry.get("name")
        if not plc:
            continue
        _track_plc(state, entry, now)
        for row in entry.get("tags") or []:
            if row.get("writable") and row.get("tag") and _good(row):
                ev = _check_setpoint(state, plc, row, intents, led, now, grace_s)
                if ev:
                    events.append(ev)
    return events


def set_clients(state, event, clients):
    """Add the Caretta clients to a detected row and to its open finding."""
    event["evidence"]["clients"] = list(clients)
    ev = event["evidence"]
    sp = state["setpoints"].get(f"{ev['plc']}|{ev['tag']}") or {}
    if sp.get("finding"):
        sp["finding"]["clients"] = list(clients)


def caretta_clients(result, plc: str, port: str = "102", exclude=("tag-server",)) -> list[str]:
    """Workloads that Caretta saw talk to the PLC on its protocol port, other than the tag server."""
    out = set()
    for s in result or []:
        m = s.get("metric") or {}
        c = m.get("client_name")
        if m.get("server_name") == plc and str(m.get("server_port")) == str(port) and c \
                and c not in exclude and c != plc:
            out.add(c)
    return sorted(out)


# --------------------------------------------------------- current balance --
def _controller_amps(asset, scada_fleet):
    """(PLC name, AMPS row) of the controller channel of an asset, or None."""
    for entry in scada_fleet or []:
        if asset in ((entry.get("cell") or {}).get("machines") or []):
            row = next((t for t in entry.get("tags") or []
                        if t.get("asset") == asset and t.get("signal") == "AMPS"), None)
            if row is not None:
                return entry.get("name"), row
    return None


def rail_reading(rail, feeder, devices, scada_fleet, base) -> dict | None:
    """feeder minus the reported currents of every device on the rail. A cell machine reports
    through its controller channel, every other device through its base PLANT.<ASSET>.AMPS tag.
    None when a device has no channel or its channel is not GOOD."""
    thr = max(MIN_GAP_A, GAP_FRAC * feeder)
    reported, channel, worst = 0.0, None, thr
    for asset, dev in sorted((devices or {}).items()):
        if (dev or {}).get("rail") != rail:
            continue
        ctrl = _controller_amps(asset, scada_fleet)
        b = base.get(asset)
        use = ctrl[1] if ctrl else b
        if not _good(use):
            return None
        reported += float(use["value"])
        if ctrl and _good(b):
            # Both channels of one machine disagree: name the controller channel.
            d = abs(float(ctrl[1]["value"]) - float(b["value"]))
            if d > worst:
                worst, channel = d, {"channel": ctrl[1]["tag"], "plc": ctrl[0], "asset": asset}
    return {"feeder": feeder, "reported": reported, "gap": feeder - reported, "thr": thr,
            "named": channel}


def _balance_event(f, status) -> dict:
    return {"verb": "balance", "target": f["rail"], "status": status,
            "evidence": {k: f.get(k) for k in BALANCE_EV}}


def balance(state, plant, scada_fleet, base_tags, now, passes: int = BALANCE_PASSES) -> list[dict]:
    """One pass of the current-balance check. A finding opens after `passes` passes in a row with
    |gap| above max(2 A, 3 % of feeder), and closes after `passes` passes back inside it."""
    events = []
    devices = (plant or {}).get("devices") or {}
    base = {t.get("asset"): t for t in base_tags or []
            if t.get("signal") == "AMPS" and t.get("kind", "measured") == "measured"}
    for rail, info in sorted(((plant or {}).get("rails") or {}).items()):
        feeder = (info or {}).get("amps")
        if not _num(feeder):
            continue
        st = state["rails"].setdefault(rail, {"over": 0, "under": 0, "finding": None})
        r = rail_reading(rail, float(feeder), devices, scada_fleet, base)
        if r is None:                          # not every input is GOOD: the run of passes breaks
            st["over"] = st["under"] = 0
            continue
        over = abs(r["gap"]) > r["thr"]
        st["over"], st["under"] = (st["over"] + 1, 0) if over else (0, st["under"] + 1)
        f = st["finding"]
        vals = {"feeder_amps": round(r["feeder"], 2), "reported_amps": round(r["reported"], 2),
                "gap_amps": round(r["gap"], 2)}
        if f is None:
            if over and st["over"] >= passes:
                named = r["named"] or {}
                f = st["finding"] = {"id": f"current_balance/{rail}", "kind": "current_balance",
                                     "status": "open", "rail": rail, **vals,
                                     "channel": named.get("channel"), "plc": named.get("plc"),
                                     "asset": named.get("asset"), "opened_at": now}
                events.append(_balance_event(f, "mismatch"))
            continue
        f.update(vals)
        if r["named"] and not f.get("channel"):
            f.update(r["named"])
        if not over and st["under"] >= passes:
            st["finding"] = None
            events.append(_balance_event(f, "cleared"))
    return events


# ---------------------------------------------------------------- exposure --
def mark(state, now, blind):
    """End of a pass. `blind` lists the inputs that failed to read. The findings stay as they are."""
    state["checked_at"] = now
    state["blind"] = list(blind)
    if not blind:
        state["last_ok"] = now


def open_findings(state) -> list[dict]:
    out = [dict(sp["finding"]) for sp in state["setpoints"].values() if sp.get("finding")]
    out += [dict(st["finding"]) for st in state["rails"].values() if st.get("finding")]
    return sorted(out, key=lambda f: (f.get("opened_at") or 0, f["id"]))


def view(state) -> dict:
    """GET /api/integrity body. source: live, blind (the last pass could not read an input), or off
    (no pass has run)."""
    source = "off" if state["checked_at"] is None else ("blind" if state["blind"] else "live")
    return {"source": source, "checked_at": state["checked_at"], "last_ok": state["last_ok"],
            "blind": state["blind"], "findings": open_findings(state)}


def blocked(findings, scada_fleet) -> list[dict]:
    """Assets whose controller channel has an open finding. They get no act-loop proposal."""
    out, seen = [], set()
    for f in findings or []:
        if f.get("kind") == "unsigned_write":
            reason = f"unsigned write on {f.get('tag')}"
        elif f.get("kind") == "current_balance" and f.get("channel"):
            reason = f"{f['channel']} contradicts the {f.get('rail')} feeder"
        else:
            continue
        plc, asset = f.get("plc"), f.get("asset")
        entry = next((e for e in scada_fleet or [] if e.get("name") == plc), None)
        machines = ((entry or {}).get("cell") or {}).get("machines") or []
        # A cell-level setpoint (CELL_ENABLE) holds every machine of that PLC.
        for a in ([asset] if asset in machines or not machines else machines):
            if a and a not in seen:
                seen.add(a)
                out.append({"asset": a, "plc": plc, "reason": reason})
    return out


def signed(item, rows, findings) -> bool:
    """True when the latest signed execute/restore row for this tag wrote the value it holds now."""
    for f in findings or []:
        if f.get("kind") == "unsigned_write" and f.get("plc") == item.get("plc") and f.get("tag") == item.get("tag"):
            return False
    last = None
    for r in rows or []:
        ev = r.get("evidence") or {}
        if SIGNED.get(r.get("verb")) == r.get("status") and ev.get("plc") == item.get("plc") \
                and ev.get("tag") == item.get("tag"):
            last = ev
    return last is not None and _same(last.get("to"), item.get("value"), 0.5)
