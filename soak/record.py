#!/usr/bin/env python3
"""Soak recorder + report builder for the SiliconKnights causal engine.

Two jobs, picked by argv[1]:

  append <samples.jsonl> <timeline.csv>
      Flatten ONE live verdict snapshot (passed in via the GRAPH_JSON / NARR_JSON env vars, with
      PHASE / CYCLE) into a raw JSONL line + a flat CSV row. Called once per sample tick by soak.sh.

  meta <rundir>
      Write meta.json at the start of a run (config snapshot).

  report <rundir> <report_template.html>
      Read samples.jsonl, compute per-scenario summary stats, and emit a self-contained report.html
      (data embedded — open it by double-click, like a `powercfg /batteryreport`). No reasoning here;
      this only summarizes what the engine already decided. Honest by construction: it shows the
      ACTUAL dominant root per scenario, so the S2 mis-root / S3 physics gaps appear as-is.
"""
import sys, os, json, time, csv

# What a clean run SHOULD root each scenario at (the ground truth). S5 has no causal root by design
# (a leak is self-caused) — its success signal is the OOM forecast firing, handled separately.
EXPECT = {"S1": "cooling-monitor", "S2": "log-archiver", "S3": "analytics-batch", "S5": None}
NOTE = {
    "S1": "Hero path — expect root = cooling-monitor.",
    "S2": "Known gap: may mis-root to a held backbone edge (a no-baseline CronJob source). See BOOK §4.2.",
    "S3": "Out of scope on this box (CPU physics can't starve co-residents) — the engine stays honestly quiet.",
    "S5": "Self-caused leak — success = OOM forecast fires BEFORE the kill (no causal root expected).",
}
SCEN_ORDER = ["S1", "S2", "S3", "S5"]


def jload(s):
    try:
        return json.loads(s) if s and s.strip() else None
    except Exception:
        return None


