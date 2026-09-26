"""The incident record (LOG-092): one event from the first sign to the calm after it.

The engine gives a verdict every 10 s, and each verdict stands alone. An operator needs the story:
what started it (the origin), what drives it now (the current driver), why the driver changed, what
tripped, what the forecast says, what was done, and when it ended. This module keeps that story.

Rules, all from measured data:
- An incident opens on any sign: a root cause, a forecast card, an integrity finding, a tripped
  machine, or a blind SCADA view. It stays open while any of them remains.
- The current driver is the engine's top root, held for HOLD_S before it replaces the last one, so a
  flicker between two suspects does not rewrite the story. A machine that a protective trip stopped
  is a consequence and never becomes the driver. The first driver is the origin, for the whole incident.
- Every change is a phase: open, a new driver, a trip, a new forecast card, an integrity finding, the
  blind SCADA view, an operator action, a measured relief, recovery, close. The phase number is the tag
  that the narrator locks on (a new text only on a new tag).
- Recovering: the origin's own drive is back to normal (its current, the loop flow, the network load,
  the supply). The incident closes after CALM_S with no physical sign. The engine's verdict can trail
  a reset by minutes while temperatures recover, and that tail does not hold the incident open.

Pure: no I/O. main.py feeds one snapshot per step from a background thread.
"""
from __future__ import annotations

import time

HOLD_S = 15.0           # a new driver must hold this long
CALM_S = 30.0           # no physical sign for this long closes the incident
EXCESS_FRAC = 0.15      # a machine draws "above normal" 15 % over its median current
MAX_PHASES = 40
KEEP_CLOSED = 5
SCADA_POD = "tag-server"   # the blind state is about this pod
RESTART_QUIET_S = 60.0     # after SCADA answers again, a leak card on SCADA_POD is from the killed process


def _devices(plant):
    return (plant or {}).get("devices") or {}


def _tripped(plant):
    return sorted(n for n, d in _devices(plant).items() if d.get("tripped"))


def driver_facts(asset, plant, normal_amps=None) -> dict:
    """Measured facts about one driver, for the reason line and the narrator. Only numbers the plant
    reports: nothing here is inferred."""
    plant = plant or {}
    d = _devices(plant).get(asset) or {}
    loop = plant.get("loop") or {}
    facts = {"asset": asset}
    if asset == "chiller-1" or asset == loop.get("name"):
        ch = loop.get("chiller") or {}
        facts.update(kind="cooling", flow=loop.get("flow"), flow_nominal=loop.get("flow_nominal"),
                     pump_health=loop.get("pump_health"), chiller_tripped=bool(d.get("tripped")),
                     trip_reason=d.get("trip_reason"), t_supply=loop.get("t_supply"),
                     t_setpoint=loop.get("t_setpoint"), running=ch.get("running", True),
                     at_capacity=bool(ch.get("at_capacity")), undervoltage=bool(ch.get("undervoltage")))
        return facts
    for seg_name, seg in (plant.get("segments") or {}).items():
        m = (seg.get("members") or {}).get(asset)
        if m is not None:
            facts.update(kind="network", segment=seg_name, offered_fps=m.get("offered_fps"),
                         capacity_fps=seg.get("capacity_fps"), utilization=seg.get("utilization"),
                         drop_ratio=seg.get("drop_ratio"), latency_ms=seg.get("latency_ms"))
            return facts
    sup = plant.get("supply") or {}
    if asset == sup.get("name"):
        facts.update(kind="supply", volts=sup.get("volts"), nominal_volts=sup.get("nominal_volts"),
                     dipped=sup.get("dipped"))
        return facts
    if asset in (plant.get("rails") or {}):
        r = plant["rails"][asset]
        facts.update(kind="rail", volts=r.get("volts"), amps=r.get("amps"))
        return facts
    if not d:
        # LOG-098: not a plant asset. A pod of the edge node, rooted by CPU, memory, or disk pressure.
        facts.update(kind="workload")
        return facts
    rail = d.get("rail")
    facts.update(kind="machine", amps=d.get("amps"), normal_amps=(normal_amps or {}).get(asset),
                 temp=d.get("temp"), rail=rail, tripped=bool(d.get("tripped")),
                 rail_volts=((plant.get("rails") or {}).get(rail) or {}).get("volts") if rail else None)
    return facts


