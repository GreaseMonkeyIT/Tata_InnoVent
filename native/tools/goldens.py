#!/usr/bin/env python3
"""The expected values of the native node kernels: the Python engine on small synthetic runs
(native/README.md, "The tests").

    python native/tools/goldens.py native/work/synthetic

For each case it trains an eval.nodes.Node on synthetic runs (as eval/tests/test_nodes.py and
live/tests/test_engine.py do), puts the trained nodes in a live pack in the form of build_pack
(live/engine.py), and exports the neutral pack with export_pack.py. Then it loads the nodes back as
the live engine does (load_pack), streams the scored run through live.engine.StreamNode one sample at
a time, and writes for every case:

    goldens/<case>/manifest.json   the inputs of every sample and the expected values, in the neutral
                                   format of the pack (one .npy per array, SHA-256 of each file)
    goldens/<case>/state/          the node's state after the middle sample, in the portable form

The cases cover both alarm rules (cusumk, cusumkv) and the plain cusum, text inputs (the PLC state
selects the residual spread) and a text output, a gap in a meter, a gap in an input, the counter
changes of --cmd-delta with a reset, a confirmation time, the controller loops left out of coverage
and of the variance statistic, the steady mask, a clip, an unknown state and a missing value. The tool
reads the overhaul code and changes nothing in it.
"""
import argparse
import hashlib
import os
import pickle
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np                                        # noqa: E402

import export_pack as X                                   # noqa: E402  (puts the repository on sys.path)
from eval import nodes as N                               # noqa: E402
from live import engine as E                              # noqa: E402

STATE_FORMAT = "visr-node-state"
CAMPAIGN = "synthetic-n1"
N_SCORED, MID = 2400, 1199          # scored samples per case; the state is written after scored sample MID


# ------------------------------------------------------------------------------- the synthetic runs --

def text_runs(seed):
    """A machine with a PLC state (run / idle) that sets its output, a text output (hi / lo) and a site
    input (as live/tests/test_engine.py _runs). The scored run has a slow shift, extra noise (for the
    variance statistic) and a 30 s gap in the meter."""
    rng = np.random.default_rng(seed)

    def run(n, lead=60, scored=False):
        u = rng.normal(size=n)
        u[0] = 0.0                                                       # every run starts from the same state
        st = np.where(np.arange(n) % 300 < 200, "run", "idle")
        # a slow wave that the inputs do not explain: the slow-bias k of a.y rises above its floor
        wave = 0.08 * np.sin(2.0 * np.pi * (np.arange(n) + rng.integers(3000)) / 3000.0)
        y = 2.0 * u + np.where(st == "run", 1.0, 0.0) + 0.1 * rng.normal(size=n) + wave
        if scored:
            y[lead + 1500:] += 0.4                                       # a slow shift
            y[lead + 1900:lead + 2100] += 0.4 * rng.normal(size=200)     # more noise
            y[lead + 900:lead + 930] = np.nan                            # a gap in the meter
        mode = np.where(y > 0.5, "hi", "lo")
        return N.Run({"site.u": u, "a.state": st, "a.y": y, "a.mode": mode}, lead=lead)
    return run


def counter_runs(seed):
    """A press like pierce-1 (eval/tests/test_nodes.py _counter_runs): a PLC hit counter steps by one
    each second except in the second of the stroke. The scored run has a pressure offset and a counter
    reset."""
    rng = np.random.default_rng(seed)

    def run(n, lead=30, scored=False):
        phase = (np.arange(n) + rng.integers(6)) % 6
        stroke = phase == 0
        count = np.cumsum(~stroke).astype(float) + 15_000.0
        p = 10.0 + 140.0 * stroke + 0.5 * rng.normal(size=n)
        if scored:
            p = p + np.where(np.arange(n) >= lead + 900, 3.0, 0.0)
            count[lead + 1300:] -= count[lead + 1300]                    # the counter resets
        v = 400.0 + rng.normal(size=n)
        v[0] = 400.0
        return N.Run({"m.v": v, "m.count": count, "m.p": p}, lead=lead)
    return run


