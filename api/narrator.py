"""The narrator (LOG-093): the incident in plain words, in a fixed order, locked to the incident tag.

The engine decides. The narrator only words it. It writes from a case file built here from the
incident record (api/incident.py) and the suggestions (api/advice.py), never from the raw graph.

- Fixed sections in a fixed order: headline, origin, driver, chain, actions, evidence, forecast,
  suggestion. An empty section is left out. Every phase has a headline (LOG-099). The template gives every section a plain sentence, so the text is
  complete without a model.
- The model rewrites the case file as natural prose, one short paragraph per section, in the style of
  STYLE and the examples. Temperature 0 and a fixed seed.
- A checker rejects a section whose numbers are not in the case file (a model must not invent a
  reading), and strips markup. A rejected section keeps its template sentence.
- Tag and lock: the text belongs to one (incident id, tag). main.py writes a new text only when the
  tag changes, so the words stay still while the live values move in their own places on the console.
Pure except for `llm`, the callable main.py passes in.
"""
from __future__ import annotations

import json
import re

import incident

SECTIONS = ("headline", "origin", "driver", "chain", "actions", "evidence", "forecast", "suggestion")

EVIDENCE_WORDS = {"write": "its own load moved first", "rail": "they share a power rail",
                  "loop": "they share the coolant loop", "net": "they share the field network segment",
                  "temporal": "the order in time agrees", "common_mode": "every member of the medium moved together",
                  "stat": "the readings move together", "forecast": "a fitted temperature curve"}

STYLE = """You write for a plant operator on a control-room console.
- Plain words. Short sentences. Active voice. No jargon: never say contention, edge, blast radius,
  evidence types, root-cause ranking, or ETA.
- Name machines exactly as written (press-1, chiller-1, psu-a).
- Use only the numbers in the case file. Never add a number, a unit, or a cause that is not there.
- No markdown, no code, no symbols such as $, *, #, or backticks.
- Keep the order and the meaning of each section. One or two sentences per section.
- Continue the story: when the previous text exists, keep what is still true and change only what the
  new phase changed."""

EXAMPLE_IN = {"headline": "press-1 started an incident on rail psu-a.",
              "origin": "press-1 draws 80 A, 90 % above its normal 42 A, and rail psu-a is at 382 V.",
              "chain": "press-1, then rail psu-a, then press-2, cnc-1 and qa-scanner-1.",
              "evidence": "its own load moved first, and they share a power rail.",
              "forecast": "press-1 heads for 88 °C and trips at 80 °C in about 170 s.",
              "suggestion": "derate press-1 to 55 % through plc-stamping."}
EXAMPLE_OUT = {"headline": "press-1 is overloading rail psu-a.",
               "origin": "press-1 draws 80 A, almost twice its normal 42 A, and pulls the rail down to 382 V.",
               "chain": "The low rail reaches press-2, cnc-1 and qa-scanner-1.",
               "evidence": "press-1's current rose before the rail fell, and all of them share that rail.",
               "forecast": "press-1 is also heating up: it heads for 88 °C and would trip at 80 °C in about 170 s.",
               "suggestion": "Derate press-1 to 55 % through plc-stamping to take the load off the rail."}


def _short(tag):
    return ".".join(str(tag or "").split(".")[-2:])


def _quiet_headline(inc: dict) -> str:
    """The headline of an incident whose first sign is gone, from its phases (LOG-099: Scenario 4A had
    an empty text after the reset)."""
    kinds = {p.get("kind"): p for p in inc.get("phases") or []}
    if "restart" in kinds and "card" in kinds:
        a = (kinds["restart"].get("facts") or {}).get("asset") or "the tag server"
        return f"{a} reached its memory limit and restarted. SCADA answers again."
    if "restart" in kinds:
        return "The tag server restarted. SCADA answers again."
    if "integrity" in kinds:
        f = kinds["integrity"].get("facts") or {}
        return ("The setpoint change with no signed record is cleared." if f.get("kind") == "unsigned_write"
                else "The PLC report agrees with the physics again.")
    if "card" in kinds:
        f = kinds["card"].get("facts") or {}
        return (f"The forecast for {f.get('asset')} no longer shows a trip." if f.get("cls") == "trip"
                else f"{f.get('asset')} is back under its memory limit.")
    if "blind" in kinds:
        return "SCADA answers again."
    return "The first sign of this incident has cleared."


