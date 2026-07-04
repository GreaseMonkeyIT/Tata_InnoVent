"""End-to-end A1->A4 pass: signal vectors in, CausalGraph JSON out.

MASTER_PLAN sections 1.4.2-1.4.5. Deterministic; no LLM anywhere near this file.
"""
from __future__ import annotations

import itertools

import numpy as np

from . import detectors
from .gate import R_ADJ, SOURCE_COUPLING_KINDS, TEMPORAL_TOL_S, Witness, accept_edge
from .lagcorr import adjacent_support, best_directed, lag_profile
from .ranking import blast_radius, build_graph, rank_root_causes

DT_S = detectors.DT_S
R_SRC = 0.5  # cross-signal write->stall correlation floor (psi-psi uses gate.R_PEAK = 0.6)


def _edge_pref(e: dict) -> tuple[int, float]:
    """Dedup preference: a source (write-evidenced) edge beats a psi edge; then by |r|."""
    return (1 if "write" in e.get("evidence", []) else 0, abs(e.get("r", 0.0)))


def _writer_edge(src, dst, wsrc, vdst, witness, w_onset, v_onset):
    """Directed SOURCE edge writer->staller: io_write[src] correlates with and leads
    psi[dst] over a shared-disk/eBPF witness. Direction is FIXED src->dst (the writer is
    physically the source), so it never coin-flips like a near-simultaneous psi pair.
    An idle writer has a flat write vector -> zero correlation -> no edge (threshold-free)."""
    kinds = [k for k in witness.kinds(src, dst) if k in SOURCE_COUPLING_KINDS or k == witness.relation_kind]
    if not kinds:
        return None
    prof = lag_profile(wsrc, vdst)            # write[src] leads psi[dst] (configured lags >= 0)
    if not prof:
        return None
    lag, r = max(prof.items(), key=lambda kv: kv[1])   # strongest POSITIVE coupling
    if r < R_SRC or not adjacent_support(prof, lag, R_ADJ):
        return None  # (c) more write must mean MORE stall: a source edge is positively coupled,
                     # never anti-correlated (write up + stall down is not contention)
    if w_onset is not None and v_onset is not None and w_onset > v_onset + TEMPORAL_TOL_S:
        return None                            # the writer must not start AFTER the victim stalls
    temporal = w_onset is not None and v_onset is not None
    return {"src": src, "dst": dst, "r": round(float(r), 3), "lag_s": int(lag),
            "evidence": ["write"] + kinds + (["temporal"] if temporal else [])}


