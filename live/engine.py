#!/usr/bin/env python3
"""The streaming engine (2026-10-22, the direct live path, step 2).

The engine of eval/nodes.py, one sample at a time. Each node (`StreamNode`) takes the trained node of
a campaign and keeps the state that `Node.alarms` builds over a whole run: the filters of its inputs,
the standardized residual of its model, the two-sided CUSUM sums on the mean, the CUSUM on the
variance, the alarm latches and the change points. After the residual every step is the batch
arithmetic in the same order, so the sums equal the batch sums. The residual itself equals the batch
residual to rounding only (about 1e-14 of the signal): the batch filters run as a block scan and the
model multiplies whole matrices.

The batch scores a run from the true onset of its fault. The live engine does not know the onset, so
it opens an incident at an alarm when no incident is open. When the incident is POST_S old, it names
the root of a single fault by the rules of the batch, on the alarms since the incident opened: the
graph rule (eval/nodes.py `score_fault`, 120 s) and the counterfactual (eval/cf.py, the engine's rule
since LOG-124) on the samples it has kept. The incident closes after HOLD_S with no alarm.

    python -m live.engine pack eval/results/<campaign>              # the model pack for the live engine
    python -m live.engine check eval/results/<campaign> --runs 40    # streaming against the batch

The check runs both engines on the same saved runs of the campaign: the alarms of every node sample by
sample, the change points, and the roots of the incident that opens at the batch's first alarm.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import pickle
import sys
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from eval import cf as CF                                 # noqa: E402
from eval import nodes as N                               # noqa: E402

STREAM_RULES = ("cusum", "cusumk", "cusumkv")
POST_S = CF.POST_S       # the root is named this long after the first alarm (the graph rule's window)
HOLD_S = 600.0           # project choice: an incident closes after 10 min with no alarm
KEEP_S = N.LEAD_S + 6 * 3600.0   # samples kept for the counterfactual: a batch run (the lead and 6 h)
PACK = "live-pack.pkl"


class StreamNode:
    """One trained node (eval/nodes.py `Node`), one sample at a time, with the state of `Node.alarms`."""

    def __init__(self, nd):
        if nd.rule not in STREAM_RULES:
            raise ValueError(f"{nd.name}: the streaming engine has the rules {', '.join(STREAM_RULES)}, not {nd.rule!r}")
        self.nd, self.name = nd, nd.name
        self.a = np.exp(-N.SAMPLE_S / np.asarray(nd.model.taus, float))     # as twin/node_model.py FilterBank
        self.n_out = len(nd.out_names)
        self.var = nd.rule == "cusumkv"
        self.w = int(N.VAR_WIN_S / N.SAMPLE_S)
        self.reset(0)

    def reset(self, lead):
        """A new run: `lead` samples only warm the filters, as in a batch run."""
        n = self.n_out
        self.lead, self.k, self.t = int(lead), 0, -1     # samples taken, scored sample index
        self.y = None                                     # the filter state (inputs x taus)
        self.c, self.mn = np.zeros(2 * n), np.zeros(2 * n)
        self.rs, self.rc = np.zeros(n), np.zeros(n)       # the rolling mean's cumulative sums
        self.hist = collections.deque([(self.rs, self.rc)], maxlen=self.w + 1)
        self.cv, self.mv = np.zeros(n), np.zeros(n)
        self.on_m = self.on_v = False
        self.run_len = 0
        self.prev_bin, self.last_change = None, None
        # per sum: the last sample at or below 0, and whether it passed its limit since then (change_point)
        self.lz = [np.full(2 * n, -1), np.full(n, -1)]
        self.up = [np.zeros(2 * n, bool), np.zeros(n, bool)]
        self.above = [np.zeros(2 * n, bool), np.zeros(n, bool)]
        self.crossings = []                               # (sample, change point) of the sums that crossed at this sample
        self.s_m, self.s_v = np.zeros(2 * n), np.zeros(n)
        self.alarm = self.cover = False
        self.z = None
        self.prev = {}                                    # the last value of each command that enters as a change

    def _uy(self, s):
        nd = self.nd
        u = [_num(s.get(t)) for t in nd.num_in]
        for t, cs in nd.cats.items():
            v = s.get(t)
            u += [1.0 if v == c else 0.0 for c in cs]
        resets = getattr(nd, "counter_resets", False)
        for t in getattr(nd, "delta_in", ()):            # the change since the last sample (0 at the first)
            v = _num(s.get(t))
            p = self.prev.get(t, v)
            u.append(v if resets and v < p else v - p)  # a step down is a reset: the change is the new value
            self.prev[t] = v
        y = []
        for t in nd.doc["measures"]:
            v = s.get(t)
            if t in nd.out_cats:
                y += [1.0 if v == c else 0.0 for c in nd.out_cats[t]]
            else:
                y.append(_num(v))
        return np.array(u, float), np.array(y, float)

    def step(self, s):
        """Take one sample {tag: value}. Returns the alarm (None during the lead)."""
        nd, m = self.nd, self.nd.model
        u, y = self._uy(s)
        if self.y is None:
            self.y = np.repeat(u[:, None], len(self.a), axis=1)      # the filters start at the first sample
        self.y = self.a[None, :] * self.y + (1.0 - self.a)[None, :] * u[:, None]
        self.k += 1
        if self.k <= self.lead:
            return None
        self.t += 1
        self.crossings = []
        x = np.concatenate([u, self.y.ravel()])[None, :]             # inputs, then each input's filters
        res = y - m._predict_x(x)[0]
        cov = bool(m._covered_x(x, 0.10)[0])
        g = -1
        if nd.state_cmd is not None:
            v = s.get(nd.state_cmd)
            g = nd.cats[nd.state_cmd].index(v) if v in nd.cats[nd.state_cmd] else -1
        z = res / nd.sd.get(int(g), nd.sd_all)
        if nd.clip:
            z = np.clip(z, -nd.clip, nd.clip)
        self.z, self.cover = z, cov
        # the CUSUM on the mean (eval/nodes.py cusum): cumulative sum less its running minimum
        ok = cov & np.isfinite(z)
        self.c = self.c + np.concatenate([np.where(ok, z - nd.k, 0.0), np.where(ok, -z - nd.k, 0.0)])
        self.mn = np.minimum(self.mn, np.minimum(self.c, 0.0))
        self.s_m = self.c - self.mn
        sm = self.s_m.max() if self.s_m.size else 0.0
        self.on_m = True if sm > nd.h else (False if sm <= 0.0 else self.on_m)
        self._track(0, self.s_m, nd.h)
        if self.var:
            # the rolling mean over VAR_WIN_S (eval/nodes.py rolling_mean), then the variance statistic
            self.rs = self.rs + np.where(ok, z, 0.0)
            self.rc = self.rc + ok.astype(float)
            self.hist.append((self.rs, self.rc))
            s0, c0 = self.hist[0]
            ws, wc = self.rs - s0, self.rc - c0
            rm = np.where(wc >= self.w / 2, ws / np.maximum(wc, 1.0), np.nan)
            xv = z - rm
            uv = xv * xv / nd.var_d - 1.0
            if nd.var_skip.any():
                uv[nd.var_skip] = np.nan
            if self._steady_masked(g):
                uv[:] = np.nan
            okv = cov & np.isfinite(uv)
            self.cv = self.cv + np.where(okv, uv - nd.kv, 0.0)
            self.mv = np.minimum(self.mv, np.minimum(self.cv, 0.0))
            self.s_v = self.cv - self.mv
            sv = self.s_v.max() if self.s_v.size else 0.0
            self.on_v = True if sv > nd.hv else (False if sv <= 0.0 else self.on_v)
            self._track(1, self.s_v, nd.hv)
        out = (self.on_m or self.on_v) and cov
        if nd.confirm_s:
            self.run_len = self.run_len + 1 if out else 0
            out = self.run_len >= int(nd.confirm_s / N.SAMPLE_S)
        self.alarm = out
        return out

    def _steady_masked(self, g):
        """The variance statistic leaves out var_steady seconds after a change of the PLC state."""
        nd = self.nd
        if not nd.var_steady or nd.state_cmd is None:
            return False
        if self.prev_bin is not None and g != self.prev_bin:
            self.last_change = self.t
        self.prev_bin = g
        return self.last_change is not None and self.t - self.last_change < int(nd.var_steady / N.SAMPLE_S)

    def _track(self, i, s, h):
        above = s > h
        for j in np.flatnonzero(above & ~self.above[i]):      # a sum crosses its limit (eval/nodes.py crossings)
            self.crossings.append((self.t, int(self.lz[i][j]) + 1))
        self.above[i] = above
        zero = s <= 0.0
        self.lz[i][zero] = self.t
        self.up[i][zero] = False
        self.up[i] |= above

    def change_point(self):
        """Page's estimate of when the change behind the present alarm began (eval/nodes.py change_point)."""
        best = None
        for i, s in enumerate((self.s_m, self.s_v)):
            hit = (s > 0.0) & self.up[i]
            if hit.any():
                c = int(self.lz[i][hit].min()) + 1
                best = c if best is None else min(best, c)
        return self.t if best is None else best


