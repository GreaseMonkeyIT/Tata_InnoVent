"""Root-cause ranking and blast radius — MASTER_PLAN section 1.4.5.

Ranking = explanatory reach: a node's score is how much of the symptom set it
explains through accepted causal edges (forward reachability with decay),
penalized when the node itself has an upstream explainer, tie-broken by onset.
Deterministic and narratable: "coolmon explains 87% of the observed degradation."
"""
from __future__ import annotations

import networkx as nx

DECAY = 0.7
CUT = 0.15
UPSTREAM_PENALTY = 0.5


def build_graph(edges: list[dict]) -> nx.DiGraph:
    g = nx.DiGraph()
    for e in edges:
        g.add_edge(e["src"], e["dst"], weight=abs(e["r"]), lag_s=e["lag_s"], evidence=e["evidence"])
    return g


def _forward_impact(g: nx.DiGraph, root: str) -> dict[str, float]:
    """Decayed best-impact to every node reachable from root."""
    impact: dict[str, float] = {}
    frontier = [(root, 1.0)]
    while frontier:
        node, w = frontier.pop(0)
        for _, nxt, data in g.out_edges(node, data=True):
            nw = w * data["weight"] * DECAY
            if nw < CUT or nw <= impact.get(nxt, 0.0):
                continue
            impact[nxt] = nw
            frontier.append((nxt, nw))
    return impact


def rank_root_causes(
    g: nx.DiGraph,
    seeds: list[str],
    onset_s: dict[str, float] | None = None,
    top: int = 3,
) -> list[dict]:
    """Score every candidate by how much of the symptom set it explains."""
    onset_s = onset_s or {}
    seed_set = {s for s in seeds if s in g} or set(g.nodes)
    if g.number_of_edges() == 0:
        return []
    scores: dict[str, float] = {}
    for n in g.nodes:
        imp = _forward_impact(g, n)
        explain = sum(v for s, v in imp.items() if s in seed_set)
        if n in seed_set and g.in_degree(n) == 0:
            explain += 1.0  # nothing upstream explains this symptom: it explains itself
        if g.in_degree(n) > 0:
            explain *= UPSTREAM_PENALTY  # someone else explains this node
        if explain > 0:
            scores[n] = explain
    if not scores:
        return []
    total = sum(scores.values())
    ranked = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], onset_s.get(kv[0], float("inf"))),
    )
    return [
        {"pod": pod, "score": round(sc / total, 3), "onset_s": onset_s.get(pod)}
        for pod, sc in ranked[:top]
    ]


def blast_radius(g: nx.DiGraph, root: str) -> list[dict]:
    """Forward reachability with weight decay; predicts next victims with ETA."""
    if root not in g:
        return []
    out: list[dict] = []
    frontier = [(root, 1.0, 0)]
    seen = {root}
    while frontier:
        node, w, eta = frontier.pop(0)
        for _, nxt, data in g.out_edges(node, data=True):
            nw = w * data["weight"] * DECAY
            if nxt in seen or nw < CUT:
                continue
            seen.add(nxt)
            neta = eta + int(data.get("lag_s", 0))
            out.append({"pod": nxt, "impact": round(nw, 3), "eta_s": neta})
            frontier.append((nxt, nw, neta))
    return sorted(out, key=lambda d: -d["impact"])
