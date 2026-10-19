#!/usr/bin/env python3
"""Score every node of the plant with ONE method (ideas.md 12.1 requirement 2, 17.6; 2026-10-15).

This replaces the hand-made benches of eval/machines.py (one input list, model size, filter set
and phase input per machine, written by hand). Here nothing is per machine:

  - Inputs and outputs come from the plant's documents (plant/model/wiring.py): a node's model
    takes its feeds and its PLC commands and predicts its measures. A categorical command (a state,
    a phase) becomes one input per category seen in training.
  - One model size and one filter set for every node (twin/node_model.py defaults).
  - The residual spread is taken per command state when the node has a categorical command, else
    one spread per output: the same rule for all.
  - A machine can have several nodes ("<asset>:loop" per PLC control loop, "compressor-1:air"),
    a text output gives one output per category, and an input that is an output of the asset's own
    control loops does not count for coverage (LOG-117). Faults are scored per machine.
  - Detection (--rule, default `cusumk` since LOG-115): a two-sided CUSUM on each residual, with a
    k per output from the slow bias of the calibration days and a limit per node from the same days.
    The options: `gate`, today's engine gate (the 35th percentile of |z| in the last 120 s above
    3.5, so 79 of the 120 samples), as in eval/machines.py; `cusum` (k = 0.5); `both` (the gate or
    `cusum`).

Data: whole-plant runs with the site weather and the material flow (plant/verify_plant.py checks
that plant). Training runs, tuning runs, validation runs and test runs use separate seed ranges
and separate days of the year (`day_of_year`: blocks of 7 days in turn, every split in every
month). The test split runs only with --freeze. A change to the engine is kept only if it holds
on the validation split, not only on the tuning split it was made on.

First job (this file, 2026-10-15): false alarms per node in normal running across the seasons, on
the tuning and the validation splits. Fault trials come when the fault magnitude rule is decided
(ideas.md 16.2, faults.py).

    python -m eval.nodes --hours 6 --train 36 --runs 8 --workers 4
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import sys
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from model.plant import UtilitiesPlant                    # noqa: E402
from model.wiring import instrument_ranges, wiring        # noqa: E402
from model import faults as FR                            # noqa: E402
from model import fault_ranges as RANGES                  # noqa: E402
from model.instruments import Instruments                 # noqa: E402

FACTORY_SEED = int(os.environ.get("VISR_FACTORY", "0"))   # the instruments' calibration (VISR_FACTORY: another factory)
from twin.node_model import NodeModel                     # noqa: E402
from eval.metrics import wilson                           # noqa: E402

K_DEV, Q_GATE, WIN_S = 3.5, 0.35, 120.0
# The CUSUM rule (2026-10-17, --rule cusum). A two-sided CUSUM on each standardized residual. The
# reference value k = 0.5 is the textbook value for a shift of one standard deviation (Montgomery,
# Introduction to Statistical Quality Control). The textbook limits (h = 4 or 5) assume independent
# samples, and residuals at 1 s are not independent. So each node takes its limit h from its own
# calibration days: the split-conformal quantile of the per-day maxima at alpha (since LOG-118 the
# default is the largest maximum; before it, CUSUM_ALPHA, which with 12 days gave the same: with 12 days,
# the largest maximum), never below the textbook h = 5.
CUSUM_K, CUSUM_ALPHA, CUSUM_H_MIN = 0.5, 0.1, 5.0
# Two variants (2026-10-17), because the limits of plain CUSUM came out large (up to 30,707): on
# normal days most residuals have a slow bias, which the sums collect for hours.
#   cusumk: k per output from that bias. k = the CUSUM_BIAS_Q quantile of |mean z| over windows of
#           CUSUM_BIAS_S on the calibration days, never below CUSUM_K. A shift smaller than the
#           normal bias then cannot build up. The window (the slow thermal time constant of TAUS)
#           and the quantile are project choices.
#   both:   the gate or plain CUSUM, the combined Shewhart-CUSUM scheme (Lucas, 1982): the gate for
#           a large sudden shift, CUSUM for a small slow one.
CUSUM_BIAS_S, CUSUM_BIAS_Q = 1800.0, 0.99
#   cusumkv: cusumk, and also a one-sided CUSUM on the variance of each residual (2026-10-18), for a
#           fault that adds noise and no shift (a worn welding contact tip, F5). The residual less
#           its 60 s mean, squared and divided by its calibration variance, less 1, is the
#           statistic; k and h come from the calibration days by the same rules as cusumk.
VAR_WIN_S = 60.0
# the variance statistic leaves out this many seconds after a change of the node's PLC state
# (VISR_VAR_STEADY_S; 0, the default, keeps every sample: a 60 s mask hid the 45 s welds, LOG-118)
VAR_STEADY_S = float(os.environ.get("VISR_VAR_STEADY_S", "0"))
HIDDEN = tuple(int(x) for x in os.environ.get("VISR_HIDDEN", "32,32").split(","))   # one size for every node
RULES = ("gate", "cusum", "cusumk", "both", "cusumkv")
DEFAULT_RULE = "cusumkv"    # since 2026-10-18 (LOG-118; cusumk from LOG-115): it held on the validation split
# The defaults of a campaign since LOG-118: 120 training days split "fit2" (24 fit days, 96 spread
# days) and the limit at the largest calibration-day maximum (alpha None): each node passes it on
# about 1 normal day in 97. On the days of LOG-116
# this gave more faults found, fewer false alarms and the root first more often than LOG-117's
# defaults, on the tuning and the validation split.
DEFAULT_TRAIN, DEFAULT_CALIB = 120, "fit2"     # 24 fit days, 96 spread days (8 per month)
# The root of a single fault (LOG-124, operator 2026-10-19): the counterfactual of eval/cf.py on the twin.
# It named 98 / 106 of 130 against 94 / 100 for the graph rule on factory 0 (98 / 100 against 90 / 93,
# 98 / 106 against 94 / 99 on factories 1 and 2), and DoWhy-GCM, the reference, agreed (LOG-124). The
# graph rule stays as the fallback (cf.py names its root when no reset removes any trouble), as the
# cross-check in the report, and as the rule for fault pairs.
DEFAULT_ROOT = "cf"
# One filter set for every node: the node model's defaults (5 s to 30 min) and a 2 h constant for the
# slow thermal states (an oil tank or a water tank behind a thermostat that cycles over about 30 min)
TAUS = (5.0, 30.0, 120.0, 600.0, 1800.0, 7200.0)
SAMPLE_S, DT = 1.0, 0.1
# The plant warm-up before a run is scored (2026-10-17, LOG-116): 3 x the slowest time constant of
# the plant, the top oil of the transformers (180 min, power.py, IEC 60076-7), so that about 5 % of
# the gap from the start state is left. It was 30 min, and the oil still heated through the run.
WARMUP_S = 3 * 180.0 * 60.0
# The end of the warm-up is recorded as a lead: it runs through the model's filters so that they
# start from history, as in a plant that runs all the time, and it is not fitted, calibrated or
# scored. Its length is the longest filter time constant (TAUS).
LEAD_S = 7200.0
SEED_BASE = {"train": 30_000, "tuning": 40_000, "validation": 60_000, "test": 940_000}

# The days of the year of the splits (2026-10-16). Before this, a hash of the seed picked the day,
# so the splits shared days: 6 of the 12 training days of campaign 6 were also tuning days. Now
# the year is cut into blocks of 7 days, and the blocks go to the splits in turn (blocked
# cross-validation for a time series). The last day of each block is a gap that no run starts
# on, so a run that crosses midnight stays in its own block. Day 364 is not used (its next day
# is 1 January). Each split gets 78 days, and 6 to 9 of them in every month.
SPLITS = ("train", "tuning", "validation", "test")
BLOCK_D = 7
# Run k of a split goes to month MONTHS[k % 12]: 4 runs fall in the 4 seasons, 12 runs in the 12
# months. Inside the month, the golden-ratio sequence on k // 12 picks the day.
MONTHS = (0, 6, 3, 9, 1, 7, 4, 10, 2, 8, 5, 11)
_MONTH_END = (31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365)   # 2025, not a leap year


def split_of(seed):
    """The split of a seed (the largest SEED_BASE at or below it), None below all of them."""
    got = [s for s in SPLITS if seed >= SEED_BASE[s]]
    return max(got, key=SEED_BASE.get) if got else None


def split_days(split, month):
    """The days of the year (0 = 1 January) on which a run of `split` can start in `month` (0..11)."""
    k = SPLITS.index(split)
    first = _MONTH_END[month - 1] if month else 0
    return [d for d in range(first, min(_MONTH_END[month], 364))
            if d % BLOCK_D != BLOCK_D - 1 and (d // BLOCK_D) % len(SPLITS) == k]


def day_of_year(seed):
    """The day of the year of a run: only days of its own split, spread over the months."""
    split = split_of(seed)
    if split is None:
        return None
    k = seed - SEED_BASE[split]
    days = split_days(split, MONTHS[k % 12])
    return days[int((((k // 12) * 0.6180339887498949) % 1.0) * len(days))]


def fit_and_calibration(train, rule="thirds"):
    """Split the training runs [(seed, run)] into the days the models fit on and the days that set
    the residual spreads. The training runs come in rounds of 12, one day per month (MONTHS).
    rule "thirds" (the default): every third round is for the spreads: with 36 runs, 24 days fit
    (2 per month) and 12 days set the spreads (1 per month), so both cover the year.
    rule "fit2" (2026-10-18): the first two rounds fit and every later round sets the spreads: with
    60 runs, 24 fit days and 36 spread days (3 per month), for steadier limits.
    With fewer than 3 rounds, the last quarter of the runs sets the spreads, as before 2026-10-17
    (those months are then missing from the fit)."""
    rnd = {sd: (sd - SEED_BASE["train"]) // 12 for sd, _ in train}
    fit_rounds = int(rule[3:]) if rule.startswith("fit") else None      # "fit2", "fit3": rounds that fit
    is_cal = (lambda r: r >= fit_rounds) if fit_rounds else (lambda r: r % 3 == 2)
    calib = [x for x in train if is_cal(rnd[x[0]])]
    if fit_rounds and calib:
        return [x for x in train if not is_cal(rnd[x[0]])], calib
    if not calib:
        n = max(1, len(train) // 4)
        return train[:-n], train[-n:]
    return [x for x in train if rnd[x[0]] % 3 != 2], calib


INSTRUMENTS = os.environ.get("VISR_INSTRUMENTS", "1") != "0"   # read through virtual instruments


# ------------------------------------------------------------------------------- the runs --
# The day cache (2026-10-16). A run's plant tags depend only on the plant code, the site weather
# file, the seed, the length and the fault, not on the engine. So each run's exact tags (every tag,
# before the instruments) are saved once, and a later campaign with the same key loads them instead
# of simulating. The documents (wiring.py) and the instruments (instruments.py) are applied at load
# time, so a change to them does not need a new simulation. VISR_CACHE=0 turns the cache off;
# VISR_CACHE_DIR moves it (default eval/cache/, gitignored and kept out of Syncthing).
CACHE = os.environ.get("VISR_CACHE", "1") != "0"
CACHE_DIR = os.environ.get("VISR_CACHE_DIR", os.path.join(ROOT, "eval", "cache"))
APPLIED_AT_LOAD = {"instruments.py", "wiring.py"}       # plant/model files read only at load time


_VERSION = []


def plant_version():
    """A hash of everything that decides a run's plant tags: the plant model's source and data files
    (except those applied at load), the weather file and the source of `simulate`."""
    if _VERSION:
        return _VERSION[0]
    import hashlib
    import inspect
    from model import weather as WX
    h = hashlib.sha256()
    mdir = os.path.join(ROOT, "plant", "model")
    for f in sorted(os.listdir(mdir)):
        if f.endswith((".py", ".json")) and f not in APPLIED_AT_LOAD:
            h.update(f.encode())
            with open(os.path.join(mdir, f), "rb") as fh:
                h.update(fh.read())
    wpath = os.environ.get("VISR_WEATHER", WX.DEFAULT_FILE)
    if os.path.exists(wpath):
        with open(wpath, "rb") as fh:
            h.update(fh.read())
    for fn in (simulate, new_plant, split_of, split_days, day_of_year):    # the plant a seed gets
        h.update(inspect.getsource(fn).encode())
    h.update(repr((DT, SAMPLE_S, WARMUP_S, LEAD_S, SEED_BASE, BLOCK_D, MONTHS)).encode())
    _VERSION.append(h.hexdigest()[:16])
    return _VERSION[0]


def _cache_path(seed, hours, fault, version):
    import hashlib
    key = repr((seed, float(hours), tuple(fault) if fault else None, version))
    return os.path.join(CACHE_DIR, f"{seed}-{hashlib.sha256(key.encode()).hexdigest()[:20]}.npz"), key


def _save(path, key, raw, info):
    """Every tag as a typed array (float, int, bool or str); a tag with mixed types as JSON. Exact:
    the load gives back the same Python values."""
    arrs, kinds = {}, {}
    for i, (t, vals) in enumerate(raw.items()):
        ty = {type(v) for v in vals}
        if ty <= {float}:
            arrs[f"a{i}"], kinds[t] = np.asarray(vals, np.float64), ("a", i)
        elif ty <= {bool} or ty <= {int} or ty <= {str}:
            arrs[f"a{i}"], kinds[t] = np.asarray(vals), ("a", i)
        else:
            kinds[t] = ("j", json.dumps(vals))
    meta = json.dumps({"key": key, "info": info, "kinds": kinds})
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp.npz"
    np.savez_compressed(tmp, meta=np.asarray(meta), **arrs)
    os.replace(tmp, path)


def _load(path, key):
    with np.load(path) as z:
        meta = json.loads(str(z["meta"]))
        if meta["key"] != key:
            return None
        raw = {t: (z[f"a{k[1]}"].tolist() if k[0] == "a" else json.loads(k[1])) for t, k in meta["kinds"].items()}
    return raw, meta["info"]


def new_plant(seed):
    return UtilitiesPlant(seed=seed, t_day0_h=(seed * 7.37) % 24.0, weather="site",
                          day_of_year=day_of_year(seed))


def simulate(seed, hours, fault=None):
    """The plant part of a run: ({tag: list of exact values}, info), every tag sampled every SAMPLE_S
    after the warm-up. fault: (fid, severity, onset_s after the warm-up) or None. The magnitude comes
    from the registry's range rule (fault_ranges.py); binary faults are on at the onset."""
    p = new_plant(seed)
    raw = {}
    every = int(round(SAMPLE_S / DT))
    n = int((hours * 3600.0 + WARMUP_S) / DT)
    info = {"seed": seed}
    on_at = off_at = None
    if fault:
        fid, sev, onset = fault
        mag = 1.0 if fid in FR.BINARY else RANGES.magnitude(fid, sev, RANGES.ends(p))
        on_at = WARMUP_S + onset
        d = FR.duration_s(fid, mag)
        off_at = on_at + d if d else None
        info.update({"fid": fid, "severity": sev, "magnitude": mag, "onset_s": onset,
                     "root": FR.FAULTS[fid].node, "control": FR.FAULTS[fid].control})
    applied = cleared = False
    m = 0                                       # samples taken
    lead = 0                                    # of them before the warm-up ends
    rec_from = WARMUP_S - min(LEAD_S, WARMUP_S)
    for k in range(n):
        if on_at is not None and not applied and p.t >= on_at:
            FR.inject(p, fault[0], mag)
            applied = True
        if off_at is not None and applied and not cleared and p.t >= off_at:
            FR.clear(p, fault[0])
            cleared = True
        p.step(DT)
        if p.t >= rec_from and k % every == 0:
            tags = p.tags()
            for t in tags.keys() - raw.keys():  # a tag that appears late: absent before
                raw[t] = [None] * m
            for t, col in raw.items():
                col.append(tags.get(t))
            m += 1
            lead += p.t < WARMUP_S
    info["lead"] = lead
    return raw, info