def case_file(inc: dict | None, suggestions: list) -> dict:
    """The facts, as one template sentence per section. The model may reword them, never add to them."""
    if not inc:
        return {}
    cf = {}
    origin, driver = inc.get("origin") or {}, inc.get("driver") or {}
    integ, cards, tripped = inc.get("integrity") or [], inc.get("cards") or [], inc.get("tripped") or []
    recovering = inc.get("status") == "recovering"
    if origin.get("asset"):
        where = (origin.get("facts") or {}).get("rail")
        cf["headline"] = (f"{origin['asset']} started an incident" + (f" on rail {where}" if where else "") + "."
                          + (" The plant recovers." if recovering else ""))
        # LOG-099: the origin's readings are from the start. Once it tripped, another machine drives, or
        # the plant recovers, they are history, so the words go to the past tense.
        past = recovering or origin["asset"] in tripped or driver.get("asset") not in (None, origin["asset"])
        if past and origin.get("facts"):
            cf["origin"] = "At the start, " + incident.reason_line(origin["facts"], past=True).rstrip(".") + "."
        else:
            cf["origin"] = origin.get("reason", "").rstrip(".") + "."
    elif integ:
        f = integ[0]
        cf["headline"] = ("A setpoint changed with no signed record." if f.get("kind") == "unsigned_write"
                          else "A PLC report contradicts the physics.")
    elif cards:
        c = cards[0]
        cf["headline"] = (f"{c['pod']} is heading for its trip." if c.get("class") == "trip"
                          else f"{c['pod']} is running out of memory.")
    elif inc.get("blind"):
        cf["headline"] = "SCADA is blind."
    else:
        cf["headline"] = _quiet_headline(inc)
    if driver.get("asset") and driver.get("asset") != origin.get("asset"):
        if recovering and driver.get("was"):             # LOG-099: the B6 watch still said "loop flow is 71"
            cf["driver"] = f"After that, {driver['asset']} drove it: {driver['was'].rstrip('.')}."
        else:
            cf["driver"] = f"{driver['asset']} drives it now: {driver.get('reason', '').rstrip('.')}."
    chain = list(inc.get("chain") or [])
    victims = [v for v in inc.get("victims") or [] if v not in tripped and v not in chain]
    parts = []
    if len(chain) > 1:
        parts.append("The chain runs " + ", then ".join(chain) + ".")
    if victims:
        parts.append(("It reached " if recovering else "It reaches ") + ", ".join(victims) + ".")
    if tripped:
        parts.append("Tripped so far: " + ", ".join(tripped) + ".")
    if parts:
        cf["chain"] = " ".join(parts)
    done = [p["text"] for p in inc.get("phases") or [] if p.get("kind") in ("action", "relief")]
    if done:
        cf["actions"] = "Done so far: " + "; ".join(done[-3:]) + "."
    ev = []
    for f in integ:
        if f.get("kind") == "unsigned_write":
            who = ", ".join(f.get("clients") or []) or "an unknown client"
            ev.append(f"{_short(f.get('tag'))} changed with no signed ledger row, and the network shows {who} wrote it")
        elif f.get("kind") == "current_balance" and f.get("gap_amps") is not None:
            if f["gap_amps"] >= 1.0:
                ev.append(f"the {f.get('rail')} feeder measures {f['gap_amps']:.1f} A more than "
                          f"{_short(f.get('channel'))} reports")
            else:
                ev.append(f"the {f.get('rail')} feeder and {_short(f.get('channel'))} agree again")
    words = (driver.get("evidence") or origin.get("evidence") or [])
    if words:
        ev.append(", and ".join(EVIDENCE_WORDS[w] for w in words if w in EVIDENCE_WORDS))
    if inc.get("blind"):
        ev.append("the tag server does not answer, so the SCADA view is blind")
    if ev:
        cf["evidence"] = "Evidence: " + "; ".join(ev) + "."
    fc = []
    for c in cards[:3]:
        if c.get("class") == "trip":
            head = f"heads for {c['t_inf']:.0f} °C and " if c.get("t_inf") else ""
            tail = (f" at the present rise of {c['rate_c_min']:.2f} °C per minute"      # LOG-100: a drift card
                    if c.get("model") == "drift" and c.get("rate_c_min") else "")
            fc.append(f"{c['pod']} {head}trips at {c.get('limit') or 80:.0f} °C in about {c.get('eta_s') or 0:.0f} s{tail}")
        else:
            fc.append(f"{c['pod']} reaches its memory limit in about {c.get('eta_s') or 0:.0f} s")
    if fc:
        cf["forecast"] = "Forecast: " + "; ".join(fc) + "."
    if suggestions:
        cf["suggestion"] = "Suggested: " + "; ".join(s["text"] for s in suggestions[:3]) + "."
    return cf


