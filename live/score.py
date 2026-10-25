#!/usr/bin/env python3
"""The live engine's own score (2026-10-22, the direct live path, step 5).

The batch score (eval/nodes.py) reads each run from the true onset of its fault. The live engine does
not know the onset: it opens incidents (live/engine.py). This file scores those incidents, on the
rows of `python -m live.engine check` (the streaming engine on the saved runs of a campaign) and the
runs' labels (models.pkl), with every rate as a count, its n and a 95 % Wilson interval:

  - a single fault: the root is named when the first incident that opens at or after the onset names
    the true root; the delay is from the onset to that incident's opening. When the incident rows have
    their candidates and victims (since 2026-10-22), "in an incident" counts the runs where the true
    root is in an incident that opens at or after the onset.
  - a control case (F14 to F16, a load or product change): a run with an incident at or after the
    onset is a false alarm.
  - a pair: passed when every real root is named by an incident that opens at or after the first onset.
  - false incidents: every incident of a normal run, and every incident that opens before the onset of
    a fault, control or pair run, per hour of plant time. An incident that merged into an older one
    (the same root) counts with the older one.
  - extra incidents: the incidents of a single-fault run after the first one from the onset, and how
    many of them name another machine than the root (what the operator sees after a fault).

    python -m live.score /var/tmp/visr-s2.json eval/results/<campaign> [--out live-score.md]
"""
import argparse
import json
import os
import pickle
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from eval.metrics import wilson                           # noqa: E402
from eval.nodes import SAMPLE_S                           # noqa: E402


def _rate(k, n):
    if not n:
        return {"k": k, "n": n, "ci95": None}
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "share": k / n, "ci95": [lo, hi]}


def _txt(r):
    return "-" if not r["n"] else f"{r['k']} / {r['n']} = {100 * r['share']:.0f} % ({100 * r['ci95'][0]:.0f} to {100 * r['ci95'][1]:.0f} %)"