def simulate_multi(seed, hours, faults):
    """`simulate` with several faults at once (a fault pair, ideas.md 11.9): faults is a tuple of
    (fid, severity, onset_s after the warm-up). Each fault is injected at its own onset and, for a
    transient fault, cleared after its duration. info lists every fault; "onset_s" is the first."""
    p = new_plant(seed)
    raw = {}
    every = int(round(SAMPLE_S / DT))
    n = int((hours * 3600.0 + WARMUP_S) / DT)
    ends = RANGES.ends(p)
    plan = []
    for fid, sev, onset in faults:
        mag = 1.0 if fid in FR.BINARY else RANGES.magnitude(fid, sev, ends)
        d = FR.duration_s(fid, mag)
        plan.append({"fid": fid, "severity": sev, "magnitude": mag, "onset_s": onset,
                     "root": FR.FAULTS[fid].node, "control": FR.FAULTS[fid].control,
                     "_on": WARMUP_S + onset, "_off": WARMUP_S + onset + d if d else None})
    info = {"seed": seed, "fid": "+".join(f["fid"] for f in plan), "onset_s": min(f["onset_s"] for f in plan),
            "root": None, "control": False}
    applied, cleared = set(), set()
    m = lead = 0
    rec_from = WARMUP_S - min(LEAD_S, WARMUP_S)
    for k in range(n):
        for i, f in enumerate(plan):
            if i not in applied and p.t >= f["_on"]:
                FR.inject(p, f["fid"], f["magnitude"])
                applied.add(i)
            if f["_off"] is not None and i in applied and i not in cleared and p.t >= f["_off"]:
                FR.clear(p, f["fid"])
                cleared.add(i)
        p.step(DT)
        if p.t >= rec_from and k % every == 0:
            tags = p.tags()
            for t in tags.keys() - raw.keys():
                raw[t] = [None] * m
            for t, col in raw.items():
                col.append(tags.get(t))
            m += 1
            lead += p.t < WARMUP_S
    info["faults"] = [{k: v for k, v in f.items() if not k.startswith("_")} for f in plan]
    info["lead"] = lead
    return raw, info


def multi_version():
    import hashlib
    import inspect
    return "-multi-" + hashlib.sha256(inspect.getsource(simulate_multi).encode()).hexdigest()[:8]