def run_pass(
    vectors: dict[str, np.ndarray],
    witness: Witness,
    slo_breach: list[str] | None = None,
    caps: dict[str, float] | None = None,
    window: int | None = None,
    write_vectors: dict[str, np.ndarray] | None = None,
    baselines: dict[str, float | None] | None = None,
    recent: int | None = None,
) -> dict:
    """One correlation pass.

    vectors: {pod: 1-D signal vector} (one representative signal per pod for v0;
             multi-signal fan-out happens in the caller)
    witness: physical relations from A3/kube-state
    slo_breach: symptom pods to seed root-cause ranking (defaults to pods with onsets)
    caps: optional {pod: limit} for saturation classification
    """
    caps = caps or {}
    # Detect over the FULL ring -- search the whole stored series for a disturbance
    # wherever/whenever it sits, rather than assuming it's in the most recent slice
    # (the data persists; we locate the event by detection, not by the clock). cusum
    # also needs a clean pre-event baseline, which only the full ring provides.
    # An onset is an INCIDENT only if it DEVIATES from the pod's learned steady-state. baselines
    # carries a per-pod incident threshold (median + k*MAD); None arg = ungated (fixtures), a None
    # value = baseline still maturing (treat as not-yet-an-incident). This is what makes S0 silent:
    # normal factory I/O stays within each pod's band, so nothing becomes a finding.
    raw_onsets: dict[str, list[dict]] = {}
    for pod, vec in vectors.items():
        ons = [o for o in detectors.cusum_onsets(vec) if abs(o["zpeak"]) >= 3.0]  # ignore weak/spurious onsets
        if not ons:
            continue
        if baselines is not None:
            thr = baselines.get(pod)
            # sustained elevation (p90), not a single noisy sample, must clear the band. When
            # `recent` is set, judge deviation over the RECENT tail only -> a storm that has
            # cooled is no longer a live incident (the verdict resets ~recent samples after it
            # ends, instead of when it scrolls out of the full ring), while detection still
            # scanned the whole ring for the onset. recent=None (fixtures) judges all of vec.
            gate_vec = vec[-recent:] if recent else vec
            if thr is None or float(np.percentile(gate_vec, 90)) <= thr:
                continue  # still learning, or within the pod's normal band -> steady state
        raw_onsets[pod] = ons

    # Choose the event CENTRE only among pods whose resources are COUPLED to another
    # anomalous pod (shared disk / network dep). An isolated noisy pod -- e.g. a
    # chronically flapping edge service -- must NOT drag the analysis window onto the
    # wrong event: the disturbance we care about lives in a coupled resource domain.
    # If nothing is coupled, fall back to the global strongest onset (old behaviour).
    onset_pods = list(raw_onsets)
    coupled_pods = [p for p in onset_pods
                    if any(q != p and witness.couples(p, q) for q in onset_pods)]
    center_idx: int | None = None
    best_z = -1.0
    for p in (coupled_pods or onset_pods):
        o = max(raw_onsets[p], key=lambda o: abs(o["zpeak"]))
        if abs(o["zpeak"]) > best_z:
            best_z, center_idx = abs(o["zpeak"]), o["idx"]
    peak: tuple[float, int] | None = (best_z, center_idx) if center_idx is not None else None

    # Per-pod EVENT onset = the onset NEAREST the event centre, not the first in the
    # ring. The 15-min ring can hold several past storms, so "first" can pin a pod to
    # a stale event and wreck the temporal ordering; nearest-to-centre ties every pod
    # to the SAME disturbance. (centre None -> first onset, preserves fixtures.)
    findings: dict[str, dict] = {}
    onset_s: dict[str, float] = {}
    for pod, ons in raw_onsets.items():
        ev = min(ons, key=lambda o: abs(o["idx"] - center_idx)) if center_idx is not None else ons[0]
        onset_s[pod] = ev["idx"] * DT_S
        findings[pod] = {
            "pod": pod,
            "class": detectors.classify(vectors[pod], ev["idx"], caps.get(pod)),
            "onset_s": onset_s[pod],
            "severity": min(abs(ev["zpeak"]) / 10.0, 1.0),
            "n_onsets": len(ons),
        }

    # Source-side WRITE onsets (io_write): the aggressor writes hard but barely stalls,
    # so it is invisible in psi -- detect its disturbance on its own signal, tied to the
    # same event centre as the victims.
    write_onset_s: dict[str, float] = {}
    for pod, wv in (write_vectors or {}).items():
        wons = [o for o in detectors.cusum_onsets(wv) if abs(o["zpeak"]) >= 3.0]
        if wons:
            ev = min(wons, key=lambda o: abs(o["idx"] - center_idx)) if center_idx is not None else wons[0]
            write_onset_s[pod] = ev["idx"] * DT_S

    # Correlate on a slice CENTRED on the detected event, not a fixed recent window:
    # the storm dominates the slice (so r isn't diluted) and an event minutes old is
    # still analysed because we found it by detection. Re-detection inside the slice
    # would fail (event lands before cusum's warmup) -- so we keep the full-ring onsets
    # and only narrow the correlation. window=None (fixtures) correlates the full ring.
    cvec = vectors
    write_cvec = write_vectors or {}
    if window and peak is not None:
        n = len(next(iter(vectors.values())))
        lo = max(0, min(peak[1] - window // 3, n - window))
        cvec = {p: v[lo:lo + window] for p, v in vectors.items()}
        if write_vectors:
            write_cvec = {p: v[lo:lo + window] for p, v in write_vectors.items()}

    active = set(findings)
    disturbed = bool(active)  # is anything anomalous at all?  (idle -> no edges)
    edges: list[dict] = []
    for a, b in itertools.combinations(sorted(vectors), 2):
        # Edges may form ONLY between pods whose resources overlap/interdepend
        # (shared disk or network dep -- witness.couples). Uncoupled pods that
        # merely happen to be hot at the same time get NO edge. Among coupled
        # pairs, evaluate one that touches an anomalous pod or -- once the system
        # is disturbed -- any coupled pair (pulls a chronically-loaded victim with
        # no clean onset of its own into the graph by CORRELATION over the shared
        # disk, never by an absolute resource threshold).
        if not witness.couples(a, b):
            continue
        if a not in active and b not in active and not disturbed:
            continue
        d = best_directed(cvec[a], cvec[b])
        src, dst = (a, b) if d["forward"] else (b, a)
        edge = accept_edge(src, dst, d["r"], d["lag_s"], d["profile"], witness, onset_s)
        if edge:
            edges.append(edge)

    # Source attribution: a writer driving a staller over a shared disk. The aggressor's
    # io_write tracks and leads the victim's psi stall though it never stalls itself --
    # this is the ONLY way the true source enters the graph (psi alone cannot see it).
    if write_vectors:
        write_act = {p: float(np.max(np.abs(wv))) for p, wv in write_cvec.items()}
        for w in sorted(write_cvec):
            if w not in write_onset_s:
                continue  # (b) a source must have DEVIATED from its baseline write (an actual
                          # storm) -- a pod doing its steady routine job is not a source. A
                          # routine pod that *starts* storming gets an onset, so it still counts.
            wv = write_cvec[w]
            for v in sorted(cvec):
                if v == w or not witness.couples_source(w, v):
                    continue
                if v not in active:
                    continue  # a SOURCE edge must point at a REAL victim (a deviating finding),
                              # never just a co-located pod. The old `disturbed` escape connected
                              # random pods once anything was active -- harmless under pvc (4 pods)
                              # but a false-edge storm under same-node, where everything couples.
                if write_act.get(w, 0.0) <= write_act.get(v, 0.0):
                    continue  # (a) and the source must out-write the victim (the dominant hog)
                e = _writer_edge(w, v, wv, cvec[v], witness, write_onset_s.get(w), onset_s.get(v))
                if e:
                    edges.append(e)
        # one edge per unordered pair; a write-evidenced source edge wins direction
        # conflicts over a victim<->victim psi edge (direction stability).
        chosen: dict[frozenset, dict] = {}
        for e in edges:
            pair = frozenset((e["src"], e["dst"]))
            if pair not in chosen or _edge_pref(e) > _edge_pref(chosen[pair]):
                chosen[pair] = e
        edges = sorted(chosen.values(), key=lambda e: (e["src"], e["dst"]))

    g = build_graph(edges)
    seeds = slo_breach or sorted(active)
    ranking = rank_root_causes(g, seeds, onset_s)
    blast = blast_radius(g, ranking[0]["pod"]) if ranking else []
    return {
        "findings": sorted(findings.values(), key=lambda f: f["onset_s"]),
        "edges": edges,
        "root_cause_ranking": ranking,
        "blast_radius": blast,
        "meta": {"pods": len(vectors), "active": len(active), "accepted_edges": len(edges)},
    }
