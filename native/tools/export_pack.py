#!/usr/bin/env python3
"""Write the neutral model pack of the native engine from a live pack (native/README.md, "The model pack").

    python native/tools/export_pack.py eval/results/<campaign>/live-pack.pkl native/work/<campaign>/pack

The input is the pickle that `python -m live.engine pack` writes (build_pack in live/engine.py). The
output is one folder: manifest.json and one .npy file for each array, with the SHA-256 of each file
in the manifest. The native engine reads only this folder; it never reads a pickle or imports Python.

The constants that the Python engine keeps in its code (the sample period, the window of the
variance statistic, the coverage margin, the windows and the hold time of the incidents) go into
the manifest, read from the overhaul modules at export time. The tool reads the overhaul code and
changes nothing in it (no __pycache__ either). A node field that this tool does not know stops the
export: the native engine must not drop a part of a model without a word.
"""
import argparse
import hashlib
import inspect
import json
import os
import pickle
import sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

import numpy as np                                        # noqa: E402

from eval import nodes as N                               # noqa: E402
from live import engine as E                              # noqa: E402

FORMAT, VERSION = "visr-pack", 1
STREAM_RULES = tuple(E.STREAM_RULES)                      # cusum, cusumk, cusumkv
# the node fields the native engine reads, and those it keeps as information only (training settings)
NODE_KEYS = {"name", "doc", "rule", "clip", "confirm_s", "var_steady", "h", "k", "cats", "out_cats",
             "out_names", "model", "sd", "sd_all", "state_cmd", "delta_in", "counter_resets", "num_in",
             "var_skip", "var_d", "kv", "hv"}
INFO_KEYS = {"alpha", "limit", "hidden", "cmd_delta", "calib_covered", "r2"}
MODEL_KEYS = {"inputs", "outputs", "hidden", "taus", "seed", "params", "norm", "cov_cols", "bounds", "leads"}
NORM_KEYS = ("xm", "xs", "ym", "ys", "xlo", "xhi")


def coverage_margin():
    """The margin of the coverage test of the streaming engine. It is a literal in StreamNode.step;
    the export stops if the source no longer has it, so the pack cannot drift from the code."""
    src = inspect.getsource(E.StreamNode.step)
    if "_covered_x(x, 0.10)" not in src:
        raise SystemExit("live/engine.py StreamNode.step no longer calls _covered_x(x, 0.10): update export_pack.py")
    return 0.10


def constants():
    return {
        "sample_s": float(N.SAMPLE_S),          # one sample per second (eval/nodes.py)
        "var_win_s": float(N.VAR_WIN_S),        # the rolling mean of the variance statistic
        "coverage_margin": coverage_margin(),   # of each feature's training range, on each side
        "lead_s": float(N.LEAD_S),              # the samples that only warm the filters
        "post_s": float(E.POST_S),              # the root is named this long after an incident opens
        "hold_s": float(E.HOLD_S),              # an incident closes after this long with no alarm
        "keep_s": float(E.KEEP_S),              # the samples kept for the counterfactual
        "graph_windows_s": [float(x) for x in N.GRAPH_WINDOWS_S],
    }


