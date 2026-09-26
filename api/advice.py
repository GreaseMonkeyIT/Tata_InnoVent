"""Suggestions for every incident (LOG-093): a small verb library and a checker.

The verbs: derate, restore, stop, isolate, restart, inspect, hold. A suggestion is EXECUTABLE only
when a checked path exists: a derate proposal from fleet.proposals (controllable, GOOD tag at 100,
not tripped, no integrity block), or a Restore of an unsigned hold. Everything else is ADVISORY: the
console shows it, and a person does it. No suggestion acts on a tripped machine, and none overrides
a PLC trip. Pure: no I/O.
"""
from __future__ import annotations


def _s(verb, target, text, why, executable=False, **extra):
    return {"verb": verb, "target": target, "text": text, "why": why, "executable": executable, **extra}


def suggest(inc: dict | None, proposals: list, active: list, blocked: list, graph: dict | None = None) -> list[dict]:
    out, seen = [], set()

    def add(s):
        key = (s["verb"], s["target"])
        if key not in seen:
            seen.add(key)
            out.append(s)

    for p in proposals or []:
        why = (f"{p['asset']} is the root and draws more current than normal" if p.get("reason") == "root"
               else f"{p['asset']} heads for its trip in about {(p.get('cites') or {}).get('eta_s') or 0:.0f} s")
        add(_s("derate", p["asset"], f"derate {p['asset']} to {p['to']} % through {p['plc']}", why,
               executable=True, proposal_id=p["id"]))
    for a in active or []:
        if a.get("signed") is False:
            add(_s("restore", a["asset"], f"restore {a['asset']} to 100 %: a write with no signed record set it "
                   f"to {a['value']:.0f} %", "the setpoint changed with no signed ledger row", executable=True))
    for b in blocked or []:
        add(_s("hold", b["asset"], f"make no write through {b['plc']} until someone checks it",
               b.get("reason") or "an integrity finding is open on the channel"))
    if not inc:
        return out

    tripped = set(inc.get("tripped") or [])
    for f in inc.get("integrity") or []:
        if f.get("kind") == "unsigned_write" and f.get("clients"):
            for c in f["clients"]:
                add(_s("isolate", c, f"block {c} from the PLC network", "it wrote a setpoint with no signed record"))
        if f.get("kind") == "current_balance":
            ch = ".".join(str(f.get("channel") or "").split(".")[-2:]) or "the PLC report"
            add(_s("inspect", f.get("channel") or f.get("rail"),
                   f"trust the {f.get('rail')} feeder meter over {ch}, and inspect the machine and its PLC",
                   f"the PLC report contradicts the feeder by {f.get('gap_amps') or 0:.1f} A"))

    origin = (inc.get("origin") or {})
    driver = (inc.get("driver") or {})
    # LOG-099: once the origin's drive is normal (recovering) there is nothing to stop or isolate. The
    # watch of 2026-09-26 still said "unload or stop press-1" after the Scenario 4B reset.
    for role in (() if inc.get("status") == "recovering" else (origin, driver)):
        f = role.get("facts") or {}
        a, k = role.get("asset"), f.get("kind")
        if not a or a in tripped and k == "machine":
            continue
        if k == "machine" and not any(p["asset"] == a for p in proposals or []):
            add(_s("stop", a, f"unload or stop {a} at the machine", "no controller VISR can write to holds it"))
        elif k == "cooling":
            sup, sp = f.get("t_supply"), f.get("t_setpoint")
            warm = sup is not None and sp is not None and sup > sp + 1.0
            if f.get("chiller_tripped") and origin.get("asset") and origin["asset"] != a:
                add(_s("inspect", a, f"reset the {a} relay only after {origin['asset']} is fixed",
                       f"{origin['asset']} overloaded it, and a reset now trips it again"))
            elif f.get("undervoltage"):                      # LOG-100: PS7
                add(_s("inspect", a, f"{a} restarts by itself when the supply is back, after its anti-recycle wait",
                       "its undervoltage protection stopped it"))
            elif warm and origin.get("asset") and origin["asset"] != a:
                add(_s("inspect", a, f"remove the extra heat at {origin['asset']} first, then bring a standby "
                       f"chiller online if there is one",
                       f"{a} is at its capacity limit and the supply water is {sup:.0f} C"))
            elif not f.get("chiller_tripped"):
                add(_s("inspect", a, f"check the {a} pump, and switch to a standby pump if there is one",
                       f"loop flow is {f.get('flow') or 0:.0f} of {f.get('flow_nominal') or 0:.0f} L/min"))
        elif k == "network":
            add(_s("isolate", a, f"rate-limit or isolate {a} on {f.get('segment')}",
                   "its traffic saturates the segment the stamping PLC uses"))
            if active:
                add(_s("inspect", "derates", "check the derates in force: a flapping link can undo them",
                       "the PLC link drops in and out"))
        elif k == "supply":
            add(_s("inspect", a, f"check the incoming supply at {a}", "every rail sags together"))
        elif k == "workload":
            add(_s("inspect", a, f"check the {a} pod on the edge node", "its CPU, memory, or disk pressure leads the verdict"))

    for c in inc.get("cards") or []:
        pod = c.get("pod")
        if c.get("class") == "leak":
            add(_s("restart", pod, f"restart {pod} in a planned way before it reaches its limit, and keep a memory "
                   "profile first", f"it reaches its memory limit in about {c.get('eta_s') or 0:.0f} s"))
        elif c.get("class") == "trip" and pod not in tripped and not any(p["asset"] == pod for p in proposals or []):
            add(_s("stop", pod, f"reduce the load on {pod} before it reaches {c.get('limit') or 80:.0f} °C",
                   f"it trips in about {c.get('eta_s') or 0:.0f} s, and no controller VISR can write to holds it"))
    for m in sorted(tripped):
        add(_s("inspect", m, f"keep {m} stopped until the cause is fixed, then check it before a restart",
               f"{m} tripped"))
    if inc.get("blind"):
        add(_s("inspect", "scada", "watch the plant from the edge view until the tag server answers",
               "SCADA is blind"))
    return out