def _num(v):
    return float(v) if isinstance(v, (int, float)) else np.nan


class Engine:
    """Every node of a campaign, the incidents and their roots. `start(lead)` before a run, then `step`
    once per sample. sig2: the free-replay variances of the counterfactual (the pack); without them the
    engine names the graph rule's root only. keep_s: the samples kept for the counterfactual. Up to that
    length the counterfactual sees the whole run, as in a batch run. After it, the oldest samples go,
    and the first LEAD_S of the kept samples warm the filters (the lead of a batch run)."""

    def __init__(self, nodes, w, sig2=None, hold_s=HOLD_S, post_s=POST_S, keep_s=KEEP_S, second_roots=False):
        self.nodes, self.w, self.sig2 = nodes, w, sig2
        self.sn = {n: StreamNode(nd) for n, nd in nodes.items()}
        self.asset = {n: d.get("asset", n) for n, d in w.items()}
        self.reach = N.downstream(w)
        self.tags = sorted({t for d in w.values() for r in ("feeds", "commands", "measures") for t in d[r]})
        self.hold, self.post = int(hold_s / N.SAMPLE_S), int(post_s / N.SAMPLE_S)
        self.keep = int(keep_s / N.SAMPLE_S)
        # A second root inside an incident (2026-10-22, option): a machine of a named incident whose sum
        # crosses its limit with a change that began after the incident's POST_S, and no machine upstream
        # of it with an alarm or a crossing in the POST_S before that change, opens its own incident
        # (the onset-free form of eval/nodes.py score_pair: a second fault on a machine already in alarm).
        self.second_roots = second_roots
        self.start(0)

    def start(self, lead):
        for s in self.sn.values():
            s.reset(lead)
        self.lead, self.k, self.t = int(lead), 0, -1
        self.cols = {t: collections.deque(maxlen=self.keep) for t in self.tags}
        self.incidents = []
        self.on = {m: False for m in set(self.asset.values())}     # a machine alarms when one of its nodes does
        self.rose = {}                                              # machine -> the last sample it started to alarm
        self.inc_of = {}                                            # machine -> the incident it belongs to
        self.mev = {m: collections.deque(maxlen=64) for m in self.on}   # machine -> its recent rises and crossings

    def step(self, sample):
        """Take one sample {tag: value}. Returns the incidents whose state changed at this sample."""
        for t in self.tags:
            self.cols[t].append(sample.get(t))
        self.k += 1
        got = {n: s.step(sample) for n, s in self.sn.items()}
        if self.k <= self.lead:
            return []
        self.t += 1
        t, changed = self.t, []
        now = {m: False for m in self.on}
        for n, a in got.items():
            now[self.asset[n]] |= bool(a)
        for m in sorted(m for m in now if now[m] and not self.on[m]):
            self.mev[m].append(t)
            inc = self._event(m, t)
            if inc is not None:
                changed.append(inc)
        self.on = now
        if self.second_roots:
            changed += self._second_roots(t)
        for n, a in got.items():
            inc = self.inc_of.get(self.asset[n])
            if a and inc is not None and inc["closed"] is None and n not in inc["first"]:
                m = self.asset[n]
                inc["first"][n], inc["cp"][n] = t, self.sn[n].change_point()
                inc["hit_cp"][m] = min(inc["hit_cp"].get(m, inc["cp"][n]), inc["cp"][n])
        for inc in self.incidents:
            if inc["closed"] is not None:
                continue
            if inc["named"] is None and t >= inc["t0"] + self.post:
                self._name(inc)
                changed.append(inc)
            if any(now[m] for m in list(inc["hit"]) + list(inc["victims"])):
                inc["last_on"] = t
            elif inc["named"] is not None and t - inc["last_on"] >= self.hold:
                inc["closed"] = t
                changed.append(inc)
        return changed

    def _event(self, m, t):
        """Machine m starts to alarm at sample t. A machine whose incident is still open stays in it (an
        alarm that goes and comes back). Else it joins the incident whose root is not named yet (the
        candidates of the graph rule: the machines that alarm within POST_S of the first). Else it is a
        victim of an incident when a machine upstream of it in the documents (not in a cycle with it)
        alarms now or started to alarm in the last POST_S, or when a machine in a cycle with it alarms in
        an open incident. Else it opens a new incident. Returns the incident it opened, or None."""
        self.rose[m] = t
        own = self.inc_of.get(m)
        if own is not None and own["closed"] is None:
            return None
        unnamed = [i for i in self.incidents if i["named"] is None and i["closed"] is None]
        if unnamed:
            inc = unnamed[-1]
            inc["hit"].setdefault(m, t)
            self.inc_of[m] = inc
            return None
        ups = [o for o in self.on if o != m and m in self.reach.get(o, ()) and o not in self.reach.get(m, ())
               and (self.on[o] or t - self.rose.get(o, -10 ** 9) <= self.post) and o in self.inc_of]
        # a machine in a cycle of the documents with a machine that alarms in an open incident (the cooling
        # water loop): the documents cannot order the two, so it joins that incident as a victim (2026-10-22)
        ups += [o for o in self.on if o != m and o in self.reach.get(m, ()) and m in self.reach.get(o, ())
                and self.on[o] and o in self.inc_of and self.inc_of[o]["closed"] is None]
        if ups:
            inc = self.inc_of[max(ups, key=lambda o: self.rose.get(o, -1))]
            if inc["closed"] is None:
                inc["victims"].setdefault(m, t)
                self.inc_of[m] = inc
                return None
        inc = {"id": len(self.incidents) + 1, "t0": t, "first": {}, "cp": {}, "hit": {m: t}, "hit_cp": {},
               "victims": {}, "named_graph": None, "named_cf": None, "named": None, "closed": None, "last_on": t}
        self.incidents.append(inc)
        self.inc_of[m] = inc
        return inc

    def _second_roots(self, t):
        """The crossings of this sample (covered samples only, as eval/nodes.py takes them for pairs)."""
        opened = []
        for n, sn in self.sn.items():
            if not sn.crossings or not sn.cover:
                continue
            m = self.asset[n]
            for _, cp in sn.crossings:
                self.mev[m].append(t)
                inc = self.inc_of.get(m)
                if inc is None or inc["closed"] is not None or inc["named"] is None or inc["named"] == m:
                    continue
                if cp <= inc["t0"] + self.post:
                    continue                              # a change of the incident's own first 120 s
                if any(m in self.reach.get(o, ()) and o not in self.reach.get(m, ())
                       and any(cp - self.post <= x <= cp for x in self.mev[o]) for o in self.mev if o != m):
                    continue                              # an upstream machine changed just before: a victim
                new = {"id": len(self.incidents) + 1, "t0": t, "first": {}, "cp": {}, "hit": {m: t}, "hit_cp": {},
                       "victims": {}, "named_graph": None, "named_cf": None, "named": None, "closed": None,
                       "last_on": t, "second_of": inc["id"]}
                self.incidents.append(new)
                self.inc_of[m] = new
                opened.append(new)
                break
        return opened

    def _name(self, inc):
        """The root of the incident: the graph rule and the counterfactual, as eval/nodes.py
        `score_fault` and eval/cf.py name a single fault at its first alarm."""
        t0, hit, hcp = inc["t0"], inc["hit"], inc["hit_cp"]
        cand = {m: i for m, i in hit.items() if i <= t0 + self.post}
        keep = [m for m in cand if not any(m in self.reach.get(o, ()) and o not in self.reach.get(m, ())
                                            for o in cand if o != m)]
        ti = min(cand[m] for m in keep)
        pick = sorted(m for m in keep if cand[m] == ti)
        if len(pick) > 1 and all(m in hcp for m in pick):
            cmin = min(hcp[m] for m in pick)
            pick = sorted(m for m in pick if hcp[m] == cmin)
        inc["named_graph"] = pick
        if self.sig2 is not None:
            inc["named_cf"], inc["cf_scores"] = self.counterfactual(t0, set(cand))
        got = inc["named_cf"]
        inc["named"] = got if got is not None else (pick[0] if len(pick) == 1 else ",".join(pick))
        # the same root as an older open incident: the twin explains these alarms by that incident's root,
        # so its machines join that incident as victims (2026-10-22). The record stays, with `merged_into`.
        older = [i for i in self.incidents if i is not inc and i["closed"] is None and i.get("merged_into") is None
                 and i["named"] == inc["named"] and i["id"] < inc["id"]]
        if older:
            keep = older[-1]
            for m, ti in list(inc["hit"].items()) + list(inc["victims"].items()):
                if m not in keep["hit"]:
                    keep["victims"].setdefault(m, ti)
                if self.inc_of.get(m) is inc:
                    self.inc_of[m] = keep
            keep["last_on"] = max(keep["last_on"], inc["last_on"])
            inc["merged_into"], inc["closed"] = keep["id"], self.t

    def run_so_far(self):
        """The kept samples as a run in the form of a saved view (eval/nodes.py save_view), and the
        offset of its scored samples: scored sample t of the engine is sample t - offset of the run."""
        cols = {}
        for t, vals in self.cols.items():
            v = N._array(list(vals))
            cols[t] = N.Text.encode(v) if v.dtype.kind in "USO" else v
        drop = self.k - len(next(iter(self.cols.values()))) if self.cols else 0
        if drop <= 0:
            return N.Run(cols, self.lead), 0
        lead = int(N.LEAD_S / N.SAMPLE_S)
        return N.Run(cols, lead), drop + lead - self.lead

    def counterfactual(self, t0, targets):
        """eval/cf.py on the kept samples: the suspect whose reset removes the most trouble at the targets."""
        CF._CTX.update(nodes=self.nodes, w=self.w, tnodes=self.nodes)
        CF._CTX.setdefault("twin", None)
        run, off = self.run_so_far()
        win = CF.window_terms(run, t0 - off)          # a window that starts before the kept samples is cut, as in a batch run
        sc = CF.target_scores(win, self.sig2, targets, self.asset)
        best = max(sc, key=sc.get) if sc and max(sc.values()) > 0 else None
        return best, dict(sorted(sc.items(), key=lambda kv: -kv[1])[:5])