def record(seed, hours, fault=None):
    """One whole-plant run as the engine sees it: (wiring, {tag: list of values}, info). The plant
    tags come from the cache when the key matches, else from `simulate` (and are cached); the
    documents and the instruments are applied here."""
    got = None
    multi = bool(fault) and isinstance(fault[0], (tuple, list))
    if CACHE:
        path, key = _cache_path(seed, hours, fault, plant_version() + (multi_version() if multi else ""))
        if os.path.exists(path):
            got = _load(path, key)
    if got is None:
        got = simulate_multi(seed, hours, fault) if multi else simulate(seed, hours, fault)
        if CACHE:
            _save(path, key, *got)
    raw, info = got
    p = new_plant(seed)                         # not stepped: the documents and the instrument ranges
    w = wiring(p)
    need = sorted({t for n in w.values() for role in ("feeds", "commands", "measures") for t in n[role]})
    commands = {t for n in w.values() for t in n["commands"]}
    inst = Instruments(FACTORY_SEED, noise=INSTRUMENTS, ranges=instrument_ranges(p))
    n = len(next(iter(raw.values())))
    rows = {}
    for t in need:                              # each instrument draws its noise from its own stream
        col = raw.get(t, [None] * n)
        rows[t] = col if (t in commands or not INSTRUMENTS) else inst.read_column(t, col, seed)
    return w, {t: _array(v) for t, v in rows.items()}, info


def _array(vals):
    """A column as a numpy array: 8 bytes per value instead of about 32 in a list of Python floats
    (a campaign holds every run in memory: 348 runs of 6 h were 12 GB as lists). Text states stay
    text (np.str_ is a str); a number column becomes float64, as Node.matrices reads it anyway."""
    if all(isinstance(v, str) for v in vals):
        return np.asarray(vals)
    if not any(isinstance(v, str) for v in vals):
        return np.asarray(vals, dtype=np.float64)
    return np.asarray(vals, dtype=object)


# --------------------------------------------------------------------- the engine view --
# The engine view of a run (2026-10-17): the tags of record(), after the documents and the
# instruments, saved once per run in VIEW_DIR. A campaign then holds only the paths, and each node
# worker loads only its own tags, one run at a time when it scores (a campaign of 372 runs held
# 4.2 GB in the main process and a copy of every node's tags in each worker). The values are the
# ones record() gives, so a report is the same.

class Text:
    """A text state column as integer codes: `codes[i]` indexes `names`, -1 for no value. 2 bytes
    per sample instead of 4 per character. Node reads it through is_text, text_cats and text_eq."""
    __slots__ = ("codes", "names")

    def __init__(self, codes, names):
        self.codes, self.names = np.asarray(codes, np.int16), tuple(names)

    @classmethod
    def encode(cls, vals):
        vals = np.asarray(vals)
        names = sorted({str(v) for v in vals.tolist() if isinstance(v, str)})
        index = {n: i for i, n in enumerate(names)}
        return cls([index.get(v, -1) if isinstance(v, str) else -1 for v in vals.tolist()], names)

    def decode(self):
        out = np.asarray([self.names[c] if c >= 0 else None for c in self.codes.tolist()], dtype=object)
        return out.astype(str) if (self.codes >= 0).all() else out

    def __len__(self):
        return len(self.codes)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return Text(self.codes[k], self.names)
        c = int(self.codes[k])
        return self.names[c] if c >= 0 else None


def is_text(v):
    if isinstance(v, Text):
        return True
    return any(isinstance(x, str) for x in v[:2000])


def text_cats(v):
    """The text values that occur in a column."""
    if isinstance(v, Text):
        return {v.names[i] for i in np.unique(v.codes).tolist() if i >= 0}
    return set(np.asarray(v).tolist())


def text_eq(v, c):
    """A boolean array: where the column has the value c."""
    if isinstance(v, Text):
        return v.codes == v.names.index(c) if c in v.names else np.zeros(len(v), bool)
    return np.asarray(v) == c


def _view_dir():
    return os.environ.get("VISR_VIEW_DIR", os.path.join(CACHE_DIR, "view"))


def view_version():
    """A hash of what decides the engine view beyond the plant tags: the documents, the
    instruments, the encoding and the instrument settings."""
    import hashlib
    import inspect
    h = hashlib.sha256(plant_version().encode())
    mdir = os.path.join(ROOT, "plant", "model")
    for f in sorted(APPLIED_AT_LOAD):
        with open(os.path.join(mdir, f), "rb") as fh:
            h.update(fh.read())
    for fn in (record, _array, Text.encode, save_view):
        h.update(inspect.getsource(fn).encode())
    h.update(repr((FACTORY_SEED, INSTRUMENTS)).encode())
    return h.hexdigest()[:16]


def save_view(path, key, w, rows, info):
    arrs, kinds = {}, {}
    for i, (t, v) in enumerate(rows.items()):
        if v.dtype.kind in "USO":
            tx = Text.encode(v)
            arrs[f"a{i}"], kinds[t] = tx.codes, ("t", i, list(tx.names))
        else:
            arrs[f"a{i}"], kinds[t] = v, ("a", i)
    meta = json.dumps({"key": key, "info": info, "wiring": w, "kinds": kinds})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp.npz"
    np.savez_compressed(tmp, meta=np.asarray(meta), **arrs)
    os.replace(tmp, path)


def _view_meta(path):
    with np.load(path) as z:
        return json.loads(str(z["meta"]))


def view(seed, hours, fault=None):
    """The engine view of a run on disk: (wiring, path, info). Made from record() once, then reused
    while its key matches."""
    import hashlib
    multi = bool(fault) and isinstance(fault[0], (tuple, list))
    key = repr((seed, float(hours), tuple(fault) if fault else None,
                view_version() + (multi_version() if multi else "")))
    path = os.path.join(_view_dir(), f"{seed}-{hashlib.sha256(key.encode()).hexdigest()[:20]}.npz")
    if os.path.exists(path):
        meta = _view_meta(path)
        if meta["key"] == key:
            return meta["wiring"], path, meta["info"]
    w, rows, info = record(seed, hours, fault)
    save_view(path, key, w, rows, info)
    return w, path, info


def load_view(path, tags=None):
    """A Run {tag: column} of a saved view, only `tags` when given, with its lead. Numbers as
    float64 arrays, text states as Text."""
    with np.load(path) as z:
        meta = json.loads(str(z["meta"]))
        kinds = meta["kinds"]
        out = {}
        for t in (tags if tags is not None else kinds):
            k = kinds[t]
            a = z[f"a{k[1]}"]
            out[t] = Text(a, k[2]) if k[0] == "t" else a
    return Run(out, meta["info"].get("lead", 0))


class RunsModel(NodeModel):
    """NodeModel over several runs: the filter bank restarts at each run (a run is a separate day).
    `leads[i]` samples at the start of run i only warm the filters: their rows are dropped from the
    features, so the outputs passed with them start after the lead."""
    bounds = None
    leads = None
    cov_cols = None     # the feature columns that count for coverage (None: all)

    def _covered_x(self, X, margin):
        if self.cov_cols is None:
            return super()._covered_x(X, margin)
        c = np.asarray(self.cov_cols, bool)
        if not c.any():
            return np.ones(len(X), bool)
        span = self.norm["xhi"][c] - self.norm["xlo"][c]
        lo, hi = self.norm["xlo"][c] - margin * span, self.norm["xhi"][c] + margin * span
        return np.all((X[:, c] >= lo) & (X[:, c] <= hi), axis=1)

    def features(self, U, dt):
        if not self.bounds:
            return super().features(U, dt)
        out, i = [], 0
        for n, lead in zip(self.bounds, self.leads or [0] * len(self.bounds)):
            out.append(super().features(U[i:i + n], dt)[lead:])
            i += n
        return np.vstack(out)


class Run(dict):
    """The tags of one run, {tag: column}, and `lead`: the samples at its start that only warm the
    model's filters (the end of the plant warm-up). They are not fitted, calibrated or scored."""

    def __init__(self, cols, lead=0):
        super().__init__(cols)
        self.lead = int(lead)


def lead_of(r):
    return getattr(r, "lead", 0)