def loop_runs(seed):
    """A heater under its own PLC loop: a state (heat / hold / off), a setpoint and a valve that are
    outputs of its loop, a temperature that follows the setpoint. The scored run has a state the
    training never saw, missing state values and a temperature offset."""
    rng = np.random.default_rng(seed)
    blocks = [("heat", 100, 60.0), ("hold", 150, 55.0), ("off", 50, 20.0)]

    def run(n, lead=60, scored=False):
        st, sp = [], []
        while len(st) < n:
            for name, m, v in blocks:
                st += [name] * m
                sp += [v] * m
        st, sp = np.array(st[:n], object), np.array(sp[:n]) + 0.2 * rng.normal(size=n)
        u = 25.0 + 2.0 * np.sin(np.arange(n) / 600.0) + 0.3 * rng.normal(size=n)
        sp[0], u[0] = 60.0, 25.0
        x = np.empty(n)
        a = np.exp(-1.0 / 20.0)
        x[0] = sp[0]
        for i in range(1, n):
            x[i] = a * x[i - 1] + (1.0 - a) * sp[i]
        x = x + 0.05 * (u - 25.0) + 0.1 * rng.normal(size=n) + 0.1 * np.sin(2.0 * np.pi * (np.arange(n) + rng.integers(2400)) / 2400.0)
        ctl = np.clip((sp - x) * 0.5, 0.0, 1.0) + 0.02 * rng.normal(size=n)
        if scored:
            st[lead + 1500:lead + 1520] = "fault"                        # a state the training never saw
            st[lead + 1700:lead + 1705] = None                           # no value
            x[lead + 1800:] += 2.0
        if not scored:
            st = st.astype(str)
        return N.Run({"b.u": u, "b.state": st, "b.sp": sp, "b.x": x, "b.ctl": ctl}, lead=lead)
    return run


def plain_runs(seed, input_gap=False):
    """One input, one output (as eval/tests/test_nodes.py test_clip_caps_z_for_the_cusum_rules). The
    scored run has a shift and, for input_gap, 10 s without the input: its filters stay NaN after it,
    as in the Python engine, and the node no longer knows."""
    rng = np.random.default_rng(seed)

    def run(n, lead=60, scored=False):
        u = rng.normal(size=n)
        u[0] = 0.0
        y = 2.0 * u + 0.1 * rng.normal(size=n)
        if scored:
            y[lead + 600:] += 0.3
            if input_gap:
                u[lead + 1000:lead + 1010] = np.nan
        return N.Run({"c.u": u, "c.y": y}, lead=lead)
    return run


TEXT_DOC = {"feeds": ["site.u"], "commands": ["a.state"], "measures": ["a.y", "a.mode"], "asset": "a", "loop_outputs": []}
COUNTER_DOC = {"feeds": ["m.v"], "commands": ["m.count"], "measures": ["m.p"], "asset": "m", "loop_outputs": []}
LOOP_DOC = {"feeds": ["b.u"], "commands": ["b.state", "b.sp"], "measures": ["b.x", "b.ctl"], "asset": "b",
            "loop_outputs": ["b.sp", "b.ctl"]}
PLAIN_DOC = {"feeds": ["c.u"], "commands": [], "measures": ["c.y"], "asset": "c", "loop_outputs": []}


def train(name, doc, runs, rule, cmd_delta=False, clip=None, var_steady=0.0, n_fit=2500, n_cal=2500):
    """A node trained as eval/nodes.py trains it: 3 fit runs, 2 calibration runs long enough for the
    slow-bias k (CUSUM_BIAS_S) to be one value per output."""
    nd = N.Node(name, doc, rule, clip=clip)
    nd.cmd_delta = cmd_delta
    nd.var_steady = var_steady
    fit = [(i, runs(n_fit)) for i in range(3)]
    cal = [(9, runs(n_cal)), (10, runs(n_cal))]
    return nd.train(fit, cal)