# ---------------------------------------------------------------- append (per tick)
def cmd_append(samples_path, timeline_path):
    g = jload(os.environ.get("GRAPH_JSON", ""))
    n = jload(os.environ.get("NARR_JSON", ""))
    phase = os.environ.get("PHASE", "")
    cycle = os.environ.get("CYCLE", "")
    now = int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    rec = {"t": iso, "epoch": now, "phase": phase, "cycle": cycle, "ok": g is not None,
           "root": "", "root_score": "", "onset_s": "", "n_edges": "", "n_findings": "",
           "n_incipient": "", "active": "", "pods": "", "top_victim": "", "evidence": "",
           "oom_pod": "", "oom_eta_s": "", "narrative": "", "narr_source": ""}
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
            f0 = min(incip, key=lambda x: x.get("eta_s") if x.get("eta_s") is not None else 1e9)
            rec["oom_pod"], rec["oom_eta_s"] = f0.get("pod") or "", f0.get("eta_s")
    if n:
        rec["narrative"], rec["narr_source"] = n.get("text", ""), n.get("source", "")

    with open(samples_path, "a") as f:
        f.write(json.dumps(rec) + "\n")

    cols = ["t", "epoch", "phase", "cycle", "ok", "root", "root_score", "onset_s", "n_edges",
            "n_findings", "n_incipient", "active", "pods", "top_victim", "evidence", "oom_pod",
            "oom_eta_s", "narr_source"]
    new = (not os.path.exists(timeline_path)) or os.path.getsize(timeline_path) == 0
    with open(timeline_path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        w.writerow([rec.get(c, "") for c in cols])


# ---------------------------------------------------------------- meta (run start)
def cmd_meta(rundir):
    meta = {"run_id": os.path.basename(rundir.rstrip("/\\")),
            "started_epoch": int(time.time()),
            "started_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scenarios": os.environ.get("SCENARIOS", ""),
            "duration_h": os.environ.get("DURATION_H", ""),
            "sample_s": os.environ.get("SAMPLE_S", ""),
            "observe_s": os.environ.get("OBSERVE_S", ""),
            "cooldown_s": os.environ.get("COOLDOWN_S", ""),
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


def cmd_report(rundir, template):
    rows = _read_samples(rundir)
    meta = jload(open(os.path.join(rundir, "meta.json")).read()) if os.path.exists(os.path.join(rundir, "meta.json")) else {}

    series, per = [], {}
    for r in rows:
        ph = r.get("phase", "")
        root = r.get("root", "") or ""
        oom = _num(r.get("oom_eta_s"))
        matched = None
        if ph in EXPECT and EXPECT[ph] is not None and root:
            matched = (root == EXPECT[ph])
        series.append({"epoch": r.get("epoch"), "t": r.get("t"), "phase": ph, "cycle": r.get("cycle"),
                       "n_edges": _num(r.get("n_edges")) or 0, "n_findings": _num(r.get("n_findings")) or 0,
                       "root": root, "matched": matched, "oom": oom})

    # per-scenario rollup (only the observe-phase rows for that scenario)
    for s in SCEN_ORDER:
        obs = [r for r in rows if r.get("phase") == s]
        cycles = sorted({r.get("cycle") for r in obs})
        n = len(obs)
        with_root = [r for r in obs if (r.get("root") or "")]
        correct = [r for r in obs if (r.get("root") or "") == (EXPECT[s] or "\0")]
        # dominant root
        counts = {}
        for r in with_root:
            counts[r["root"]] = counts.get(r["root"], 0) + 1
        dom, dom_n = (max(counts.items(), key=lambda kv: kv[1]) if counts else ("—", 0))
        # OOM (S5) — fraction of cycles where a forecast fired, and the best (smallest) ETA seen
        oom_cycles, min_eta = set(), None
        for r in obs:
            e = _num(r.get("oom_eta_s"))
            if e is not None:
                oom_cycles.add(r.get("cycle"))
                min_eta = e if (min_eta is None or e < min_eta) else min_eta
        # median time-to-first-detection per cycle (root present, or OOM for S5)
        ttds = []
        for c in cycles:
            cr = [r for r in obs if r.get("cycle") == c and r.get("epoch") is not None]
            if not cr:
                continue
            t0 = min(r["epoch"] for r in cr)
            if s == "S5":
                hit = [r["epoch"] for r in cr if _num(r.get("oom_eta_s")) is not None]
            else:
                hit = [r["epoch"] for r in cr if (r.get("root") or "")]
            if hit:
                ttds.append(min(hit) - t0)
        ttds.sort()
        med_ttd = ttds[len(ttds) // 2] if ttds else None
        per[s] = {
            "cycles": len(cycles), "samples": n,
            "detect_rate": round(len(with_root) / n, 3) if n else 0.0,
            "correct_rate": round(len(correct) / n, 3) if n else 0.0,
            "dominant_root": dom, "dominant_n": dom_n,
            "expected": EXPECT[s] or "(no root — OOM forecast)",
            "oom_cycle_rate": round(len(oom_cycles) / len(cycles), 3) if cycles else 0.0,
            "min_oom_eta_s": min_eta, "median_ttd_s": med_ttd, "note": NOTE[s],
        }

    epochs = [r["epoch"] for r in series if r.get("epoch")]
    t0, t1 = (min(epochs), max(epochs)) if epochs else (0, 0)
    summary = {
        "run_id": meta.get("run_id", os.path.basename(rundir.rstrip("/\\"))),
        "started_iso": meta.get("started_iso", ""),
        "ended_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t1)) if t1 else "",
        "duration_min": round((t1 - t0) / 60.0, 1) if t1 else 0.0,
        "n_samples": len(series),
        "n_cycles": len(sorted({r.get("cycle") for r in rows if r.get("phase") in SCEN_ORDER})),
        "scenarios": meta.get("scenarios", " ".join(SCEN_ORDER)),
        "generated_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # notable events: first detection per (cycle, scenario) + OOM warnings (de-duped) + a few narratives
    events, seen = [], set()
    for r in rows:
        ph, root = r.get("phase", ""), (r.get("root") or "")
        key = (r.get("cycle"), ph)
        if ph in EXPECT and root and key not in seen:
            seen.add(key)
            ok = "✓" if (EXPECT[ph] and root == EXPECT[ph]) else ("?" if EXPECT[ph] else "·")
            events.append({"t": r.get("t"), "kind": "detect", "text":
                           f"{ph} cycle {r.get('cycle')}: root = {root} {ok}"})
        e = _num(r.get("oom_eta_s"))
        okey = ("oom", r.get("cycle"))
        if e is not None and okey not in seen:
            seen.add(okey)
            events.append({"t": r.get("t"), "kind": "oom", "text":
                           f"{ph} cycle {r.get('cycle')}: OOM forecast — {r.get('oom_pod')} in ~{int(e)}s"})
    # keep the last 40 events
    events = events[-40:]

    data = {"summary": summary, "per_scenario": per, "series": series, "events": events,
            "scen_order": SCEN_ORDER}
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
