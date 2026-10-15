"""Score metrics for the engine (ideas.md 16.3). Pure functions, no engine or sim imports.

A run is a timeline of engine passes. Each pass gives the root ranking (a list of names, best first)
and the trip cards (machine, eta in s). The run label gives the truth: the fault, its onset, the true
root (None for a calm run) and the trips the plant really made.

Definitions (fixed here, so every engine version is scored the same way):

  verdict       the first root that holds rank 1 for VERDICT_PASSES consecutive passes. An operator acts
                on a root that holds, not on one pass.
  first root    the rank-1 name of the first pass after the onset that names any root.
  detection     a verdict exists inside the watch window after the onset.
  top-1         the verdict is the true root. top-3: the true root is in the top 3 of the verdict pass.
  reciprocal    1 / rank of the true root in the verdict pass (0 when it is absent or no verdict).
  delay         seconds from the onset to the verdict pass.
  false alarm   a verdict while the plant is calm: in a calm run, or before the onset of a fault run.
                One episode counts once, however long it lasts.
  card          a trip card is true when its machine trips inside the watch window, false otherwise.
                A trip is covered when a card for that machine came before the trip. Lead time = trip
                time - first card time. ETA error = |first card's eta - (trip time - first card time)|.
"""
import math
from dataclasses import dataclass, field

VERDICT_PASSES = 3


def wilson(k, n, z=1.96):
    """The 95 % Wilson score interval of a rate k / n. Returns (low, high); (0, 1) for n = 0."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


@dataclass
class Pass:
    t: float                          # seconds since the run start
    ranking: list                     # root names, best first
    cards: list = field(default_factory=list)   # [(machine, eta_s)]


def verdicts(passes, k=VERDICT_PASSES):
    """Every verdict episode: (start_t, root, ranking at the verdict pass). A new episode starts when
    a root reaches k consecutive rank-1 passes after a pass with another rank-1 name or none."""
    out, run_root, run_len, emitted = [], None, 0, False
    for p in passes:
        top = p.ranking[0] if p.ranking else None
        if top is not None and top == run_root:
            run_len += 1
        else:
            run_root, run_len, emitted = top, (1 if top is not None else 0), False
        if top is not None and run_len >= k and not emitted:
            out.append((p.t, top, list(p.ranking)))
            emitted = True
    return out


def score_fault_run(passes, onset_t, truth, watch_s, trips=None, k=VERDICT_PASSES):
    """Score one fault run. Times are run times (the same clock as the passes). `trips`: {machine:
    trip time} that the plant really made."""
    pre = [p for p in passes if p.t < onset_t]
    post = [p for p in passes if onset_t <= p.t <= onset_t + watch_s]
    v = verdicts(post, k)
    first = next((p for p in post if p.ranking), None)
    res = {"truth": truth, "detected": bool(v), "false_alarms_pre": len(verdicts(pre, k)),
           "calm_s_pre": (pre[-1].t - pre[0].t) if len(pre) > 1 else 0.0}
    if v:
        t, root, ranking = v[0]
        rank = ranking.index(truth) + 1 if truth in ranking else None
        res.update({"verdict": root, "verdict_t": t - onset_t, "top1": root == truth,
                    "top3": rank is not None and rank <= 3, "rr": 1.0 / rank if rank else 0.0,
                    "verdict_ranking": ranking[:5]})
    else:
        res.update({"verdict": None, "verdict_t": None, "top1": False, "top3": False, "rr": 0.0,
                    "verdict_ranking": []})
    res["first_root"] = first.ranking[0] if first else None
    res["first_root_t"] = (first.t - onset_t) if first else None
    res["cards"] = score_cards(post, trips or {}, onset_t)
    return res


def score_calm_run(passes, k=VERDICT_PASSES):
    v = verdicts(passes, k)
    dur = (passes[-1].t - passes[0].t) if len(passes) > 1 else 0.0
    return {"truth": None, "false_alarms": len(v), "calm_s": dur, "false_roots": [r for _, r, _ in v]}


def score_cards(passes, trips, onset_t=0.0):
    """Trip cards against the real trips: per machine with a card, true or false; per trip, covered
    or not, with lead time and eta error."""
    first_card = {}
    for p in passes:
        for m, eta in p.cards:
            first_card.setdefault(m, (p.t, eta))
    carded = {m: (m in trips) for m in first_card}
    covered = {}
    for m, t_trip in trips.items():
        c = first_card.get(m)
        if c and c[0] <= t_trip:
            lead = t_trip - c[0]
            covered[m] = {"lead_s": lead, "eta_err_s": abs(c[1] - lead)}
        else:
            covered[m] = None
    return {"carded": carded, "covered": covered}


def summarize(results):
    """Aggregate scored runs into the metric table, each rate with its n and its Wilson interval."""
    fault = [r for r in results if r.get("truth") is not None]
    calm = [r for r in results if r.get("truth") is None]

    def rate(xs, key):
        k = sum(1 for x in xs if x.get(key))
        lo, hi = wilson(k, len(xs))
        return {"k": k, "n": len(xs), "rate": (k / len(xs)) if xs else None, "ci95": [lo, hi]}

    def pct(vals, q):
        vals = sorted(vals)
        if not vals:
            return None
        i = min(len(vals) - 1, max(0, int(math.ceil(q * len(vals))) - 1))
        return vals[i]

    out = {"by_scenario": {}, "overall": {}}
    by = {}
    for r in fault:
        by.setdefault(r["scenario"], []).append(r)
    for sc, rs in sorted(by.items()):
        delays = [r["verdict_t"] for r in rs if r["top1"]]
        out["by_scenario"][sc] = {
            "truth": rs[0]["truth"], "detection": rate(rs, "detected"), "top1": rate(rs, "top1"),
            "top3": rate(rs, "top3"), "mrr": sum(r["rr"] for r in rs) / len(rs),
            "delay_median_s": pct(delays, 0.5), "delay_p95_s": pct(delays, 0.95),
            "wrong_roots": sorted({r["verdict"] for r in rs if r["verdict"] and not r["top1"]}),
        }
    if fault:
        out["overall"] = {"top1": rate(fault, "top1"), "top3": rate(fault, "top3"),
                          "detection": rate(fault, "detected"),
                          "mrr": sum(r["rr"] for r in fault) / len(fault)}
    # false alarms: calm runs plus the calm stretch before each onset
    fa = sum(r["false_alarms"] for r in calm) + sum(r.get("false_alarms_pre", 0) for r in fault)
    hours = (sum(r["calm_s"] for r in calm) + sum(r.get("calm_s_pre", 0.0) for r in fault)) / 3600.0
    out["false_alarms"] = {"episodes": fa, "calm_hours": hours, "per_hour": (fa / hours) if hours else None,
                           "calm_runs_with_any": sum(1 for r in calm if r["false_alarms"]), "calm_runs": len(calm)}
    # trip cards
    true_c = false_c = trips = covered = 0
    leads, errs = [], []
    for r in fault:
        c = r.get("cards") or {}
        for ok in (c.get("carded") or {}).values():
            true_c += ok
            false_c += not ok
        for v in (c.get("covered") or {}).values():
            trips += 1
            if v:
                covered += 1
                leads.append(v["lead_s"])
                errs.append(v["eta_err_s"])
    out["cards"] = {"precision": {"k": true_c, "n": true_c + false_c, "ci95": list(wilson(true_c, true_c + false_c))},
                    "recall": {"k": covered, "n": trips, "ci95": list(wilson(covered, trips))},
                    "lead_median_s": pct(leads, 0.5), "eta_mae_s": (sum(errs) / len(errs)) if errs else None}
    return out