def reason_line(f: dict, past: bool = False) -> str:
    """One plain sentence from driver_facts. `past` words the same facts in the past tense, for an
    origin whose readings no longer hold (it tripped, another machine drives, or the plant recovers)."""
    a, k = f.get("asset"), f.get("kind")
    is_ = "was" if past else "is"
    if k == "cooling":
        nom = f.get("flow_nominal") or 0
        flow, sup, sp = f.get("flow"), f.get("t_supply"), f.get("t_setpoint")
        warm = sup is not None and sp is not None and sup > sp + 1.0
        # LOG-100: the chiller states. A flow shortfall (PS5), a capacity limit (PS2), or a stop.
        if f.get("chiller_tripped"):
            head = f"{a}'s overload relay tripped"
        elif f.get("undervoltage"):
            head = f"{a} stopped on undervoltage"
        elif f.get("running") is False:
            head = f"{a} {is_} stopped"
        elif warm and (flow is None or not nom or flow >= 0.9 * nom):
            head = f"{a} {is_} at its capacity limit"
        else:
            head = f"{a} {is_} short of cooling"
        if warm:
            head += f", and the supply water {is_} {sup:.0f} C against its {sp:.0f} C setpoint"
        elif flow is not None and nom:
            head += f", and loop flow {is_} {flow:.0f} of {nom:.0f} L/min"
        if f.get("pump_health") is not None and f["pump_health"] < 0.95:
            head += f" (pump health {f['pump_health'] * 100:.0f} %)"
        return head
    if k == "network":
        util, fps = f.get("utilization"), f.get("offered_fps")
        line = (f"{a} {'flooded' if past else 'floods'} {f.get('segment')}" if fps is None else
                f"{a} {'offered' if past else 'offers'} {fps:.0f} frames/s on {f.get('segment')}")
        return line + (f", which {'ran' if past else 'runs'} at {util * 100:.0f} % of its capacity"
                       if util is not None else "")
    if k == "supply":
        return f"the supply {a} {is_} at {f.get('volts') or 0:.0f} of {f.get('nominal_volts') or 0:.0f} V"
    if k == "rail":
        return f"rail {a} {is_} at {f.get('volts') or 0:.0f} V"
    if k == "workload":
        return (f"{a} {'led' if past else 'leads'} the verdict on the edge node: its CPU, memory, or disk "
                "pressure moved first")
    amps, normal = f.get("amps"), f.get("normal_amps")
    line = (f"{a} {'drew' if past else 'draws'} {amps:.0f} A" if amps is not None
            else f"{a} {'led' if past else 'leads'} the verdict")
    if amps is not None and normal:
        line += f", {max(0.0, amps / normal - 1) * 100:.0f} % above its normal {normal:.0f} A"
    if f.get("rail") and f.get("rail_volts") is not None:
        line += f", and rail {f['rail']} {is_} at {f['rail_volts']:.0f} V"
    return line


def drive_normal(f: dict) -> bool:
    """True when the driver's own drive is back to normal: the fault that started it is gone."""
    k = f.get("kind")
    if k == "cooling":
        nom = f.get("flow_nominal") or 0
        sup, sp = f.get("t_supply"), f.get("t_setpoint")
        supply_ok = sup is None or sp is None or sup <= sp + 1.0
        return ((not f.get("chiller_tripped")) and f.get("running") is not False and supply_ok
                and nom > 0 and (f.get("flow") or 0) >= 0.9 * nom)
    if k == "network":
        return (f.get("utilization") or 0) < 0.5
    if k == "supply":
        return not f.get("dipped")
    if k == "rail":
        return True
    if f.get("tripped"):
        return False          # a stopped machine's drive is unknown, not recovered
    amps, normal = f.get("amps"), f.get("normal_amps")
    if amps is None or not normal:
        return False
    return amps <= normal * (1 + EXCESS_FRAC)