def score(rows, runs, hours):
    """rows: the check rows; runs: {key: (path, info)} of the campaign; hours: scored hours per run."""
    out = {"faults": {}, "controls": {}, "pairs": {}, "false": {}}
    per = {}
    ctrl = {}
    pairs = {}
    normal_inc = normal_n = 0
    pre_inc, pre_s = 0, 0.0
    extra = extra_wrong = 0
    for r in rows:
        key = r["key"]
        _, kind, _ = key.split("|")
        info = runs.get(key, (None, {}))[1]
        incs = r["incidents"]
        own = [i for i in incs if not i.get("merged_into")]      # a merged incident is part of an older one
        if kind == "normal":
            normal_n += 1
            normal_inc += len(own)
            continue
        io = int(info["onset_s"] / SAMPLE_S)
        pre_inc += sum(1 for i in own if i["t0"] < io)
        pre_s += info["onset_s"]
        after = [i for i in incs if i["t0"] >= io]
        if kind == "pair":
            roots = [f["root"] for f in info["faults"] if not f["control"]]
            named = {str(i["named"]) for i in after if i.get("named")}
            p = pairs.setdefault(info["fid"], [0, 0])
            p[0] += all(x in named for x in roots)
            p[1] += 1
            continue
        if info.get("control"):
            c = ctrl.setdefault(info["fid"], [0, 0])
            c[0] += bool(after)
            c[1] += 1
            continue
        f = per.setdefault(info["fid"], {"root": info["root"], "named": 0, "inc": 0, "has_members": 0, "n": 0, "delays": []})
        f["n"] += 1
        first = after[0] if after else None
        later = [i for i in after[1:] if not i.get("merged_into")]
        extra += len(later)
        extra_wrong += sum(1 for i in later if str(i.get("named")) != info["root"])
        if first is not None and str(first.get("named")) == info["root"]:
            f["named"] += 1
            f["delays"].append((first["t0"] - io) * SAMPLE_S)
        if any("hit" in i for i in incs):
            f["has_members"] += 1
            f["inc"] += any(info["root"] in (i.get("hit") or {}) or info["root"] in (i.get("victims") or {}) for i in after)
    tot = {"named": 0, "inc": 0, "has_members": 0, "n": 0}
    for fid, f in sorted(per.items(), key=lambda kv: int(kv[0][1:])):
        d = sorted(f["delays"])
        out["faults"][fid] = {"root": f["root"], "root_named": _rate(f["named"], f["n"]),
                              "in_incident": _rate(f["inc"], f["has_members"]) if f["has_members"] else None,
                              "delay_median_s": d[len(d) // 2] if d else None}
        for k in tot:
            tot[k] += f[k]
    out["total"] = {"root_named": _rate(tot["named"], tot["n"]),
                    "in_incident": _rate(tot["inc"], tot["has_members"]) if tot["has_members"] else None,
                    "extra_incidents": extra, "extra_not_root": extra_wrong, "runs": tot["n"]}
    for fid, (k, n) in sorted(ctrl.items()):
        out["controls"][fid] = _rate(k, n)
    out["controls_total"] = _rate(sum(v[0] for v in ctrl.values()), sum(v[1] for v in ctrl.values()))
    for fid, (k, n) in sorted(pairs.items()):
        out["pairs"][fid] = _rate(k, n)
    out["pairs_total"] = _rate(sum(v[0] for v in pairs.values()), sum(v[1] for v in pairs.values()))
    h_normal = normal_n * hours
    out["false"] = {"normal_incidents": normal_inc, "normal_hours": h_normal,
                    "normal_per_hour": normal_inc / h_normal if h_normal else None,
                    "pre_onset_incidents": pre_inc, "pre_onset_hours": pre_s / 3600.0,
                    "pre_onset_per_hour": pre_inc / (pre_s / 3600.0) if pre_s else None}
    return out


def report_md(s, name):
    L = [f"# The live engine's score ({name})", "",
         "Incidents of the streaming engine (live/engine.py) on the saved runs of the campaign. The root is named "
         "when the first incident from the onset names the true root.", "",
         "| Fault | Root | Root named (95 % CI) | Root in an incident | Delay to the incident, median (s) |", "|---|---|---|---|---|"]
    for fid, f in s["faults"].items():
        delay = "-" if f["delay_median_s"] is None else f"{f['delay_median_s']:.0f}"
        inc = _txt(f["in_incident"]) if f["in_incident"] else "-"
        L.append(f"| {fid} | {f['root']} | {_txt(f['root_named'])} | {inc} | {delay} |")
    t = s["total"]
    L.append(f"| all | | {_txt(t['root_named'])} | {_txt(t['in_incident']) if t['in_incident'] else '-'} | |")
    L += ["", f"After the first incident from the onset, the single-fault runs have {t['extra_incidents']} more incidents "
          f"in {t['runs']} runs ({t['extra_not_root']} of them name another machine than the root)."]
    L += ["", "| Control case | Runs with an incident after the onset (false) |", "|---|---|"]
    for fid, r in s["controls"].items():
        L.append(f"| {fid} | {_txt(r)} |")
    L.append(f"| all | {_txt(s['controls_total'])} |")
    L += ["", "| Pair | Passed (every real root named by an incident after the first onset) |", "|---|---|"]
    for fid, r in s["pairs"].items():
        L.append(f"| {fid} | {_txt(r)} |")
    L.append(f"| all | {_txt(s['pairs_total'])} |")
    fl = s["false"]
    L += ["", "False incidents:", "",
          f"- normal runs: {fl['normal_incidents']} in {fl['normal_hours']:.0f} h"
          + (f", one per {fl['normal_hours'] / fl['normal_incidents']:.1f} h" if fl["normal_incidents"] else ""),
          f"- before the onset of the fault, control and pair runs: {fl['pre_onset_incidents']} in {fl['pre_onset_hours']:.0f} h"
          + (f", one per {fl['pre_onset_hours'] / fl['pre_onset_incidents']:.1f} h" if fl["pre_onset_incidents"] else "")]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows", help="the JSON rows of python -m live.engine check --out")
    ap.add_argument("results", help="the campaign folder (models.pkl)")
    ap.add_argument("--out", help="write the report here (Markdown) and its numbers next to it (JSON)")
    a = ap.parse_args(argv)
    with open(a.rows) as fh:
        rows = json.load(fh)
    with open(os.path.join(a.results, "models.pkl"), "rb") as fh:
        m = pickle.load(fh)
    s = score(rows, m["runs"], m["hours"])
    md = report_md(s, os.path.basename(os.path.normpath(a.results)))
    print(md)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        with open(os.path.splitext(a.out)[0] + ".json", "w") as fh:
            json.dump(s, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