# ------------------------------------------------------------------------------- the pack --

def load_campaign(results, view_dir=None):
    """The trained nodes, the documents and the runs of a campaign (models.pkl). view_dir: where the
    views are when the campaign ran on another machine (the file names stay)."""
    with open(os.path.join(results, "models.pkl"), "rb") as fh:
        m = pickle.load(fh)
    if view_dir:
        def re(p):
            return os.path.join(view_dir, os.path.basename(p))
        m["runs"] = {k: (re(p), info) for k, (p, info) in m["runs"].items()}
        m["calib"] = [(sd, re(p)) for sd, p in m["calib"]]
        m["fit"] = [(sd, re(p)) for sd, p in m["fit"]]
    m["nodes"] = {n: N.Node.from_state(s) for n, s in m["nodes"].items()}
    return m


def _calib_terms(path):
    return CF.calib_job(path, None, suspects=())


def _init_cf(results, view_dir):
    m = load_campaign(results, view_dir)
    CF._CTX.update(nodes=m["nodes"], w=m["wiring"], tnodes=m["nodes"], twin=None)


def build_pack(results, view_dir=None, workers=4, calib_step=4):
    """The live engine's model pack: the trained nodes, the documents and the free-replay variances of
    the counterfactual, from the same calibration windows as eval/cf.py (every calib_step-th day)."""
    m = load_campaign(results, view_dir)
    calib = m["calib"][::calib_step]
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_init_cf, initargs=(results, view_dir)) as ex:
        cal_w = [x for f in [ex.submit(_calib_terms, p) for _, p in calib] for x in f.result()]
    sig2 = CF.free_variance(cal_w, m["wiring"])
    with open(os.path.join(results, "models.pkl"), "rb") as fh:
        raw = pickle.load(fh)
    pack = {"nodes": raw["nodes"], "wiring": raw["wiring"], "sig2": sig2, "meta": raw["meta"],
            "campaign": os.path.basename(os.path.normpath(results)), "calib_windows": len(cal_w)}
    with open(os.path.join(results, PACK), "wb") as fh:
        pickle.dump(pack, fh)
    return pack


