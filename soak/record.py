#!/usr/bin/env python3
"""Soak recorder + report builder for the SiliconKnights causal engine.

Three jobs, picked by argv[1]:

  append <samples.jsonl> <timeline.csv>
      Flatten ONE live snapshot into a raw JSONL line + a flat CSV row. Called once per sample tick
      by soak.sh. The API bodies come in as files (GRAPH_FILE, NARR_FILE, SCEN_FILE, PLANT_FILE,
      TAGS_FILE) or as env JSON (GRAPH_JSON, NARR_JSON, SCEN_JSON, PLANT_JSON, TAGS_JSON). The tick
      context comes in as PHASE, CYCLE, SCENARIO, FIRE_EPOCH and FIRE_OK.

  meta <rundir>
      Write meta.json at the start of a run (config snapshot).

  report <rundir> <report_template.html>
      Read samples.jsonl, score each scenario with its own rule (SPEC), and emit a self-contained
      report.html (data embedded — open it by double-click, like a `powercfg /batteryreport`). No
      reasoning here; this only summarizes what VISR already decided. Honest by construction: it
      shows the ACTUAL dominant root per scenario, so a mis-root appears as-is.
"""
import sys, os, json, time, csv

# One scoring rule per id (SCENARIOS.md section 8). A hit is one observe sample that shows the
# expected result:
#   root       the top root equals `expect`
#   integrity  an open integrity finding of kind `expect`
#   forecast   an incipient card of class `expect`
#   blind      an incipient card of class `expect` on `pod`, or /api/tags answers "unavailable"
# `channel` is the controller channel the finding must name. The report shows how often it does.
# `trip` and `segment` add the chiller-1 relay and the field-1 segment to the report row.
SPEC = {
    "PS1": {"kind": "root", "expect": "press-1",
            "note": "Rail-sag cascade: expect root press-1 along rail psu-a."},
    "PS2": {"kind": "root", "expect": "compressor-1", "trip": "chiller-1",
            "note": "Power sag trips the chiller: expect root compressor-1, then the chiller-1 overload trip "
                    "and loop cool-1."},
    "PS3": {"kind": "root", "expect": "hmi-gw", "segment": "field-1",
            "note": "Control network storm: expect root hmi-gw along segment field-1."},
    "PS4A": {"kind": "integrity", "expect": "unsigned_write",
             "channel": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT",
             "note": "Setpoint write with no record: expect an unsigned_write finding on press-1 DERATE_PCT. "
                     "The first finding waits GRACE_S (35 s)."},
    "PS4B": {"kind": "integrity", "expect": "current_balance",
             "channel": "FLEET.PLC_STAMPING.PRESS_1.AMPS",
             "note": "Current report contradicts the feeder: expect a current_balance finding on rail psu-a "
                     "that names press-1 AMPS."},
    "PS5": {"kind": "forecast", "expect": "trip",
            "note": "Coolant pump degradation: success = the trip forecast card fires before the 78 °C trip."},
    "PS6": {"kind": "blind", "expect": "leak", "pod": "tag-server",
            "note": "The monitor runs out of memory: expect a leak card on tag-server, then the SCADA view "
                    "blind after the kill."},
}
QUIET = ("baseline", "cooldown")
CLOSED = ("cleared", "closed", "resolved")


def jload(s):
    try:
        return json.loads(s) if s and s.strip() else None
    except Exception:
        return None


def _body(name):
    """One API body for this tick: <NAME>_FILE wins over <NAME>_JSON. None when absent or not JSON."""
    path = os.environ.get(name + "_FILE", "")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return jload(f.read())
        except OSError:
            return None
    return jload(os.environ.get(name + "_JSON", ""))


def open_integrity(g):
    """Open integrity findings from a graph sample. The key holds a list, or the /api/integrity
    object with a `findings` list. A finding without a status counts as open."""
    raw = (g or {}).get("integrity")
    if isinstance(raw, dict):
        raw = raw.get("findings")
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict) and str(f.get("status") or "").lower() not in CLOSED]