def cases():
    """[(case, trained node, the scored run)]. Each case is one node of the synthetic pack."""
    out = []
    tr = text_runs(3)
    for rule in ("cusumk", "cusumkv"):
        nd = train(f"{rule}_text_gap", TEXT_DOC, tr, rule)
        out.append((nd.name, nd, tr(N_SCORED + 60, scored=True)))
    conf = N.Node.from_state(out[-1][1].state())
    conf.name, conf.confirm_s = "cusumkv_confirm", 5.0
    out.append((conf.name, conf, tr(N_SCORED + 60, scored=True)))
    cr = counter_runs(7)
    nd = train("cusumkv_cmd_delta", COUNTER_DOC, cr, "cusumkv", cmd_delta=True)
    assert nd.delta_in == ["m.count"], nd.delta_in
    out.append((nd.name, nd, cr(N_SCORED + 30, scored=True)))
    lr = loop_runs(11)
    nd = train("cusumkv_loops_clip", LOOP_DOC, lr, "cusumkv", clip=4.0, var_steady=10.0)
    out.append((nd.name, nd, lr(N_SCORED + 60, scored=True)))
    pr = plain_runs(13, input_gap=True)
    nd = train("cusumk_input_gap", PLAIN_DOC, pr, "cusumk")
    out.append((nd.name, nd, pr(N_SCORED + 60, scored=True)))
    pr = plain_runs(17)
    nd = train("cusum_plain", PLAIN_DOC, pr, "cusum")
    out.append((nd.name, nd, pr(N_SCORED + 60, scored=True)))
    return out


# ------------------------------------------------------------------------------------ the stream --

class Recording(E.StreamNode):
    """StreamNode that keeps the input and output vectors of the last sample (for the features)."""

    def _uy(self, s):
        u, y = super()._uy(s)
        self.last_u, self.last_y = u, y
        return u, y


def write_state(sn, out, node, pack_sha):
    """The node's state in the portable form (native/README.md): what a takeover needs to go on."""
    f = X.Folder(out)
    n = sn.n_out
    ar = {
        "c": f.array("c.npy", sn.c, float), "mn": f.array("mn.npy", sn.mn, float),
        "rs": f.array("rs.npy", sn.rs, float), "rc": f.array("rc.npy", sn.rc, float),
        "hist_rs": f.array("hist_rs.npy", np.array([h[0] for h in sn.hist], float).reshape(len(sn.hist), n)),
        "hist_rc": f.array("hist_rc.npy", np.array([h[1] for h in sn.hist], float).reshape(len(sn.hist), n)),
        "cv": f.array("cv.npy", sn.cv, float), "mv": f.array("mv.npy", sn.mv, float),
        "s_m": f.array("s_m.npy", sn.s_m, float), "s_v": f.array("s_v.npy", sn.s_v, float),
        "lz_m": f.array("lz_m.npy", sn.lz[0], np.int64), "lz_v": f.array("lz_v.npy", sn.lz[1], np.int64),
        "up_m": f.array("up_m.npy", sn.up[0], bool), "up_v": f.array("up_v.npy", sn.up[1], bool),
        "above_m": f.array("above_m.npy", sn.above[0], bool), "above_v": f.array("above_v.npy", sn.above[1], bool),
        "prev": f.array("prev.npy", [sn.prev.get(t, np.nan) for t in sn.nd.delta_in], float),
        "prev_set": f.array("prev_set.npy", [t in sn.prev for t in sn.nd.delta_in], bool),
    }
    if sn.y is not None:
        ar["filters"] = f.array("filters.npy", sn.y, float)
    if sn.z is not None:
        ar["z"] = f.array("z.npy", sn.z, float)
    f.manifest({
        "format": STATE_FORMAT, "version": 1, "node": node, "pack_manifest_sha256": pack_sha,
        "written_by": "native/tools/goldens.py (live.engine.StreamNode)",
        "scalars": {"lead": int(sn.lead), "taken": int(sn.k), "t": int(sn.t), "on_m": bool(sn.on_m),
                    "on_v": bool(sn.on_v), "run_len": int(sn.run_len),
                    "prev_bin": None if sn.prev_bin is None else int(sn.prev_bin),
                    "last_change": None if sn.last_change is None else int(sn.last_change),
                    "alarm": bool(sn.alarm), "cover": bool(sn.cover)},
        "arrays": ar,
    })