def load_pack(path):
    with open(path, "rb") as fh:
        p = pickle.load(fh)
    p["nodes"] = {n: N.Node.from_state(s) for n, s in p["nodes"].items()}
    return p


# ------------------------------------------------------------------------------ the check --

def samples_of(run):
    """The samples of a saved run, in order, as the plant service gives them ({tag: value})."""
    cols = {}
    for t, v in run.items():
        if isinstance(v, N.Text):
            cols[t] = [v.names[c] if c >= 0 else None for c in v.codes.tolist()]
        elif np.asarray(v).dtype.kind in "USO":
            cols[t] = [x if isinstance(x, str) else None for x in np.asarray(v, object).tolist()]
        else:
            cols[t] = np.asarray(v, float).tolist()
    n = len(next(iter(cols.values())))
    tags = list(cols)
    for i in range(n):
        yield {t: cols[t][i] for t in tags}


_CHECK = {}


def _init_check(pack_path):
    _CHECK["pack"] = load_pack(pack_path)


def check_run(key, path, info, cf_named=None, second_roots=False):
    """One saved run through the batch engine and the streaming engine. Returns the differences."""
    p = _CHECK["pack"]
    nodes, w = p["nodes"], p["wiring"]
    run = N.load_view(path)
    kind = key.split("|")[1]
    io = int(info["onset_s"] / N.SAMPLE_S) if kind != "normal" else None
    t1 = time.time()
    batch, cps = {}, {}
    for n, nd in nodes.items():
        batch[n] = nd.alarms(run, with_cover=True)
        if io is not None:
            fa = N.first_alarm(batch[n][0], io)
            cps[n] = None if fa is None else N.change_point(nd._stats, fa)
    t2 = time.time()
    eng = Engine(nodes, w, p["sig2"], second_roots=second_roots)
    eng.start(run.lead)
    T = len(next(iter(run.values()))) - run.lead
    al = {n: np.zeros(T, bool) for n in nodes}
    zmax = 0.0
    zb = {n: nodes[n].z(run)[0] for n in nodes}
    for s in samples_of(run):
        eng.step(s)
        if eng.t >= 0:
            for n, sn in eng.sn.items():
                al[n][eng.t] = sn.alarm
                d = np.nanmax(np.abs(sn.z - zb[n][eng.t])) if sn.z.size else 0.0
                zmax = max(zmax, float(d) if np.isfinite(d) else 0.0)
    t3 = time.time()
    diff = {n: int((al[n] != batch[n][0]).sum()) for n in nodes}
    out = {"key": key, "samples": T, "alarm_diff": {n: d for n, d in diff.items() if d}, "z_max_diff": zmax,
           "incidents": [{k: v for k, v in i.items() if k in ("id", "t0", "named_graph", "named_cf", "named", "closed",
                                                              "hit", "victims", "merged_into", "second_of")}
                         for i in eng.incidents],
           "wall_batch_s": round(t2 - t1, 1), "wall_stream_s": round(t3 - t2, 1)}
    if kind == "fault" and not info.get("control"):
        amap = {n: d["asset"] for n, d in w.items()}
        rec = N.score_fault({n: batch[n][0] for n in nodes}, info, amap, cps, N.downstream(w))
        t0 = min((N.first_alarm(batch[n][0], io) for n in nodes if N.first_alarm(batch[n][0], io) is not None),
                 default=None)
        inc = next((i for i in eng.incidents if i["t0"] == t0), None)
        after = next((i for i in eng.incidents if i["t0"] >= io), None)
        out.update({"t0": t0, "open_at_onset": any(batch[n][0][io] for n in nodes) if io < T else False,
                    "root": info["root"], "batch_graph": rec.get("named_g120"), "batch_cf": cf_named,
                    "live_graph": inc and inc["named_graph"], "live_cf": inc and inc["named_cf"],
                    "incident_found": inc is not None,
                    # the live engine's own answer: the first incident that opens at or after the onset
                    "live_named": after and after["named"], "live_t0": after and after["t0"]})
    if io is not None:
        out["incidents_before_onset"] = sum(1 for i in eng.incidents if i["t0"] < io)
    else:
        out["incidents_normal"] = len(eng.incidents)
    return out