class Node:
    def __init__(self, name, doc, rule=DEFAULT_RULE, alpha=None, limit="conformal", clip=None):
        if rule not in RULES:
            raise ValueError(f"unknown alarm rule {rule!r}")
        self.name, self.doc, self.rule = name, doc, rule
        self.alpha, self.limit = alpha, limit
        self.clip = clip                # robust CUSUM: |z| is capped at clip before it enters a sum
        self.confirm_s = 0.0            # an alarm counts once it has lasted this long (LOG-118)
        self.hidden = HIDDEN            # the network's hidden layers (twin/node_model.py)
        self.var_steady = VAR_STEADY_S   # seconds of the variance statistic left out after a state change
        self.h = None                   # the CUSUM limit, from the calibration days
        self.k = None                   # the CUSUM reference value (per output for cusumk)
        self.cats = {}                  # categorical command -> sorted categories seen in training
        self.out_cats = {}              # categorical output (a controller's state) -> its categories
        self.out_names = list(doc["measures"])
        self.model = None
        self.sd = {}
        self.sd_all = None
        self.state_cmd = None

    def design(self, runs):
        """Input names from the documents: numeric feeds and commands as they are, categorical
        commands one-hot over the categories of the training runs."""
        self.num_in = []
        for t in self.doc["feeds"] + self.doc["commands"]:
            if any(is_text(r[t]) for _, r in runs):
                cats = sorted(set().union(*(text_cats(r[t]) for _, r in runs)))
                self.cats[t] = cats
                if self.state_cmd is None:
                    self.state_cmd = t
            else:
                self.num_in.append(t)
        names = list(self.num_in) + [f"{t}={c}" for t, cs in self.cats.items() for c in cs]
        # a categorical output (a controller's state): one output per category, 1 where it holds
        self.out_names = []
        for t in self.doc["measures"]:
            if any(is_text(r[t]) for _, r in runs):
                self.out_cats[t] = sorted(set().union(*(text_cats(r[t]) for _, r in runs)))
                self.out_names += [f"{t}={c}" for c in self.out_cats[t]]
            else:
                self.out_names.append(t)
        return names

    def coverage_columns(self, names, n_tau):
        """The feature columns that count for coverage. An input that is an output of the asset's own
        PLC control loops does not count: when a fault drives the machine's own controller where it
        never went, that is a symptom, not a condition the model has not seen (LOG-117). The features
        are the inputs, then each input's filters (twin/node_model.py, input-major).
        A feedback input (`feedback_inputs`) does not count either (LOG-118): its node can be reached
        from this node's outputs through the documents, so this node's own fault can drive it out of
        range (a cooling pump that fails heats the water a machine returns to it)."""
        own = set(self.doc.get("loop_outputs", [])) | set(self.doc.get("feedback_inputs", []))
        keep = [n.split("=", 1)[0] not in own for n in names]
        return keep + [k for k in keep for _ in range(n_tau)]

    def matrices(self, r):
        cols = [np.asarray(r[t], float) for t in self.num_in]
        for t, cs in self.cats.items():
            for c in cs:
                cols.append(text_eq(r[t], c).astype(float))
        U = np.column_stack(cols) if cols else np.zeros((len(r[self.doc["measures"][0]]), 0))
        ys = []
        for t in self.doc["measures"]:
            if t in self.out_cats:
                ys += [text_eq(r[t], c).astype(float) for c in self.out_cats[t]]
            else:
                ys.append(np.asarray(r[t], float))
        return U, np.column_stack(ys)

    def bins(self, r):
        if self.state_cmd is None:
            return np.zeros(len(r[self.doc["measures"][0]]), int)
        v = r[self.state_cmd]
        out = np.full(len(v), -1)
        for i, c in enumerate(self.cats[self.state_cmd]):
            out[text_eq(v, c)] = i
        return out

    def train(self, runs, calib):
        """Fit on `runs`; take the residual spreads and the fit quality on `calib`, days the model
        has not seen (in-sample residuals are small by construction and make every new day alarm)."""
        names = self.design(runs + calib)
        Us, Ys = zip(*(self.matrices(r) for _, r in runs))
        leads = [lead_of(r) for _, r in runs]
        self.model = RunsModel(names, self.out_names, taus=TAUS, hidden=self.hidden)
        self.model.cov_cols = self.coverage_columns(names, len(TAUS))
        self.model.bounds, self.model.leads = [len(u) for u in Us], leads
        self.model.fit(np.vstack(Us), np.vstack([y[n:] for y, n in zip(Ys, leads)]), SAMPLE_S,
                       epochs=150, patience=15)
        self.model.bounds = self.model.leads = None
        res, bins, ys, covs = [], [], [], []
        for _, r in calib:
            rr, cv, Y, b = self._residuals(r)
            covs.append(cv)
            res.append(rr)
            bins.append(b)
            ys.append(Y)
        res, bins, ys, cov = np.vstack(res), np.concatenate(bins), np.vstack(ys), np.concatenate(covs)
        self.calib_covered = float(cov.mean())
        # the spreads from the covered samples when there are enough of them, else from all (a
        # calibration day outside the training range would leave no residual and a NaN spread,
        # which never alarms: a silent failure)
        if cov.sum() >= 600:
            res, bins, ys = res[cov], bins[cov], ys[cov]
        self.sd_all = res.std(0) + 1e-9
        for g in np.unique(bins):
            m = bins == g
            self.sd[int(g)] = (res[m].std(0) + 1e-9) if m.sum() > 60 else self.sd_all
        self.r2 = [float(1.0 - res[:, j].var() / max(ys[:, j].var(), 1e-12)) for j in range(ys.shape[1])]
        if not np.all(np.isfinite(self.sd_all)):
            raise ValueError(f"{self.name}: no finite residual spread from the calibration days")
        if self.rule != "gate":
            # k and the limit from the same calibration days, with the spreads just set
            zs = [self.z(r) for _, r in calib]
            self.k = slow_bias_k(zs) if self.rule in ("cusumk", "cusumkv") else CUSUM_K
            self.h = cusum_limit([cusum(z, cv, self.k).max(initial=0.0) for z, cv in zs], self.alpha,
                                 method=self.limit)
            if self.rule == "cusumkv":
                own = set(self.doc.get("loop_outputs", []))
                self.var_skip = np.array([n.split("=", 1)[0] in own for n in self.out_names], bool)
                # the calibration variance from the samples the statistic uses (steady operation)
                self.var_d = None
                xs, oks = [], []
                for _, r in calib:              # steady_mask reads the state of the same run (self._bins)
                    z, cv = self.z(r)
                    x = z - rolling_mean(z, cv, int(VAR_WIN_S / SAMPLE_S))
                    oks.append(cv[:, None] & np.isfinite(x) & ~self.steady_mask(len(x))[:, None])
                    xs.append(x)
                allx, ok = np.concatenate(xs), np.concatenate(oks)
                self.var_d = np.array([np.var(allx[ok[:, j], j]) if ok[:, j].any() else 1.0
                                       for j in range(allx.shape[1])]) + 1e-12
                us = []
                for _, r in calib:
                    z, cv = self.z(r)
                    us.append((self.var_stat(z, cv), cv))
                self.kv = slow_bias_k(us)
                self.hv = cusum_limit([cusum_up(u, cv, self.kv).max(initial=0.0) for u, cv in us], self.alpha,
                                      method=self.limit)
        return self

    def var_stat(self, z, cov):
        """The variance statistic per output: the residual less its 60 s mean, squared, divided by its
        calibration variance, less 1. Not for a controller output (an output of the asset's own
        control loops, such as a valve or a load state): its residual is a switching error, and its
        spread says nothing about noise (LOG-118). Those columns are NaN, so the sums hold."""
        x = z - rolling_mean(z, cov, int(VAR_WIN_S / SAMPLE_S))
        u = x * x / self.var_d - 1.0
        if self.var_skip.any():
            u[:, self.var_skip] = np.nan
        # noise is judged in steady operation only (LOG-118): not within VAR_WIN_S after a change of
        # the node's PLC state, where the model's filters still catch up (more state changes per hour,
        # a heavier schedule, is not more noise)
        u[self.steady_mask(len(u))] = np.nan
        return u

    def steady_mask(self, n_samples):
        """True within var_steady seconds after a change of the node's PLC state (of the run last passed to z):
        the samples the variance statistic leaves out."""
        mask = np.zeros(n_samples, bool)
        b = getattr(self, "_bins", None)
        if not self.var_steady or self.state_cmd is None or b is None or len(b) != n_samples:
            return mask
        n = int(self.var_steady / SAMPLE_S)
        for i in np.flatnonzero(b[1:] != b[:-1]) + 1:
            mask[i:i + n] = True
        return mask

    def _residuals(self, r):
        """Residuals and coverage of one run after its lead (the filters run through the lead), with
        the outputs and the state bins of the same samples."""
        U, Y = self.matrices(r)
        n = lead_of(r)
        self.model.bounds, self.model.leads = [len(U)], [n]
        try:
            res, cov = self.model.residuals_covered(U, Y[n:], SAMPLE_S)
        finally:
            self.model.bounds = self.model.leads = None
        return res, cov, Y[n:], self.bins(r)[n:]

    def predict(self, r):
        """The model's prediction of the outputs of run r after its lead (T x outputs) and the covered
        mask (T): the measured outputs less the residuals. The twin reads it (twin/twin.py)."""
        res, cov, Y, _ = self._residuals(r)
        return Y - res, cov

    def state(self):
        """The trained node as plain data (no class reference, so a file written by `python -m
        eval.nodes` loads in another program): eval/cf.py rebuilds it with `from_state`."""
        s = {k: v for k, v in self.__dict__.items() if k not in ("model", "_stats", "_bins")}
        s["model"] = dict(self.model.__dict__)
        return s

    @classmethod
    def from_state(cls, s):
        nd = cls.__new__(cls)
        nd.__dict__.update({k: v for k, v in s.items() if k != "model"})
        nd.model = RunsModel.__new__(RunsModel)
        nd.model.__dict__.update(s["model"])
        return nd

    def z(self, r):
        """The standardized residuals of a run after its lead (T x outputs) and the covered mask (T),
        capped at +-clip when the node has a clip (the CUSUM rules only; the gate keeps them)."""
        res, cov, _, b = self._residuals(r)
        self._bins = b                  # the PLC state per sample, for the variance statistic
        sd = np.stack([self.sd.get(int(g), self.sd_all) for g in b])
        z = res / sd
        if self.clip and self.rule != "gate":
            z = np.clip(z, -self.clip, self.clip)
        return z, cov

    def alarms(self, r, with_cover=False):
        """Alarms per sample, by the node's rule. Outside the training range (with a 10 % margin) the
        node does not know: no alarm there; `with_cover` also returns the covered mask."""
        z, cov = self.z(r)
        self._stats = []                # the CUSUM statistics and their limits, for change_point
        if self.rule == "gate":
            out = gate(np.abs(z))
        else:
            sms = cusum(z, cov, self.k, cols=True)
            self._stats.append((sms, self.h))
            out = cusum_alarm(sms.max(axis=1) if sms.size else np.zeros(len(cov)), self.h)
            if self.rule == "cusumkv":
                svs = cusum_up(self.var_stat(z, cov), cov, self.kv, cols=True)
                self._stats.append((svs, self.hv))
                out |= cusum_alarm(svs.max(axis=1) if svs.size else np.zeros(len(cov)), self.hv)
            if self.rule == "both":
                out |= gate(np.abs(z))
        out &= cov
        if self.confirm_s:
            out = confirm(out, int(self.confirm_s / SAMPLE_S))
        return (out, cov) if with_cover else out