def finding_channel(f):
    """The channel a finding names: the tag (unsigned_write), the controller channel (current_balance),
    or the rail when the balance check names no channel."""
    return f.get("tag") or f.get("channel") or ("rail:%s" % f["rail"] if f.get("rail") else "")


# ---------------------------------------------------------------- append (per tick)
def cmd_append(samples_path, timeline_path):
    g = _body("GRAPH")
    n = _body("NARR")
    scen = _body("SCEN")
    plant = _body("PLANT")
    tags = _body("TAGS")
    phase = os.environ.get("PHASE", "")
    cycle = os.environ.get("CYCLE", "")
    now = int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    # oom_pod / oom_eta_s keep their old names: the soonest forecast card of any class.
    rec = {"t": iso, "epoch": now, "phase": phase, "cycle": cycle,
           "scenario": os.environ.get("SCENARIO", ""), "fire_epoch": os.environ.get("FIRE_EPOCH", ""),
           "fire_ok": os.environ.get("FIRE_OK", ""), "ok": g is not None,
           "root": "", "root_score": "", "onset_s": "", "n_edges": "", "n_findings": "",
           "n_incipient": "", "active": "", "pods": "", "top_victim": "", "evidence": "",
           "oom_pod": "", "oom_eta_s": "", "incip_class": "", "incipients": "",
           "n_integrity": "", "integrity_kinds": "", "integrity_channel": "",
           "tags_source": "", "tags_not_good": "", "chiller_tripped": "", "chiller_trip_reason": "",
           "seg_util": "", "seg_drop": "", "active_scenarios": "", "narrative": "", "narr_source": ""}
    if g is not None:
        roots = g.get("root") or []
        edges = g.get("edges") or []
        find = g.get("findings") or []
        incip = g.get("incipient") or []
        meta = g.get("meta") or {}
        if roots:
            rec["root"] = roots[0].get("pod") or ""
            rec["root_score"] = roots[0].get("score")
            rec["onset_s"] = roots[0].get("onset_s")
        rec["n_edges"], rec["n_findings"], rec["n_incipient"] = len(edges), len(find), len(incip)
        rec["active"], rec["pods"] = meta.get("active"), meta.get("pods")
        if edges:
            src = [e for e in edges if e.get("src") == rec["root"]] or edges
            e = max(src, key=lambda x: abs(x.get("r") or 0.0))
            rec["top_victim"] = e.get("dst") or ""
            rec["evidence"] = "|".join(e.get("evidence") or [])
        if incip:
            cards = sorted(incip, key=lambda x: _num(x.get("eta_s")) if _num(x.get("eta_s")) is not None else 1e9)
            rec["oom_pod"], rec["oom_eta_s"] = cards[0].get("pod") or "", cards[0].get("eta_s")
            rec["incip_class"] = cards[0].get("class") or ""
            rec["incipients"] = "|".join(
                "%s:%s:%s" % (c.get("class") or "", c.get("pod") or "",
                              "" if _num(c.get("eta_s")) is None else int(_num(c["eta_s"]))) for c in cards)
        integ = open_integrity(g)
        rec["n_integrity"] = len(integ)
        rec["integrity_kinds"] = "|".join(sorted({str(f.get("kind") or "") for f in integ}))
        rec["integrity_channel"] = "|".join(ch for ch in (finding_channel(f) for f in integ) if ch)
    if isinstance(tags, dict):
        rec["tags_source"] = tags.get("source") or ""
        rec["tags_not_good"] = sum(1 for t in tags.get("tags") or [] if t.get("quality") != "GOOD")
    if isinstance(plant, dict) and plant.get("source") != "unavailable":
        ch = (plant.get("devices") or {}).get("chiller-1") or {}
        if ch:
            rec["chiller_tripped"] = 1 if ch.get("tripped") else 0
            rec["chiller_trip_reason"] = ch.get("trip_reason") or ""
        seg = (plant.get("segments") or {}).get("field-1") or {}
        rec["seg_util"], rec["seg_drop"] = seg.get("utilization", ""), seg.get("drop_ratio", "")
    if isinstance(scen, list):
        rec["active_scenarios"] = "|".join(str(s.get("id")) for s in scen
                                           if isinstance(s, dict) and s.get("active") is True)
    if n:
        rec["narrative"], rec["narr_source"] = n.get("text", ""), n.get("source", "")

    with open(samples_path, "a") as f:
        f.write(json.dumps(rec) + "\n")

    cols = ["t", "epoch", "phase", "cycle", "scenario", "fire_epoch", "fire_ok", "ok", "root", "root_score",
            "onset_s", "n_edges", "n_findings", "n_incipient", "active", "pods", "top_victim", "evidence",
            "oom_pod", "oom_eta_s", "incip_class", "incipients", "n_integrity", "integrity_kinds",
            "integrity_channel", "tags_source", "tags_not_good", "chiller_tripped", "chiller_trip_reason",
            "seg_util", "seg_drop", "active_scenarios", "narr_source"]
    new = (not os.path.exists(timeline_path)) or os.path.getsize(timeline_path) == 0
    with open(timeline_path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        w.writerow([rec.get(c, "") for c in cols])


# ---------------------------------------------------------------- meta (run start)
def cmd_meta(rundir):
    windows = {}
    for item in os.environ.get("WINDOWS", "").split():   # "PS2:300:150 PS6:420:240"
        parts = item.split(":")
        if len(parts) == 3:
            windows[parts[0]] = {"observe_s": parts[1], "cooldown_s": parts[2]}
    meta = {"run_id": os.path.basename(rundir.rstrip("/\\")),
            "started_epoch": int(time.time()),
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scenarios": os.environ.get("SCENARIOS", ""),
            "duration_h": os.environ.get("DURATION_H", ""),
            "sample_s": os.environ.get("SAMPLE_S", ""),
            "baseline_s": os.environ.get("BASELINE_S", ""),
            "observe_s": os.environ.get("OBSERVE_S", ""),
            "cooldown_s": os.environ.get("COOLDOWN_S", ""),
            "windows": windows,
            "host": os.environ.get("HOSTNAME", "")}
    with open(os.path.join(rundir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


# ---------------------------------------------------------------- report (run end)
def _read_samples(rundir):
    out = []
    p = os.path.join(rundir, "samples.jsonl")
    if not os.path.exists(p):
        return out
    with open(p) as f:
        for line in f:
            r = jload(line)
            if r:
                out.append(r)
    return out


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def _split(s):
    return [x for x in str(s or "").split("|") if x]


def _cards(r):
    """Forecast cards of a sample as (class, pod, eta_s). A sample from before the class field has
    only the soonest card, with an unknown class."""
    if "incipients" not in r:
        eta = _num(r.get("oom_eta_s"))
        return [("", r.get("oom_pod") or "", eta)] if eta is not None else []
    out = []
    for item in _split(r.get("incipients")):
        parts = item.split(":")
        if len(parts) == 3:
            out.append((parts[0], parts[1], _num(parts[2])))
    return out


def _blind(r):
    return r.get("tags_source") == "unavailable"


def _matching_cards(spec, r):
    cards = _cards(r)
    if "incipients" not in r and spec["kind"] == "forecast":
        return cards                     # an old sample: any card counts, as the old report did
    return [c for c in cards if c[0] == spec["expect"] and (not spec.get("pod") or c[1] == spec["pod"])]


def is_hit(spec, r):
    kind = spec["kind"]
    if kind == "root":
        return (r.get("root") or "") == spec["expect"]
    if kind == "integrity":
        return spec["expect"] in _split(r.get("integrity_kinds"))
    if kind == "forecast":
        return bool(_matching_cards(spec, r))
    if kind == "blind":
        return _blind(r) or bool(_matching_cards(spec, r))
    return False


def is_noisy(r):
    """A false positive in a baseline or cooldown sample: any root, integrity finding, or forecast card."""
    return bool(r.get("root")) or (_num(r.get("n_integrity")) or 0) > 0 or (_num(r.get("n_incipient")) or 0) > 0


def scenario_order(meta, rows):
    """The ids in run order: meta scenarios first, then any other observe phase seen in the samples."""
    order = []
    for sid in str(meta.get("scenarios") or "").upper().split():
        if sid not in order:
            order.append(sid)
    for r in rows:
        ph = r.get("phase", "")
        if ph and ph not in QUIET and ph not in order:
            order.append(ph)
    return order


def attribute(rows):
    """Set r["_scen"] on every sample. New samples carry `scenario`. For older samples, a baseline row
    belongs to the next fault window and a cooldown row to the previous one."""
    pending, last = [], ""
    for r in rows:
        ph, sid = r.get("phase", ""), r.get("scenario") or ""
        if ph not in QUIET:
            sid = sid or ph
            for p in pending:
                p["_scen"] = sid
            pending, last = [], sid
            r["_scen"] = sid
        elif sid:
            r["_scen"] = sid
        elif ph == "baseline":
            r["_scen"] = ""
            pending.append(r)
        else:
            r["_scen"] = last


def _cycle_key(c):
    n = _num(c)
    return (0, n, "") if n is not None else (1, 0, str(c))


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def _fire_t0(cr):
    """The time-to-detect origin of one cycle: the fire call, else the first observe sample."""
    fires = [_num(r.get("fire_epoch")) for r in cr if _num(r.get("fire_epoch")) is not None]
    return min(fires) if fires else min(r["epoch"] for r in cr)


def score(sid, rows):
    spec = SPEC.get(sid)
    obs = [r for r in rows if r.get("phase") == sid]
    cycles = sorted({r.get("cycle") for r in obs}, key=_cycle_key)
    n = len(obs)
    with_root = [r for r in obs if (r.get("root") or "")]
    counts = {}
    for r in with_root:
        counts[r["root"]] = counts.get(r["root"], 0) + 1
    dom, dom_n = (max(counts.items(), key=lambda kv: kv[1]) if counts else ("—", 0))
    quiet = [r for r in rows if r.get("phase") in QUIET and r.get("_scen") == sid]
    fp_base = sum(1 for r in quiet if r.get("phase") == "baseline" and is_noisy(r))
    fp_cool = sum(1 for r in quiet if r.get("phase") == "cooldown" and is_noisy(r))
    fire_failed = len({r.get("cycle") for r in obs if str(r.get("fire_ok")) == "0"})
    out = {
        "kind": spec["kind"] if spec else "unknown", "cycles": len(cycles), "samples": n,
        "detect_rate": round(len(with_root) / n, 3) if n else 0.0,
        "dominant_root": dom, "dominant_n": dom_n,
        "fp_baseline": fp_base, "fp_cooldown": fp_cool,
        "quiet_baseline": sum(1 for r in quiet if r.get("phase") == "baseline"),
        "quiet_cooldown": sum(1 for r in quiet if r.get("phase") == "cooldown"),
        "fire_failed": fire_failed,
        "fire_timed": any(_num(r.get("fire_epoch")) is not None for r in obs),
    }
    if spec is None:
        out.update({"expected": "(no scoring rule for this id)", "hit_rate": None, "hit_cycle_rate": None,
                    "correct_rate": None, "median_ttd_s": None, "min_eta_s": None,
                    "note": "Not scored. The samples stay in the timeline and the false-positive count."})
        return out

    hits = [r for r in obs if is_hit(spec, r)]
    hit_cycles = {r.get("cycle") for r in hits}
    ttds = []
    for c in cycles:
        cr = [r for r in obs if r.get("cycle") == c and r.get("epoch") is not None]
        ch = [r["epoch"] for r in cr if is_hit(spec, r)]
        if cr and ch:
            ttds.append(max(0, min(ch) - _fire_t0(cr)))
    etas = [c[2] for r in obs for c in _matching_cards(spec, r) if c[2] is not None]
    expected = {"root": "root %s", "integrity": "%s finding", "forecast": "%s forecast card",
                "blind": "%s card or SCADA view blind"}[spec["kind"]] % spec["expect"]
    if spec.get("pod"):
        expected = expected.replace(" card", " card on %s" % spec["pod"], 1)
    out.update({
        "expected": expected, "expect": spec["expect"],
        "hit_rate": round(len(hits) / n, 3) if n else 0.0,
        "hit_cycle_rate": round(len(hit_cycles) / len(cycles), 3) if cycles else 0.0,
        "correct_rate": round(len(hits) / n, 3) if (n and spec["kind"] == "root") else None,
        "median_ttd_s": _median(ttds), "min_eta_s": min(etas) if etas else None,
        "note": spec["note"],
    })
    if spec.get("channel"):
        named = [r for r in hits if spec["channel"] in _split(r.get("integrity_channel"))]
        out["channel"] = spec["channel"]
        out["channel_rate"] = round(len(named) / len(hits), 3) if hits else 0.0
    if spec["kind"] == "blind":
        blind_cycles = {r.get("cycle") for r in obs if _blind(r)}
        card_cycles = {r.get("cycle") for r in obs if _matching_cards(spec, r)}
        out["blind_cycle_rate"] = round(len(blind_cycles) / len(cycles), 3) if cycles else 0.0
        out["card_cycle_rate"] = round(len(card_cycles) / len(cycles), 3) if cycles else 0.0
    if spec.get("trip"):
        trip_s, trip_cycles = [], set()
        for c in cycles:
            cr = [r for r in obs if r.get("cycle") == c and r.get("epoch") is not None]
            tripped = [r["epoch"] for r in cr if str(r.get("chiller_tripped")) == "1"]
            if cr and tripped:
                trip_cycles.add(c)
                trip_s.append(max(0, min(tripped) - _fire_t0(cr)))
        out["trip"] = spec["trip"]
        out["trip_cycle_rate"] = round(len(trip_cycles) / len(cycles), 3) if cycles else 0.0
        out["median_trip_s"] = _median(trip_s)
    if spec.get("segment"):
        utils = [_num(r.get("seg_util")) for r in obs if _num(r.get("seg_util")) is not None]
        drops = [_num(r.get("seg_drop")) for r in obs if _num(r.get("seg_drop")) is not None]
        out["segment"] = spec["segment"]
        out["peak_util"] = max(utils) if utils else None
        out["peak_drop"] = max(drops) if drops else None
    return out


def _events(rows):
    """Notable events in time order: first detection per fault window, integrity findings, forecast
    cards, blind samples, chiller-1 trips, failed triggers, and false positives in quiet windows."""
    events, seen = [], set()

    def add(key, kind, r, text):
        if key not in seen:
            seen.add(key)
            events.append({"t": r.get("t"), "kind": kind, "text": text})

    for r in rows:
        ph, root, cyc, sid = r.get("phase", ""), (r.get("root") or ""), r.get("cycle"), r.get("_scen", "")
        where = "%s cycle %s" % (ph, cyc) if ph not in QUIET else "%s of %s cycle %s" % (ph, sid or "?", cyc)
        spec = SPEC.get(ph)
        if ph not in QUIET and str(r.get("fire_ok")) == "0":
            add(("fail", cyc, ph), "fail", r, "%s: trigger failed, so these samples show no fault" % where)
        if ph not in QUIET and root:
            expect = spec["expect"] if spec and spec["kind"] == "root" else None
            ok = "✓" if (expect and root == expect) else ("?" if expect else "·")
            add(("detect", cyc, ph), "detect", r, "%s: root = %s %s" % (where, root, ok))
        if ph in QUIET and is_noisy(r):
            what = ("root %s" % root) if root else ("integrity finding" if (_num(r.get("n_integrity")) or 0) > 0
                                                    else "forecast card")
            add(("fp", cyc, sid, ph), "fp", r, "%s: false positive (%s)" % (where, what))
        if (_num(r.get("n_integrity")) or 0) > 0:
            add(("integrity", cyc, sid, r.get("integrity_kinds")), "integrity", r, "%s: integrity %s on %s" % (
                where, r.get("integrity_kinds") or "?", r.get("integrity_channel") or "?"))
        for cls, pod, eta in _cards(r):
            eta_txt = " in ~%ds" % eta if eta is not None else ""
            add(("forecast", cyc, sid, cls, pod), "forecast", r,
                "%s: forecast card %sfor %s%s" % (where, (cls + " ") if cls else "", pod, eta_txt))
        if _blind(r):
            add(("blind", cyc, sid), "blind", r, "%s: SCADA view blind (/api/tags unavailable)" % where)
        if str(r.get("chiller_tripped")) == "1":
            add(("trip", cyc, sid), "trip", r, "%s: chiller-1 tripped (%s)" % (
                where, r.get("chiller_trip_reason") or "no reason given"))
    return events[-60:]


def cmd_report(rundir, template):
    rows = _read_samples(rundir)
    meta_path = os.path.join(rundir, "meta.json")
    meta = (jload(open(meta_path).read()) or {}) if os.path.exists(meta_path) else {}
    attribute(rows)
    order = scenario_order(meta, rows)

    warnings = []
    for sid in order:
        if sid not in SPEC:
            warnings.append("%s has no scoring rule in record.py. Its samples are kept but not scored." % sid)
        elif not any(r.get("phase") == sid for r in rows):
            warnings.append("%s is in the run config but has no fault-window samples." % sid)
    for w in warnings:
        print("WARN: " + w, file=sys.stderr)

    per = {sid: score(sid, rows) for sid in order}

    series = []
    for r in rows:
        ph = r.get("phase", "")
        root = r.get("root", "") or ""
        spec = SPEC.get(ph)
        matched = None
        if spec and spec["kind"] == "root" and root:
            matched = (root == spec["expect"])
        series.append({"epoch": r.get("epoch"), "t": r.get("t"), "phase": ph, "cycle": r.get("cycle"),
                       "n_edges": _num(r.get("n_edges")) or 0, "n_findings": _num(r.get("n_findings")) or 0,
                       "root": root, "matched": matched, "oom": _num(r.get("oom_eta_s")),
                       "hit": is_hit(spec, r) if spec else None,
                       "fp": is_noisy(r) if ph in QUIET else None,
                       "integrity": _num(r.get("n_integrity")) or 0, "blind": _blind(r),
                       "trip": str(r.get("chiller_tripped")) == "1"})

    quiet = [r for r in rows if r.get("phase") in QUIET]
    epochs = [r["epoch"] for r in series if r.get("epoch")]
    t0, t1 = (min(epochs), max(epochs)) if epochs else (0, 0)
    summary = {
        "run_id": meta.get("run_id", os.path.basename(rundir.rstrip("/\\"))),
        "started_iso": meta.get("started_iso", ""),
        "ended_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t1)) if t1 else "",
        "duration_min": round((t1 - t0) / 60.0, 1) if t1 else 0.0,
        "n_samples": len(series),
        "n_cycles": len({r.get("cycle") for r in rows if r.get("phase") in order}),
        "scenarios": meta.get("scenarios") or " ".join(order),
        "quiet_samples": len(quiet),
        "fp_samples": sum(1 for r in quiet if is_noisy(r)),
        "baseline_chiller_trips": sum(1 for r in rows if r.get("phase") == "baseline"
                                      and str(r.get("chiller_tripped")) == "1"),
        "warnings": warnings,
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    data = {"summary": summary, "per_scenario": per, "series": series, "events": _events(rows),
            "scen_order": order}
    blob = json.dumps(data).replace("</", "<\\/")

    with open(template, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__SOAK_DATA__", blob)
    out = os.path.join(rundir, "report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(out)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: record.py append|meta|report ...")
    cmd = sys.argv[1]
    if cmd == "append":
        cmd_append(sys.argv[2], sys.argv[3])
    elif cmd == "meta":
        cmd_meta(sys.argv[2])
    elif cmd == "report":
        cmd_report(sys.argv[2], sys.argv[3])
    else:
        sys.exit("unknown command: " + cmd)


if __name__ == "__main__":
    main()