def check(results, view_dir=None, keys=None, workers=4, cf_root=None, second_roots=False):
    pack_path = os.path.join(results, PACK)
    m = load_campaign(results, view_dir)
    roots = {}
    cfp = cf_root or os.path.join(results, "cf-root.json")
    if os.path.exists(cfp):
        with open(cfp) as fh:
            for sp, recs in json.load(fh)["faults"].items():
                for r in recs:
                    roots[f"{sp}|fault|{r['seed']}"] = r.get("named_cf")
    jobs = [(k, m["runs"][k]) for k in (keys or [k for k in m["runs"] if not k.startswith("train|")])]
    jobs = [(k, v) for k, v in jobs if os.path.exists(v[0])]
    out = []
    with cf.ProcessPoolExecutor(max_workers=workers, initializer=_init_check, initargs=(pack_path,)) as ex:
        futs = {ex.submit(check_run, k, p, info, roots.get(k), second_roots): k for k, (p, info) in jobs}
        for f in cf.as_completed(futs):
            r = f.result()
            out.append(r)
            print(json.dumps({k: r.get(k) for k in ("key", "alarm_diff", "z_max_diff", "batch_graph", "live_graph",
                                                      "batch_cf", "live_cf", "open_at_onset", "wall_stream_s")}),
                  flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("pack", help="write live-pack.pkl into a campaign folder")
    a1.add_argument("results")
    a1.add_argument("--views", help="the folder of the views, when the campaign ran on another machine")
    a1.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a2 = sub.add_parser("check", help="the streaming engine against the batch engine on saved runs")
    a2.add_argument("results")
    a2.add_argument("--views")
    a2.add_argument("--keys", help="a JSON list of run keys (split|kind|seed), default every scored run")
    a2.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a2.add_argument("--out", help="write the result rows here (JSON)")
    a2.add_argument("--second-roots", action="store_true", help="the engine names second roots inside incidents (Engine)")
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        t0 = time.time()
        p = build_pack(a.results, a.views, a.workers)
        print(f"{PACK}: {len(p['nodes'])} nodes, sig2 from {p['calib_windows']} calibration windows ({time.time() - t0:.0f} s)")
        return 0
    keys = json.load(open(a.keys)) if a.keys else None
    rows = check(a.results, a.views, keys, a.workers, second_roots=a.second_roots)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rows, fh, indent=1)
    bad = [r for r in rows if r["alarm_diff"]]
    roots = [r for r in rows if "batch_graph" in r]
    same_g = sum(r["live_graph"] == r["batch_graph"] for r in roots)
    same_c = sum(r["live_cf"] == r["batch_cf"] for r in roots if r["batch_cf"] is not None)
    n_c = sum(r["batch_cf"] is not None for r in roots)
    clean = [r for r in roots if not r["open_at_onset"]]
    print(f"{len(rows)} runs: alarms differ in {len(bad)}. Largest residual difference "
          f"{max((r['z_max_diff'] for r in rows), default=0):.3g} sd.")
    print(f"Roots, the incident at the batch's first alarm: graph rule equal {same_g} / {len(roots)}, counterfactual "
          f"equal {same_c} / {n_c}. Runs with no alarm on at the onset: graph {sum(r['live_graph'] == r['batch_graph'] for r in clean)} "
          f"/ {len(clean)}, counterfactual {sum(r['live_cf'] == r['batch_cf'] for r in clean if r['batch_cf'] is not None)} "
          f"/ {sum(r['batch_cf'] is not None for r in clean)}.")
    print(f"True root named: batch counterfactual {sum(r['batch_cf'] == r['root'] for r in roots)} / {len(roots)}, "
          f"live (the first incident from the onset) {sum(r['live_named'] == r['root'] for r in roots)} / {len(roots)}. "
          f"Incidents before the onset {sum(r.get('incidents_before_onset', 0) for r in rows)}, in normal runs "
          f"{sum(r.get('incidents_normal', 0) for r in rows)}.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