def confirm(alarm, n):
    """The alarm from the sample at which it has been on for n samples in a row (the first n - 1
    samples of each episode drop out; an episode shorter than n samples drops out)."""
    if n <= 1:
        return alarm
    a = np.asarray(alarm, bool)
    run = np.zeros(len(a), int)
    c = 0
    for i, v in enumerate(a):
        c = c + 1 if v else 0
        run[i] = c
    return run >= n


def crossings(stats, start):
    """Every upward crossing of a limit by a single CUSUM sum (one side of one output) at or after
    `start`, with the change point of that sum: [(t, cp)], sorted. A machine that is already in alarm
    from one fault still shows a new change from a second fault this way (LOG-118, fault pairs)."""
    out = []
    for s, h in stats:
        s = np.asarray(s, float).reshape(len(s), -1)
        if not len(s) or not s.shape[1]:
            continue
        above = s > h
        up = above & ~np.vstack([np.zeros((1, s.shape[1]), bool), above[:-1]])
        for t, j in zip(*np.nonzero(up)):
            if t < start:
                continue
            zero = np.flatnonzero(s[:t + 1, j] <= 0.0)
            out.append((int(t), int(zero[-1]) + 1 if len(zero) else 0))
    return sorted(set(out))


def change_point(stats, t):
    """Page's estimate of when the change behind an alarm at sample t began: for each single CUSUM
    sum (one side of one output) that is in an episode at t (above 0, and above its limit at some
    sample since its last 0), the sample after that last 0; the earliest of them. t itself when no
    sum is in an episode (the gate rule). stats: [(sums T x m, or the largest T, limit)].
    (LOG-118 fix: the first version used the largest sum over the outputs, whose last 0 is the last
    sample with every sum at 0, far back on a node with many outputs.)"""
    best = None
    for s, h in stats:
        s = np.asarray(s, float).reshape(len(s), -1)
        if t >= len(s):
            continue
        for j in np.flatnonzero(s[t] > 0.0):
            col = s[:t + 1, j]
            zero = np.flatnonzero(col <= 0.0)
            c = int(zero[-1]) + 1 if len(zero) else 0
            if (col[c:] > h).any():
                best = c if best is None else min(best, c)
    return t if best is None else best


def feedback_inputs(w):
    """{node: [its inputs that are feedback]} from the documents. An input t of node A is feedback when
    the node that measures t can be reached from A: A -> B when an input of B is a measure of A."""
    meas_of = {t: n for n, d in w.items() for t in d["measures"]}
    succ = {n: set() for n in w}
    for n, d in w.items():
        for t in d["feeds"] + d["commands"]:
            m = meas_of.get(t)
            if m is not None and m != n:
                succ[m].add(n)
    out = {}
    for a, d in w.items():
        seen, todo = set(), [a]
        while todo:
            for b in succ[todo.pop()]:
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
        out[a] = [t for t in d["feeds"] + d["commands"] if meas_of.get(t) in seen and meas_of.get(t) != a]
    return out


def gate(a, w=None):
    """Today's gate, as a rolling count: the Q_GATE quantile (numpy, linear) of a window of w values
    is above K_DEV when at least w - floor(Q_GATE (w - 1)) of them are above K_DEV (then the two order
    statistics it interpolates are both above). One sample short of that, it depends on the values:
    the count rule calls it no alarm (conservative by one sample). O(n) instead of a sort per sample.
    a: T x n |z|; returns T booleans (any output)."""
    w = w or int(WIN_S / SAMPLE_S)
    need = w - int(np.floor(Q_GATE * (w - 1)))
    over = (a > K_DEV).astype(np.int32)
    c = np.cumsum(np.vstack([np.zeros((1, over.shape[1]), np.int32), over]), axis=0)
    out = np.zeros(len(a), bool)
    if len(a) >= w:
        cnt = c[w:] - c[:-w]
        out[w - 1:] = np.any(cnt >= need, axis=1)
    return out


def cusum(z, cov, k=CUSUM_K, cols=False):
    """The largest two-sided CUSUM statistic over the outputs, per sample (T). For each output:
    S+ = max(0, S+ + z - k) and S- = max(0, S- - z - k), from 0 at the start of the run. A sample
    outside the training range, or with no finite residual, adds nothing: the sums hold. Computed as
    the cumulative sum less its running minimum (the Lindley form of the same recursion)."""
    cov = np.asarray(cov, bool)
    z = np.asarray(z, float).reshape(len(cov), -1)
    if z.shape[1] == 0:
        return np.zeros((len(cov), 0)) if cols else np.zeros(len(cov))
    ok = cov[:, None] & np.isfinite(z)
    x = np.concatenate([np.where(ok, z - k, 0.0), np.where(ok, -z - k, 0.0)], axis=1)
    c = np.cumsum(x, axis=0)
    s = c - np.minimum.accumulate(np.minimum(c, 0.0), axis=0)
    return s if cols else s.max(axis=1)


def cusum_up(u, cov, k, cols=False):
    """The largest one-sided (upward) CUSUM over the outputs, per sample: S = max(0, S + u - k).
    Samples outside the training range, or not finite, hold the sums."""
    cov = np.asarray(cov, bool)
    u = np.asarray(u, float).reshape(len(cov), -1)
    if u.shape[1] == 0:
        return np.zeros((len(cov), 0)) if cols else np.zeros(len(cov))
    ok = cov[:, None] & np.isfinite(u)
    c = np.cumsum(np.where(ok, u - k, 0.0), axis=0)
    s = c - np.minimum.accumulate(np.minimum(c, 0.0), axis=0)
    return s if cols else s.max(axis=1)


def rolling_mean(z, cov, w):
    """The trailing mean of each column over the last w samples (covered, finite samples only; NaN
    until a window has half of them)."""
    z = np.asarray(z, float).reshape(len(cov), -1)
    ok = np.asarray(cov, bool)[:, None] & np.isfinite(z)
    s = np.cumsum(np.vstack([np.zeros((1, z.shape[1])), np.where(ok, z, 0.0)]), axis=0)
    c = np.cumsum(np.vstack([np.zeros((1, z.shape[1])), ok.astype(float)]), axis=0)
    i = np.arange(1, len(z) + 1)
    lo = np.maximum(0, i - w)
    ws, wc = s[i] - s[lo], c[i] - c[lo]
    return np.where(wc >= w / 2, ws / np.maximum(wc, 1.0), np.nan)


def slow_bias_k(zs, window_s=CUSUM_BIAS_S, q=CUSUM_BIAS_Q, k_min=CUSUM_K):
    """k per output from the slow bias of the calibration days: the q quantile of |mean z| over
    windows of window_s (sliding, one per sample, the covered and finite samples only, a window
    counts when at least half of it is), never below k_min. zs: [(z T x n, covered T)]."""
    w = int(window_s / SAMPLE_S)
    means = []
    for z, cov in zs:
        z = np.asarray(z, float).reshape(len(cov), -1)
        ok = np.asarray(cov, bool)[:, None] & np.isfinite(z)
        if len(z) < w:
            continue
        s = np.cumsum(np.vstack([np.zeros((1, z.shape[1])), np.where(ok, z, 0.0)]), axis=0)
        c = np.cumsum(np.vstack([np.zeros((1, z.shape[1])), ok.astype(float)]), axis=0)
        ws, wc = s[w:] - s[:-w], c[w:] - c[:-w]
        m = np.where(wc >= w / 2, np.abs(ws) / np.maximum(wc, 1.0), np.nan)
        means.append(m)
    if not means:
        return k_min
    m = np.vstack(means)
    k = np.array([np.nanquantile(m[:, j], q) if np.isfinite(m[:, j]).any() else k_min for j in range(m.shape[1])])
    return np.maximum(k, k_min)