def golden(case, nd, r, out, pack_sha):
    """Stream the scored run through StreamNode and write the inputs and the expected values."""
    f = X.Folder(out)
    samples = list(E.samples_of(r))
    tags = []
    for i, t in enumerate(sorted(r)):
        col = [s[t] for s in samples]
        if any(isinstance(v, str) for v in col):
            names = sorted({v for v in col if isinstance(v, str)})
            codes = [names.index(v) if isinstance(v, str) else -1 for v in col]
            tags.append({"tag": t, "kind": "text", "names": names, "file": f.array(f"in/{i:03d}.npy", codes, np.int32)})
        else:
            tags.append({"tag": t, "kind": "num", "file": f.array(f"in/{i:03d}.npy", col, float)})
    sn = Recording(nd)
    sn.reset(r.lead)
    rec = {k: [] for k in ("x", "yv", "z", "cover", "s_m", "s_v", "on_m", "on_v", "alarm", "cp")}
    cross = []
    for s in samples:
        a = sn.step(s)
        if a is None:
            continue
        rec["x"].append(np.concatenate([sn.last_u, sn.y.ravel()]))
        rec["yv"].append(sn.last_y)
        rec["z"].append(sn.z)
        rec["cover"].append(sn.cover)
        rec["s_m"].append(sn.s_m)
        rec["s_v"].append(sn.s_v if sn.var else np.zeros(0))
        rec["on_m"].append(sn.on_m)
        rec["on_v"].append(sn.on_v)
        rec["alarm"].append(a)
        rec["cp"].append(sn.change_point())
        cross += sn.crossings
        if sn.t == MID:
            write_state(sn, os.path.join(out, "state"), nd.name, pack_sha)
    T = len(rec["alarm"])
    batch = nd.alarms(r)                              # the batch engine on the whole run, for the record
    zb, _ = nd.z(r)
    exp = {
        "x": f.array("x.npy", rec["x"], float), "yv": f.array("yv.npy", rec["yv"], float),
        "z": f.array("z.npy", rec["z"], float), "cover": f.array("cover.npy", rec["cover"], bool),
        "s_m": f.array("s_m.npy", rec["s_m"], float),
        "s_v": f.array("s_v.npy", np.array(rec["s_v"], float).reshape(T, -1)),
        "on_m": f.array("on_m.npy", rec["on_m"], bool), "on_v": f.array("on_v.npy", rec["on_v"], bool),
        "alarm": f.array("alarm.npy", rec["alarm"], bool), "cp": f.array("cp.npy", rec["cp"], np.int64),
        "crossings": f.array("crossings.npy", np.array(cross, np.int64).reshape(-1, 2)),
    }
    al = np.array(rec["alarm"])
    info = {"alarm_samples": int(al.sum()), "episodes": int((al & ~np.r_[False, al[:-1]]).sum()),
            "cover_share": float(np.mean(rec["cover"])),
            "batch_alarms_equal": bool(np.array_equal(al, batch)),
            "batch_z_max_abs_diff": float(np.nanmax(np.abs(np.array(rec["z"]) - zb))) if T else 0.0}
    f.manifest({"format": "visr-goldens", "version": 1, "case": case, "node": nd.name,
                "pack_manifest_sha256": pack_sha, "written_by": "native/tools/goldens.py",
                "lead": int(r.lead), "samples": len(samples), "scored": T, "state_after": MID,
                "tags": tags, "expect": exp, "info": info})
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", help="the folder to write: live-pack.pkl, pack/ and goldens/")
    a = ap.parse_args(argv)
    t0 = time.time()
    cs = cases()
    os.makedirs(a.out, exist_ok=True)
    pack = {"nodes": {name: nd.state() for name, nd, _ in cs}, "wiring": {name: nd.doc for name, nd, _ in cs},
            "sig2": None, "meta": {"synthetic": "native/tools/goldens.py"}, "campaign": CAMPAIGN, "calib_windows": 0}
    pkl = os.path.join(a.out, E.PACK)
    with open(pkl, "wb") as fh:
        pickle.dump(pack, fh)
    man, pack_sha = X.export(pkl, os.path.join(a.out, "pack"))
    nodes = E.load_pack(pkl)["nodes"]                 # the nodes as the live engine loads them
    for name, _, r in cs:
        info = golden(name, nodes[name], r, os.path.join(a.out, "goldens", name), pack_sha)
        nd = nodes[name]
        print(f"{name:22s} k {np.round(np.atleast_1d(nd.k), 3).tolist()} h {nd.h:.2f}"
              + (f" kv {np.round(np.atleast_1d(nd.kv), 3).tolist()} hv {nd.hv:.2f}" if nd.rule == "cusumkv" else ""))
        print(f"{name:22s} rule {nodes[name].rule:8s} alarms {info['alarm_samples']:5d} in {info['episodes']:3d} episodes, "
              f"covered {info['cover_share']:.3f}, stream = batch: {info['batch_alarms_equal']}")
    print(f"{a.out}: pack ({len(man['files'])} arrays, manifest {pack_sha[:16]}), {len(cs)} cases ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