def _plain(v):
    """JSON-safe copy: numpy scalars and arrays to Python, tuples to lists."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.ndarray):
        return _plain(v.tolist())
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


class Folder:
    """A neutral folder: manifest.json and .npy files, the SHA-256 of each file in the manifest."""

    def __init__(self, out):
        self.out, self.files = out, {}
        os.makedirs(out, exist_ok=True)

    def array(self, rel, a, dtype=None):
        a = np.ascontiguousarray(np.asarray(a, dtype=dtype))
        if a.dtype.byteorder == ">":
            a = a.astype(a.dtype.newbyteorder("<"))
        path = os.path.join(self.out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.save(path, a, allow_pickle=False)
        with open(path, "rb") as fh:
            self.files[rel] = hashlib.sha256(fh.read()).hexdigest()
        return rel

    def manifest(self, m):
        m["files"] = dict(sorted(self.files.items()))
        with open(os.path.join(self.out, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=1, allow_nan=False)
            fh.write("\n")
        with open(os.path.join(self.out, "manifest.json"), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()


def node_entry(f, i, name, s):
    """One node of the pack: its settings in the manifest, its arrays in nodes/<i>/."""
    unknown = set(s) - NODE_KEYS - INFO_KEYS
    if unknown:
        raise SystemExit(f"node {name}: fields the native engine does not know: {sorted(unknown)}")
    if s["rule"] not in STREAM_RULES:
        raise SystemExit(f"node {name}: rule {s['rule']!r} does not stream (the engine has {', '.join(STREAM_RULES)})")
    m = s["model"]
    unknown = set(m) - MODEL_KEYS
    if unknown:
        raise SystemExit(f"node {name}: model fields the native engine does not know: {sorted(unknown)}")
    d = f"nodes/{i:04d}"
    n_out = len(s["out_names"])
    var = s["rule"] == "cusumkv"
    params = m["params"]
    sd_bins = sorted(int(g) for g in s["sd"])
    e = {
        "name": name,
        "rule": s["rule"],
        "doc": _plain(s["doc"]),
        "num_in": list(s["num_in"]),
        "cats": [{"tag": t, "values": list(cs)} for t, cs in s["cats"].items()],
        "state_cmd": s["state_cmd"],
        "delta_in": list(s.get("delta_in", [])),
        "counter_resets": bool(s.get("counter_resets", False)),     # StreamNode's getattr default
        "out_names": list(s["out_names"]),
        "out_cats": [{"tag": t, "values": list(cs)} for t, cs in s["out_cats"].items()],
        "clip": float(s["clip"]) if s.get("clip") else None,
        "confirm_s": float(s.get("confirm_s") or 0.0),
        "var_steady": float(s.get("var_steady") or 0.0),
        "h": float(s["h"]),
        "hv": float(s["hv"]) if var else None,
        "sd_bins": sd_bins,
        "arrays": {
            "k": f.array(f"{d}/k.npy", np.broadcast_to(np.asarray(s["k"], float), (n_out,))),
            "sd_all": f.array(f"{d}/sd_all.npy", s["sd_all"], float),
            "sd": [f.array(f"{d}/sd_{j}.npy", s["sd"][g], float) for j, g in enumerate(sd_bins)],
        },
        "model": {
            "inputs": list(m["inputs"]),
            "outputs": list(m["outputs"]),
            "taus": [float(x) for x in m["taus"]],
            "hidden": [int(x) for x in m["hidden"]],
            "mlp": [{"w": f.array(f"{d}/mlp_{j}_w.npy", W, float), "b": f.array(f"{d}/mlp_{j}_b.npy", b, float)}
                    for j, (W, b) in enumerate(params[:-1])],
            "linear": {"w": f.array(f"{d}/linear_w.npy", params[-1][0], float),
                       "b": f.array(f"{d}/linear_b.npy", params[-1][1], float)},
            "norm": {k: f.array(f"{d}/norm_{k}.npy", m["norm"][k], float) for k in NORM_KEYS},
            "cov_cols": (f.array(f"{d}/cov_cols.npy", m["cov_cols"], bool) if m.get("cov_cols") is not None else None),
        },
        "info": _plain({k: s[k] for k in sorted(INFO_KEYS & set(s))}),
    }
    if var:
        e["arrays"]["kv"] = f.array(f"{d}/kv.npy", np.broadcast_to(np.asarray(s["kv"], float), (n_out,)))
        e["arrays"]["var_d"] = f.array(f"{d}/var_d.npy", s["var_d"], float)
        e["arrays"]["var_skip"] = f.array(f"{d}/var_skip.npy", s["var_skip"], bool)
    return e


def export(pkl, out):
    with open(pkl, "rb") as fh:
        raw = fh.read()
    p = pickle.loads(raw)
    f = Folder(out)
    nodes = {name: node_entry(f, i, name, s) for i, (name, s) in enumerate(sorted(p["nodes"].items()))}
    sig2 = p.get("sig2")
    man = {
        "format": FORMAT,
        "version": VERSION,
        "campaign": p.get("campaign"),
        "calib_windows": p.get("calib_windows"),
        "source": {"pickle": os.path.basename(pkl), "sha256": hashlib.sha256(raw).hexdigest()},
        "exported_by": "native/tools/export_pack.py",
        "float": "float64",
        "constants": constants(),
        "wiring": _plain(p["wiring"]),
        "nodes": nodes,
        "sig2": (None if sig2 is None else
                 {n: f.array(f"sig2/{i:04d}.npy", sig2[n], float) for i, n in enumerate(sorted(sig2))}),
        "meta": _plain(p.get("meta")),
    }
    return man, f.manifest(man)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pickle", help="live-pack.pkl (python -m live.engine pack)")
    ap.add_argument("out", help="the pack folder to write")
    a = ap.parse_args(argv)
    man, sha = export(a.pickle, a.out)
    print(f"{a.out}: {len(man['nodes'])} nodes, {len(man['files'])} arrays, manifest sha256 {sha[:16]}")


if __name__ == "__main__":
    main()