def cusum_alarm(s, h):
    """The alarm per sample: on when the statistic passes h, off again when it falls back to 0."""
    idx = np.arange(len(s))
    last_on = np.maximum.accumulate(np.where(s > h, idx, -1)) if len(s) else idx
    last_off = np.maximum.accumulate(np.where(s <= 0.0, idx, -1)) if len(s) else idx
    return last_on > last_off


def cusum_limit(day_maxima, alpha=CUSUM_ALPHA, h_min=CUSUM_H_MIN, method="conformal"):
    """The limit from the calibration-day maxima of a CUSUM statistic, never below h_min.
    conformal: the ceil((n + 1)(1 - alpha))-th smallest of the n maxima (the largest when that index
    passes n), so a normal day passes it with a chance of at most about alpha.
    gumbel (2026-10-18): the 1 - alpha quantile of a Gumbel distribution fitted to the maxima by the
    method of moments (extreme value theory: the maximum of a long run). It can go past the largest
    maximum, for an alpha smaller than 1 / (n + 1)."""
    m = sorted(float(v) for v in day_maxima)
    if not m:
        return h_min
    if method == "gumbel" and len(m) >= 3 and alpha:
        beta = np.sqrt(6.0) * float(np.std(m, ddof=1)) / np.pi
        mu = float(np.mean(m)) - 0.5772156649 * beta
        return max(h_min, mu - beta * np.log(-np.log(1.0 - alpha)))
    if alpha is None:                   # the largest maximum: alpha < 1 / (n + 1)
        return max(h_min, m[-1])
    i = min(len(m), int(np.ceil((len(m) + 1) * (1.0 - alpha))))
    return max(h_min, m[i - 1])


def first_activity(rows, doc, start):
    """The first sample at or after `start` at which one of the node's state commands (a text tag
    from the documents, such as a phase) differs from its value at `start`: when the machine next
    changes what it does. `start` if the node has no state command or never changes."""
    for t in doc["commands"]:
        v = rows[t]
        if is_text(v) and start < len(v):
            c = v.codes if isinstance(v, Text) else np.asarray(v)
            idx = np.flatnonzero(c[start:] != c[start])
            return start + int(idx[0]) if len(idx) else start
    return start


def episodes(alarm):
    n, prev = 0, False
    for a in alarm:
        if a and not prev:
            n += 1
        prev = a
    return n


def first_alarm(alarm, start):
    idx = np.flatnonzero(alarm[start:])
    return int(idx[0]) + start if len(idx) else None


def node_job(name, doc, fit, calib, scored, rule=DEFAULT_RULE, alpha=None, limit="conformal",
             onsets=None, clip=None, confirm_s=0.0, keep=False):
    """One node in a worker process: train it, then its alarms and coverage on every scored run
    ({key: view path} -> {key: (alarm, covered)}), and what the report needs of the model. fit and
    calib: [(seed, view path)]. The node loads only its own tags, and a scored run only while it
    scores that run. onsets {key: sample}: for a fault run, the change point of the node's first
    alarm after the onset ("cp"). keep: also return the trained node as plain data ("state")."""
    t1 = time.time()
    tags = doc["feeds"] + doc["commands"] + doc["measures"]
    nd = Node(name, doc, rule, alpha, limit, clip)
    nd.confirm_s = confirm_s
    nd = nd.train([(sd, load_view(p, tags)) for sd, p in fit],
                                     [(sd, load_view(p, tags)) for sd, p in calib])
    alarms, cps, events = {}, {}, {}
    for k, p in scored.items():
        alarms[k] = nd.alarms(load_view(p, tags), with_cover=True)
        if onsets and k in onsets:
            fa = first_alarm(alarms[k][0], onsets[k])
            cps[k] = None if fa is None else change_point(nd._stats, fa)
            if k[1] == "pair":
                cov = alarms[k][1]
                events[k] = [(t, c) for t, c in crossings(nd._stats, onsets[k]) if cov[t]]
    return {"inputs": nd.model.inputs, "outputs": nd.model.outputs, "weights": nd.model.n_weights(),
            "r2": nd.r2, "calib_covered": nd.calib_covered, "h": nd.h, "hv": getattr(nd, "hv", None),
            "k": None if nd.k is None else np.atleast_1d(nd.k).astype(float).tolist(),
            "alarms": alarms, "cp": cps, "events": events, "wall_s": time.time() - t1,
            "state": nd.state() if keep else None}


GRAPH_WINDOWS_S = (120.0, 600.0)


def downstream(w):
    """{asset: the assets downstream of it in the documents}: A -> B when an input of a node of B is a
    measure of a node of A (another asset), and its closure."""
    asset = {n: d.get("asset", n) for n, d in w.items()}
    meas_of = {t: asset[n] for n, d in w.items() for t in d["measures"]}
    succ = {a: set() for a in asset.values()}
    for n, d in w.items():
        for t in d["feeds"] + d["commands"]:
            m = meas_of.get(t)
            if m is not None and m != asset[n]:
                succ[m].add(asset[n])
    out = {}
    for a in succ:
        seen, todo = set(), [a]
        while todo:
            for b in succ[todo.pop()]:
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
        out[a] = seen - {a}
    return out


def score_pair(al, info, asset, reach, events=None):
    """A trial with several faults (a fault pair). The engine names as roots the machines that alarm
    after the first onset, except a machine that an upstream machine (in the documents, not in a cycle
    with it) alarmed before, by at most 120 s. It passes when every real root (not the control cases)
    is named and alarmed after its own fault's onset (ideas.md 13.1: a pair passes only if the engine
    names both roots)."""
    io = int(info["onset_s"] / SAMPLE_S)
    hit = {}
    for n, a in al.items():
        i = first_alarm(a, io)
        m = asset.get(n, n)
        if i is not None and (m not in hit or i < hit[m]):
            hit[m] = i
    # a machine is a victim, not a root, when a machine upstream of it in the documents (not in a cycle
    # with it) alarmed at most GRAPH_WINDOWS_S[0] before it: a victim alarms after its cause
    wnd = GRAPH_WINDOWS_S[0] / SAMPLE_S
    named = sorted(m for m in hit if not any(m in reach.get(o, ()) and o not in reach.get(m, ())
                                             and 0 <= hit[m] - hit[o] <= wnd for o in hit if o != m))
    # a machine with a new change of its own (a crossing whose change began after the first onset,
    # with no upstream machine alarming or crossing in the 120 s before that change) is named too:
    # a second fault on a machine that the first fault had already set off (LOG-118 review)
    if events is not None:
        ev_by = {}
        for n, ev in events.items():
            ev_by.setdefault(asset.get(n, n), []).extend(ev)
        times_of = {m: ([hit[m]] if m in hit else []) + [t for t, _ in ev_by.get(m, [])] for m in set(hit) | set(ev_by)}
        for m, ev in ev_by.items():
            if m in named:
                continue
            for _, c in ev:
                if c < io:
                    continue
                if not any(m in reach.get(o, ()) and o not in reach.get(m, ()) and
                           any(c - wnd <= t <= c for t in times_of.get(o, ())) for o in times_of if o != m):
                    named = sorted(set(named) | {m})
                    break
    roots = [f["root"] for f in info["faults"] if not f["control"]]
    det = {f["root"]: (f["root"] in hit and hit[f["root"]] >= int(f["onset_s"] / SAMPLE_S))
           for f in info["faults"] if not f["control"]}
    # a second fault on a machine that the first one already set off: a sum of that machine that
    # crosses its limit with a change that began after this fault's onset (within the graph window)
    if events is not None:
        tol = GRAPH_WINDOWS_S[0] / SAMPLE_S
        for f in info["faults"]:
            if f["control"] or det.get(f["root"]):
                continue
            of = int(f["onset_s"] / SAMPLE_S)
            det[f["root"]] = any(c >= of - tol for n, ev in events.items() if asset.get(n, n) == f["root"]
                                 for _, c in ev)
    return {"fid": info["fid"], "seed": info["seed"], "faults": info["faults"], "alarmed": sorted(hit, key=hit.get),
            "named": named, "roots": roots, "detected": det, "all_detected": all(det.values()) if det else True,
            "passed": all(r in named and det[r] for r in roots),
            "pre_alarms": sum(episodes(a[:io]) for a in al.values())}


