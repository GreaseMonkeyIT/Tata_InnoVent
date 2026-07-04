"""Stage 1 (ROAD): union per-signal rendered graphs into one served causal graph.

Each resource signal (psi_io / psi_cpu / psi_mem) is detected and correlated independently by its
own GraphMemory (a different physical witness and a different case family per resource class). This
merges their rendered graphs into a single view: every edge and finding is tagged with its
`signal`, and root cause + blast radius are re-ranked over the UNION so an aggressor that contends
on more than one resource wins. Pure function -- unit-tested in tests/test_merge.py; the service
layer owns the per-signal passes.
"""
from __future__ import annotations

from .ranking import blast_radius, build_graph, rank_root_causes

# per-signal stats that sum across the merged signals
_STAT_KEYS = ("edge_memory", "visible_memory_edges", "cases", "families", "open_mistakes", "held_edges")
# case fields surfaced from whichever signal owns the current incident
_CASE_KEYS = ("case_id", "case_family", "case_register", "case_sim")


def merge_graphs(per_signal: dict[str, dict], primary: str | None = None) -> dict:
    """per_signal: {signal: rendered_graph}  ->  one merged graph with per-edge `signal` tags.
    `primary` is the topbar's idle label (the main monitored signal); during an incident the
    label is the active signal instead."""
    edges: list[dict] = []
    findings: list[dict] = []
    for sig, g in per_signal.items():
        for e in g.get("edges", []) or []:
            edges.append({**e, "signal": e.get("signal", sig)})
        for f in g.get("findings", []) or []:
            findings.append({**f, "signal": f.get("signal", sig)})

    # Unified ranking over the union of accepted edges -- but only when something is an incident
    # (findings present). At idle the merged graph may still carry a steady backbone (held edges
    # with no findings); that must not invent a root cause. Mirrors state._render's guard.
    ranking: list[dict] = []
    blast: list[dict] = []
    if edges and findings:
        g = build_graph(edges)
        # Seed ONLY with findings that actually sit on the causal graph. A finding on one signal
        # (e.g. a CPU burst with no CPU edge yet) must not seed ranking over another signal's
        # steady-backbone edges -- rank_root_causes falls back to all nodes on an empty seed set,
        # which would blame the backbone. No on-graph symptom -> no root.
        seeds = [f["pod"] for f in findings if f["pod"] in g]
        if seeds:
            onset = {f["pod"]: f.get("onset_s") for f in findings}
            ranking = rank_root_causes(g, seeds, onset)
            if ranking:
                blast = blast_radius(g, ranking[0]["pod"])

    meta: dict = {"signals": sorted(per_signal)}
    for g in per_signal.values():
        m = g.get("meta", {}) or {}
        for k in _STAT_KEYS:
            if k in m:
                meta[k] = meta.get(k, 0) + (m[k] or 0)

    # Surface the case (and the "active" signal label) from whichever signal owns the top root.
    active_sig = None
    if ranking:
        root = ranking[0]["pod"]
        active_sig = next((e["signal"] for e in edges if e["src"] == root), None)
        if active_sig and active_sig in per_signal:
            m = per_signal[active_sig].get("meta", {}) or {}
            for k in _CASE_KEYS:
                if k in m:
                    meta[k] = m[k]
    # Topbar label: the active incident signal, else the primary monitored signal (NOT the
    # alphabetically-first, which would show e.g. psi_cpu at idle).
    meta["signal"] = active_sig or primary or (sorted(per_signal)[0] if per_signal else None)
    meta["pods"] = max((g.get("meta", {}).get("pods", 0) for g in per_signal.values()), default=0)
    meta["active"] = len({f["pod"] for f in findings})
    meta["accepted_edges"] = len(edges)

    return {
        "findings": findings,
        "edges": edges,
        "root_cause_ranking": ranking,
        "blast_radius": blast,
        "meta": meta,
    }