class Tracker:
    def __init__(self, hold_s: float = HOLD_S, calm_s: float = CALM_S):
        self.hold_s, self.calm_s = hold_s, calm_s
        self.active: dict | None = None
        self.closed: list[dict] = []
        self._pending: tuple | None = None           # (asset, first seen)
        self._seq = 0
        self._scada_back = None                      # when SCADA last answered again after a blind spell
        self._scada_started = None                   # the tag server's process start, from /tags

    # ------------------------------------------------------------------ helpers --
    def _phase(self, now, kind, text, facts=None):
        inc = self.active
        inc["tag"] += 1
        inc["phases"].append({"n": inc["tag"], "ts": round(now, 1), "kind": kind, "text": text,
                              **({"facts": facts} if facts else {})})
        del inc["phases"][:-MAX_PHASES]

    def _open(self, now, why):
        self._seq += 1
        self.active = {
            "id": time.strftime("INC-%Y%m%d-%H%M%S", time.localtime(now)) + f"-{self._seq}",
            "opened_ts": round(now, 1), "closed_ts": None, "status": "active", "tag": 0,
            "origin": None, "driver": None, "chain": [], "victims": [], "tripped": [], "cards": [], "integrity": [],
            "blind": False, "phases": [], "last_sign_ts": now, "_seen_cards": set(),
            "_seen_integrity": set(), "_seen_ledger": now,
        }
        self._pending = None
        self._phase(now, "open", why)

    def _close(self, now):
        inc = self.active
        self._phase(now, "close", "the plant is calm again")
        inc["status"], inc["closed_ts"] = "closed", round(now, 1)
        self.closed.append(inc)
        del self.closed[:-KEEP_CLOSED]
        self.active = None
        self._pending = None

    # --------------------------------------------------------------------- step --
    def step(self, now: float, graph: dict, plant: dict, integrity: list, scada_blind: bool,
             ledger: list, normal_amps: dict | None = None, scada_started: float | None = None) -> dict | None:
        tripped = _tripped(plant)
        roots = [r["pod"] for r in graph.get("root") or []]
        # A root becomes the driver only when its own drive is off normal. A process machine that its
        # protective trip stopped is a consequence. A tripped chiller is not: lost cooling drives the
        # loop. A root whose drive is normal is the verdict's tail after a reset, or a duty cycle.
        def candidate(r):
            f = driver_facts(r, plant, normal_amps)
            return not (r in tripped and f.get("kind") == "machine") and not drive_normal(f)
        top = next((r for r in roots if candidate(r)), None)
        if self.active is not None and self.active["blind"] and not scada_blind:
            self._scada_back = now
        # A new start time is a restart, even when no pass saw the gap (LOG-099, B6 Scenario 6).
        restarted = (scada_started is not None and self._scada_started is not None
                     and scada_started != self._scada_started)
        if scada_started is not None:
            self._scada_started = scada_started
        if restarted:
            self._scada_back = now
        # LOG-099: after the OOM kill the engine kept the old leak card for 15 to 20 s ("limit in about
        # 18 s" on a restarted tag server). A leak card on the tag server just after it came back is stale.
        fresh = self._scada_back is None or now - self._scada_back >= RESTART_QUIET_S
        cards = [c for c in graph.get("incipient") or [] if c.get("class") in ("trip", "leak")
                 and (fresh or not (c.get("class") == "leak" and c.get("pod") == SCADA_POD))]
        integ = list(integrity or [])
        physical = bool(cards or integ or tripped or scada_blind)
        sign = physical or top is not None

        if self.active is None:
            if not sign:
                return None
            why = ("an integrity check failed" if integ else "a forecast card opened" if cards and not top
                   else "SCADA went blind" if scada_blind and not top else
                   "a machine tripped" if tripped and not top else "the engine found a root cause")
            self._open(now, why)
        inc = self.active

        # the current driver, held HOLD_S before it replaces the last one. The first driver (the origin)
        # needs no hold: an incident with no origin had an empty story for one pass (LOG-099).
        cur = (inc["driver"] or {}).get("asset")
        if top and top != cur:
            if cur is None or self._pending and self._pending[0] == top:
                if cur is None or now - self._pending[1] >= self.hold_s:
                    f = driver_facts(top, plant, normal_amps)
                    reason = reason_line(f)
                    out = [e for e in graph.get("edges") or [] if e.get("src") == top and e.get("evidence")]
                    ev = list(max(out, key=lambda e: abs(e.get("r") or 0))["evidence"]) if out else []
                    was = reason_line(f, past=True)        # LOG-099: the console's words once it is history
                    inc["driver"] = {"asset": top, "since_ts": round(now, 1), "reason": reason, "was": was,
                                     "facts": f, "evidence": ev}
                    if inc["origin"] is None:
                        inc["origin"] = {"asset": top, "ts": round(now, 1), "reason": reason, "was": was,
                                         "facts": f, "evidence": ev}
                        self._phase(now, "origin", f"{top} starts it: {reason}", f)
                    else:
                        self._phase(now, "driver", f"{top} now drives it: {reason}", f)
                    if top not in inc["chain"]:
                        inc["chain"].append(top)
                    self._pending = None
            else:
                self._pending = (top, now)
        elif top == cur:
            self._pending = None
        if inc["driver"]:                              # keep the driver's facts live
            inc["driver"]["facts"] = driver_facts(inc["driver"]["asset"], plant, normal_amps)
            if top == inc["driver"]["asset"]:          # the machines the driver reaches, per the engine
                media = set((plant or {}).get("rails") or {}) | {((plant or {}).get("loop") or {}).get("name")}
                inc["victims"] = [b["pod"] for b in graph.get("blast_radius") or []
                                  if b.get("pod") not in media and b.get("pod") not in inc["chain"]][:4]

        for m in tripped:
            if m not in inc["tripped"]:
                inc["tripped"].append(m)
                temp = (_devices(plant).get(m) or {}).get("temp")
                self._phase(now, "trip", f"{m} tripped" + (f" at {temp:.1f} °C" if temp is not None else ""),
                            {"asset": m, "temp": temp})
        for c in cards:
            key = (c.get("pod"), c.get("class"))
            if key not in inc["_seen_cards"]:
                inc["_seen_cards"].add(key)
                if c.get("class") == "trip":
                    text = (f"forecast: {c['pod']} heads for the {c.get('limit') or 80:.0f} °C trip in about "
                            f"{c.get('eta_s') or 0:.0f} s")
                else:
                    text = f"forecast: {c['pod']} reaches its memory limit in about {c.get('eta_s') or 0:.0f} s"
                self._phase(now, "card", text, {"asset": c.get("pod"), "eta_s": c.get("eta_s"),
                                                "t_inf": c.get("t_inf"), "cls": c.get("class")})
        inc["cards"] = [{k: c.get(k) for k in ("pod", "class", "eta_s", "value", "limit", "t_inf", "model", "rate_c_min")}
                        for c in cards]
        for f in integ:
            key = (f.get("kind"), f.get("tag") or f.get("channel") or f.get("rail"))
            if key not in inc["_seen_integrity"]:
                inc["_seen_integrity"].add(key)
                if f.get("kind") == "unsigned_write":
                    who = ", ".join(f.get("clients") or []) or "an unknown client"
                    text = f"integrity: {f.get('tag')} changed with no signed record, written by {who}"
                else:
                    text = (f"integrity: {f.get('channel')} reports less current than the {f.get('rail')} "
                            f"feeder measures, a gap of {f.get('gap_amps') or 0:.1f} A")
                self._phase(now, "integrity", text, {k: f.get(k) for k in ("kind", "tag", "channel", "rail",
                                                                          "gap_amps", "clients")})
        inc["integrity"] = [{k: f.get(k) for k in ("kind", "tag", "channel", "rail", "gap_amps", "clients")}
                            for f in integ]
        if scada_blind != inc["blind"]:
            inc["blind"] = scada_blind
            self._phase(now, "blind", "SCADA is blind: the tag server does not answer" if scada_blind
                        else "SCADA answers again")
        if restarted:
            self._phase(now, "restart", f"{SCADA_POD} restarted, and SCADA answers again", {"asset": SCADA_POD})
        for row in ledger or []:
            ts = row.get("ts") or 0
            if ts <= inc["_seen_ledger"]:
                continue
            inc["_seen_ledger"] = ts
            verb, ev = row.get("verb"), row.get("evidence") or {}
            if verb == "execute":
                self._phase(now, "action", f"{row.get('actor')} derated {row.get('target')} to {ev.get('to')} % "
                            f"through {ev.get('plc')}", {"asset": row.get("target"), "to": ev.get("to")})
            elif verb == "relief":
                vb, va = ev.get("volts_before"), ev.get("volts_after")
                ab, aa = ev.get("amps_before"), ev.get("amps_after")
                txt = f"measured relief on {row.get('target')}"
                if ab is not None and aa is not None:
                    txt += f": {ab:.1f} → {aa:.1f} A"
                if vb is not None and va is not None:
                    txt += f", rail {ev.get('rail')} {vb:.1f} → {va:.1f} V"
                self._phase(now, "relief", txt, {k: ev.get(k) for k in ("amps_before", "amps_after",
                                                                       "volts_before", "volts_after")})
            elif verb == "restore":
                self._phase(now, "action", f"{row.get('actor')} restored {row.get('target')} to 100 %",
                            {"asset": row.get("target")})

        # recovering, then close
        origin = inc["origin"]
        drive_ok = (origin is None) or drive_normal(driver_facts(origin["asset"], plant, normal_amps))
        if origin and drive_ok and not tripped and inc["status"] == "active":
            inc["status"] = "recovering"       # sticky: a duty cycle after a reset must not flap it
            self._phase(now, "recovering", f"{origin['asset']} is back to normal, and the plant recovers")
        if physical or (top is not None and not drive_ok):
            inc["last_sign_ts"] = now
        elif now - inc["last_sign_ts"] >= self.calm_s:
            self._close(now)
            return None
        return inc

    def view(self) -> dict:
        def clean(inc):
            return {k: v for k, v in inc.items() if not k.startswith("_")} if inc else None
        return {"active": clean(self.active),
                "recent": [{k: i[k] for k in ("id", "opened_ts", "closed_ts", "origin", "tripped")}
                           for i in reversed(self.closed)]}