def score_fault(al, info, asset=None, cps=None, reach=None):
    """One fault trial: which asset alarmed first after the onset, was the true root among the
    alarms, how long it took, and the alarms before the onset (false). al: node -> alarm per sample;
    asset: node -> the machine it belongs to (a machine's first alarm is the first of its nodes);
    cps: node -> the change point of its first alarm after the onset (node_job), for a second root
    rule: the machine whose change began first ("first_cp", "top1_cp")."""
    io = int(info["onset_s"] / SAMPLE_S)
    asset = asset or {}
    hit, hit_cp = {}, {}
    for n, a in al.items():
        i = first_alarm(a, io)
        m = asset.get(n, n)
        if i is not None and (m not in hit or i < hit[m]):
            hit[m] = i
        c = (cps or {}).get(n)
        if i is not None and c is not None and (m not in hit_cp or c < hit_cp[m]):
            hit_cp[m] = c
    pre = sum(episodes(a[:io]) for a in al.values())
    rec = dict(info, pre_alarms=pre, alarmed=sorted(hit, key=hit.get))
    root = info["root"]
    if info["control"]:
        rec["false_nodes"] = len(hit)
        return rec
    rec["detected"] = root in hit
    rec["delay_s"] = (hit[root] - io) * SAMPLE_S if root in hit else None
    # from the first time the root machine changed what it does after the onset (a fault in a press
    # that stands idle does not act until the press moves); negative: it alarmed before that
    ia = int(info.get("active_s", info["onset_s"]) / SAMPLE_S)
    rec["delay_active_s"] = (hit[root] - ia) * SAMPLE_S if root in hit else None
    t0 = min(hit.values()) if hit else None
    rec["first"] = sorted(n for n, i in hit.items() if i == t0) if hit else []
    rec["top1"] = rec["first"] == [root]
    if hit_cp:
        c0 = min(hit_cp.values())
        rec["first_cp"] = sorted(n for n, c in hit_cp.items() if c == c0)
        rec["top1_cp"] = rec["first_cp"] == [root]
    # the engine names one root: the first machine to alarm; machines that alarm in the same second
    # are split by the earlier change point (LOG-118). "top1" stays the strict count (a tie is a miss).
    tied = rec["first"]
    if len(tied) > 1 and all(m in hit_cp for m in tied):
        cmin = min(hit_cp[m] for m in tied)
        tied = sorted(m for m in tied if hit_cp[m] == cmin)
    rec["named"] = tied
    rec["top1_named"] = tied == [root]
    # the graph rule: of the machines that alarm within W s of the first, drop those downstream of
    # another one (unless they are in a cycle with it), then name the earliest (ties by change point)
    if reach is not None and hit:
        for wnd in GRAPH_WINDOWS_S:
            cand = {m: i for m, i in hit.items() if i <= t0 + wnd / SAMPLE_S}
            keep = [m for m in cand if not any(m in reach.get(o, ()) and o not in reach.get(m, ())
                                                for o in cand if o != m)]
            ti = min(cand[m] for m in keep)
            pick = sorted(m for m in keep if cand[m] == ti)
            if len(pick) > 1 and all(m in hit_cp for m in pick):
                cmin = min(hit_cp[m] for m in pick)
                pick = sorted(m for m in pick if hit_cp[m] == cmin)
            rec[f"top1_g{int(wnd)}"] = pick == [root]
            rec[f"named_g{int(wnd)}"] = pick
        # the order-aware variant: a candidate drops out only when an upstream candidate alarmed
        # before it (a victim alarms after its cause), as in score_pair (LOG-118 review)
        wnd = GRAPH_WINDOWS_S[0]
        cand = {m: i for m, i in hit.items() if i <= t0 + wnd / SAMPLE_S}
        keep = [m for m in cand if not any(m in reach.get(o, ()) and o not in reach.get(m, ())
                                            and cand[o] <= cand[m] for o in cand if o != m)]
        ti = min(cand[m] for m in keep)
        pick = sorted(m for m in keep if cand[m] == ti)
        if len(pick) > 1 and all(m in hit_cp for m in pick):
            cmin = min(hit_cp[m] for m in pick)
            pick = sorted(m for m in pick if hit_cp[m] == cmin)
        rec["top1_g120o"] = pick == [root]
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--train", type=int, default=DEFAULT_TRAIN,
                    help="training runs (days); with --calib fit2, 120 = 24 to fit (2 per month) and 96 for the "
                         "spreads and limits (8 per month)")
    ap.add_argument("--runs", type=int, default=8, help="normal runs per scored split")
    ap.add_argument("--faults", default="", help="fault ids, for example F1,F2,F6 (registry, faults.py)")
    ap.add_argument("--fault-runs", type=int, default=10, help="runs per fault per split")
    ap.add_argument("--pairs", default="", help="fault pairs, for example F6+F13,F7+F8 (ideas.md 11.10)")
    ap.add_argument("--pair-runs", type=int, default=10, help="runs per pair per split")
    ap.add_argument("--splits", default="tuning,validation")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--rule", choices=RULES, default=DEFAULT_RULE,
                    help="the alarm rule on the residuals: gate (the 120 s window), cusum, cusumk (k per "
                         "output from the calibration bias, the default), both (the gate or cusum) or "
                         "cusumkv (cusumk and a CUSUM on the residual variance)")
    ap.add_argument("--calib", choices=("thirds", "fit2", "fit3", "fit4"), default=DEFAULT_CALIB,
                    help="the split of the training days into fit and spread days (fit_and_calibration)")
    ap.add_argument("--alpha", type=float, default=None,
                    help="the chance that a normal day passes a node's CUSUM limit (default: the largest "
                         "calibration-day maximum, alpha < 1 / (n + 1))")
    ap.add_argument("--limit", choices=("conformal", "gumbel"), default="conformal",
                    help="how the limit comes from the calibration-day maxima (cusum_limit)")
    ap.add_argument("--clip", type=float, default=None,
                    help="robust CUSUM: cap |z| at this value before it enters a sum (default: no cap)")
    ap.add_argument("--confirm", type=float, default=0.0,
                    help="an alarm counts once it has lasted this many seconds (default 0: at once)")
    ap.add_argument("--no-feedback-rule", action="store_true",
                    help="count feedback inputs for coverage, as before LOG-118 (feedback_inputs)")
    ap.add_argument("--no-added-loop-inputs", action="store_true",
                    help="leave out the loop outputs that wiring.py adds to a machine's own node")
    ap.add_argument("--root", choices=("cf", "graph"), default=DEFAULT_ROOT,
                    help="the rule that names the root of a single fault: cf, the counterfactual of eval/cf.py (the "
                         "default since LOG-124; it saves the models and runs after the scoring), or graph, the graph "
                         "rule only. The graph rule is always reported as the cross-check, and it names the roots of "
                         "fault pairs")
    ap.add_argument("--save-models", action="store_true",
                    help="also write models.pkl (the trained nodes, the documents and the scored views) for "
                         "eval/cf.py, the twin and the counterfactual root")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results"))
    a = ap.parse_args(argv)
    splits = [s.strip() for s in a.splits.split(",") if s.strip()]
    fids = [f.strip() for f in a.faults.split(",") if f.strip()]
    if a.root == "cf" and fids:
        a.save_models = True            # eval/cf.py reads the trained nodes
    pairs = [tuple(x.strip() for x in pr.split("+")) for pr in a.pairs.split(",") if pr.strip()]
    if "test" in splits and not a.freeze:
        ap.error("the test split is held out: run it only at the freeze, with --freeze")
    if a.hours + WARMUP_S / 3600.0 > 24.0:
        ap.error("a run longer than one day leaves its block of days (day_of_year)")
    t0 = time.time()
    jobs = [("train", SEED_BASE["train"] + i, None) for i in range(a.train)]
    for s in splits:
        jobs += [(s, SEED_BASE[s] + i, None) for i in range(a.runs)]
        for j, fid in enumerate(fids):
            for i in range(a.fault_runs):
                seed = SEED_BASE[s] + 1000 * (j + 1) + i
                r = np.random.default_rng(seed)
                onset = float(r.uniform(3600.0, max(3600.0, a.hours * 3600.0 - 3600.0)))
                jobs.append((s, seed, (fid, float(r.uniform(0.05, 1.0)), onset)))
        for j, pair in enumerate(pairs):
            for i in range(a.pair_runs):
                seed = SEED_BASE[s] + 17000 + 100 * j + i
                r = np.random.default_rng(seed)
                spec = tuple((fid, float(r.uniform(0.05, 1.0)),
                              float(r.uniform(3600.0, max(3600.0, a.hours * 3600.0 - 3600.0)))) for fid in pair)
                jobs.append((s, seed, spec))
    data = {}
    w = None
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(view, sd, a.hours, fl): (sp, sd, fl) for sp, sd, fl in jobs}
        for f in cf.as_completed(futs):
            sp, sd, fl = futs[f]
            w, path, info = f.result()
            kind = "normal" if not fl else ("pair" if isinstance(fl[0], (tuple, list)) else "fault")
            data.setdefault((sp, kind), []).append((sd, path, info))
            label = info.get("fid", "normal")
            print(f"run {sp} {sd} {label} done ({time.time() - t0:.0f} s)", flush=True)
    for k in data:
        data[k].sort(key=lambda x: x[0])
    train = [(sd, p) for sd, p, _ in data[("train", "normal")]]
    fit, calib = fit_and_calibration(train, a.calib)
    if a.no_added_loop_inputs:
        w = {n: dict(d, feeds=[t for t in d["feeds"] if t not in d.get("added_inputs", [])]) for n, d in w.items()}
    if not a.no_feedback_rule:
        fb = feedback_inputs(w)
        w = {n: dict(d, feedback_inputs=fb[n]) for n, d in w.items()}

    scored = {(sp, kind, sd): p for (sp, kind), runs in data.items() if sp != "train" for sd, p, _ in runs}
    onsets = {(sp, kind, sd): int(info["onset_s"] / SAMPLE_S)
              for (sp, kind), runs in data.items() if kind in ("fault", "pair") for sd, _, info in runs}
    # every node trains and scores in its own process, on its own tags only (the nodes are independent:
    # the same computation as one after another, so the same report); the workers read the views
    fitted = {}
    with cf.ProcessPoolExecutor(max_workers=min(a.workers, len(w))) as ex:
        futs = {}
        for name, doc in w.items():
            futs[ex.submit(node_job, name, doc, fit, calib, scored, a.rule, a.alpha, a.limit, onsets, a.clip,
                           a.confirm, a.save_models)] = name
        for f in cf.as_completed(futs):
            fitted[futs[f]] = f.result()
            print(f"trained and scored {futs[f]}: {fitted[futs[f]]['weights']} weights, "
                  f"{fitted[futs[f]]['wall_s']:.0f} s", flush=True)
    report = {"meta": {"hours": a.hours, "train_seeds": [sd for sd, _ in train], "splits": splits, "faults": fids,
                       "rule": a.rule, "calib_seeds": [sd for sd, _ in calib], "calib": a.calib,
                       "hidden": list(HIDDEN), "factory_seed": FACTORY_SEED, "instruments": INSTRUMENTS,
                       "no_added_loop_inputs": a.no_added_loop_inputs, "alpha": a.alpha, "limit": a.limit, "clip": a.clip,
                       "feedback_rule": not a.no_feedback_rule, "confirm_s": a.confirm, "var_steady_s": VAR_STEADY_S,
                       "root_rule": a.root,
                       "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")},
              "nodes": {}, "faults": {}}
    for name in w:
        fn = fitted[name]
        row = {k: fn[k] for k in ("inputs", "outputs", "weights", "r2", "calib_covered", "h", "k", "hv")}
        for sp in splits:
            eps, covs = [], []
            for sd, _, _ in data.get((sp, "normal"), []):
                al, cov = fn["alarms"][(sp, "normal", sd)]
                eps.append(episodes(al))
                covs.append(float(cov.mean()))
            hrs = a.hours * len(eps)
            k = sum(1 for e in eps if e)
            row[sp] = {"episodes": sum(eps), "hours": hrs, "per_hour": sum(eps) / hrs if hrs else None,
                       "runs_with_alarm": k, "runs": len(eps), "ci95": list(wilson(k, len(eps))),
                       "covered": float(np.mean(covs)) if covs else None}
        report["nodes"][name] = row
    reach = downstream(w)
    for sp in splits:
        for sd, path, info in data.get((sp, "fault"), []):
            al = {name: fitted[name]["alarms"][(sp, "fault", sd)][0] for name in w}
            if info.get("root"):
                io = int(info["onset_s"] / SAMPLE_S)
                doc = w[info["root"]]
                rows = load_view(path, doc["commands"])          # the lead comes first in the rows
                info = dict(info, active_s=(first_activity(rows, doc, io + rows.lead) - rows.lead) * SAMPLE_S)
            report["faults"].setdefault(sp, []).append(
                score_fault(al, info, {n: d.get("asset", n) for n, d in w.items()},
                            {name: fitted[name]["cp"].get((sp, "fault", sd)) for name in w}, reach))
        for sd, path, info in data.get((sp, "pair"), []):
            al = {name: fitted[name]["alarms"][(sp, "pair", sd)][0] for name in w}
            report.setdefault("pairs", {}).setdefault(sp, []).append(
                score_pair(al, info, {n: d.get("asset", n) for n, d in w.items()}, reach,
                           {name: fitted[name]["events"].get((sp, "pair", sd), []) for name in w}))
    report["meta"]["wall_s"] = round(time.time() - t0)
    md = report_md(report, a)
    out = os.path.join(a.out, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-nodes")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=float)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    if a.save_models:
        import pickle
        runs = {f"{sp}|{kind}|{sd}": (path, info) for (sp, kind), rs in data.items() for sd, path, info in rs}
        with open(os.path.join(out, "models.pkl"), "wb") as fh:
            pickle.dump({"nodes": {n: fitted[n]["state"] for n in w}, "wiring": w, "fit": fit, "calib": calib,
                         "runs": runs, "hours": a.hours, "meta": report["meta"]}, fh)
    if a.root == "cf" and report["faults"]:
        # the engine's root of a single fault: the counterfactual (eval/cf.py, LOG-124), merged into the report
        from eval import cf as CF
        CF.main([out, "--kinds", "fault", "--tag", "root", "--workers", str(a.workers)])
        with open(os.path.join(out, "cf-root.json")) as fh:
            got = json.load(fh)["faults"]
        for sp, recs in report["faults"].items():
            by = {r["seed"]: r for r in got.get(sp, [])}
            for rec in recs:
                c = by.get(rec["seed"])
                if c is not None and not rec.get("control"):
                    rec["named_cf"], rec["top1_cf"] = c.get("named_cf"), bool(c.get("top1_cf"))
        md = report_md(report, a)
        with open(os.path.join(out, "report.json"), "w") as fh:
            json.dump(report, fh, indent=1, default=float)
        with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
    print("\n" + md + "\nwritten to " + out)
    return 0


def report_md(report, a):
    m = report["meta"]
    splits = m["splits"]
    L = [f"# Node score ({m['date']})", "",
         f"One method for every node (eval/nodes.py). Training: {len(m['train_seeds'])} runs of {a.hours} h. "
         f"Scored splits: {', '.join(splits)}, other days of the year. Alarm rule: {m.get('rule', 'gate')}.", "",
         "## Normal running (every alarm is false)", "",
         "| Node | Inputs | Weights | R2 held-out (per output) | " + " | ".join(f"{s}: runs with an alarm (95 % CI), per hour, time covered" for s in splits) + " |",
         "|---|---|---|---|" + "---|" * len(splits)]
    for name, row in report["nodes"].items():
        cells = []
        for s in splits:
            x = row[s]
            lo, hi = x["ci95"]
            ph = f"{x['per_hour']:.2f}" if x["per_hour"] is not None else "-"
            cv = f"{100 * x['covered']:.0f} %" if x.get("covered") is not None else "-"
            cells.append(f"{x['runs_with_alarm']} / {x['runs']} ({100 * lo:.0f} to {100 * hi:.0f} %), {ph}, {cv}")
        r2 = ", ".join(f"{v:.2f}" for v in row["r2"])
        L.append(f"| {name} | {len(row['inputs'])} | {row['weights']} | {r2} | " + " | ".join(cells) + " |")
    if report["faults"]:
        L += ["", "## Faults (severity uniform in 0.05 to 1 of the range rule, fault_ranges.py)", "",
              "| Split | Fault | Root | Detected on the root (95 % CI) | Root alarmed first | Root named (counterfactual, "
              "the engine's rule) | Graph rule (120 s, cross-check) | Delay median (s) "
              "| From the root's first activity, median (s) | Nodes alarmed, median | Alarms before onset |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for sp in splits:
            recs = report["faults"].get(sp, [])
            for fid in sorted({r["fid"] for r in recs}, key=lambda x: int(x[1:])):
                rs = [r for r in recs if r["fid"] == fid]
                n = len(rs)
                pre = sum(r["pre_alarms"] for r in rs)
                nal = sorted(len(r["alarmed"]) for r in rs)[n // 2]
                if rs[0]["control"]:
                    k = sum(1 for r in rs if r["false_nodes"])
                    lo, hi = wilson(k, n)
                    L.append(f"| {sp} | {fid} (control) | none | false alarms in {k} / {n} runs "
                             f"({100 * lo:.0f} to {100 * hi:.0f} %) | - | - | - | - | - | {nal} | {pre} |")
                    continue
                k = sum(r["detected"] for r in rs)
                t1 = sum(r["top1"] for r in rs)
                tc = sum(bool(r.get("top1_g120")) for r in rs)
                tf = f"{sum(bool(r.get('top1_cf')) for r in rs)} / {n}" if any("top1_cf" in r for r in rs) else "-"
                lo, hi = wilson(k, n)
                d = sorted(r["delay_s"] for r in rs if r["detected"])
                dm = f"{d[len(d) // 2]:.0f}" if d else "-"
                da = sorted(r["delay_active_s"] for r in rs if r["detected"] and r.get("delay_active_s") is not None)
                dam = f"{da[len(da) // 2]:.0f}" if da else "-"
                L.append(f"| {sp} | {fid} | {rs[0]['root']} | {k} / {n} ({100 * lo:.0f} to {100 * hi:.0f} %) | "
                         f"{t1} / {n} | {tf} | {tc} / {n} | {dm} | {dam} | {nal} | {pre} |")
    if report.get("pairs"):
        L += ["", "## Fault pairs (ideas.md 11.10: a pair passes only if the engine names every real root)", "",
              "| Split | Pair | Runs | Every root detected | Passed (every root named) | Machines named, median |",
              "|---|---|---|---|---|---|"]
        for sp in splits:
            recs = report["pairs"].get(sp, [])
            for fid in sorted({r["fid"] for r in recs}):
                rs = [r for r in recs if r["fid"] == fid]
                n = len(rs)
                nn = sorted(len(r["named"]) for r in rs)[n // 2]
                L.append(f"| {sp} | {fid} | {n} | {sum(r['all_detected'] for r in rs)} | "
                         f"{sum(r['passed'] for r in rs)} | {nn} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