_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_MARKUP = re.compile(r"[`*#$~]|\\\(|\\\)|\\\[|\\\]")


def clean(text: str) -> str:
    t = _MARKUP.sub("", str(text or ""))
    return re.sub(r"\s+", " ", t).strip()


def numbers_ok(text: str, allowed: set) -> bool:
    """Every number in `text` must be a number of the case file, allowing rounding (1 unit or 2 %).
    'about twice' is fine; '87 A' where the file says 85 A is not."""
    for m in _NUM.findall(text):
        v = float(m)
        if not any(abs(v - a) <= max(1.0, 0.02 * abs(a)) for a in allowed):
            return False
    return True


def allowed_numbers(cf: dict) -> set:
    return {float(m) for s in cf.values() for m in _NUM.findall(s)}


def render(cf: dict, sections: dict | None = None) -> str:
    sections = sections or cf
    return " ".join(sections[k].strip() for k in SECTIONS if sections.get(k))


def prompt(cf: dict, previous: dict | None) -> str:
    return (STYLE + "\n\nAnswer with one JSON object with exactly these keys: "
            + ", ".join(k for k in SECTIONS if k in cf) + ".\n\n"
            + "EXAMPLE CASE FILE:\n" + json.dumps(EXAMPLE_IN) + "\nEXAMPLE ANSWER:\n" + json.dumps(EXAMPLE_OUT)
            + ("\n\nPREVIOUS TEXT:\n" + json.dumps(previous) if previous else "")
            + "\n\nCASE FILE:\n" + json.dumps(cf) + "\nANSWER:\n")


def write(cf: dict, previous: dict | None, llm) -> tuple[dict, str]:
    """(sections, source). llm(prompt) returns the model's raw answer or None. Each section the model
    got wrong keeps its template sentence. source: llm, mixed, or template."""
    if not cf:
        return {}, "template"
    raw = llm(prompt(cf, previous)) if llm else None
    try:
        got = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        got = None
    if not isinstance(got, dict):
        return dict(cf), "template"
    allowed = allowed_numbers(cf)
    out, used = {}, 0
    for k in SECTIONS:
        if k not in cf:
            continue
        v = clean(got.get(k) or "")
        # LOG-099: the model dropped the period after the headline, so render() joined two sections
        # into one run-on sentence. Every section ends with a stop.
        if v and v[-1] not in ".!?":
            v += "."
        if v and numbers_ok(v, allowed) and len(v) <= 400:
            out[k], used = v, used + 1
        else:
            out[k] = cf[k]
    return out, ("llm" if used == len(out) else "mixed" if used else "template")
