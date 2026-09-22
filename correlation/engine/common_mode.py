"""The common-mode rule (SCENARIOS.md 4.5).

The plant coupling rule pairs a victim with a source that LEADS it. `PLANT_SOURCES` says
`bus_voltage:current_draw`, so a rail sag needs a machine load that rises first. An external
disturbance has no such load anywhere inside the plant, so the rule alone can never place a
cause above the plant. The plant model declares the supply above its rails as one more medium,
and this module is the inference that reads it.

The rule fires when every member of one declared medium deviates together and no member leads.
The cause is then the medium itself, which is the entity the domain is named after
(`rail:incomer-1` -> `incomer-1`), the same convention that names `psu-a` inside `rail:psu-a`.

What is inference and what is declaration:
  - Conditions 4a and 4b are the inference. They rule out every internal cause the engine can
    express, and every member that even looks like one.
  - Conditions 1 to 3 say the disturbance is common to the whole medium.
  - That the medium sits ABOVE its members is declared topology, not inference. The evidence
    label `common_mode` says so, and the console must not claim more than that.

Why the rule may REVERSE an edge. When a whole medium moves at once, the members' vectors are
nearly identical, so the gate finds r near 1.0 at lag 0 for every pair and picks a direction by
tie-break. Between the medium and its own member that direction is a coin flip, and declared
topology breaks the tie honestly. The rule therefore replaces every lag-0 bare edge between the
medium and one of its members, whichever way the tie-break sent it, so the verdict always carries
the honest `common_mode` label when the rule fired. It never touches an edge that has real
evidence for its own direction: source (`write`) evidence, or a non-zero lag. One source-evidenced
edge anywhere inside the domain stops the rule instead (condition 4a).
"""
from __future__ import annotations

import numpy as np

EVIDENCE = "common_mode"
WRITE = "write"


def domain_entity(domain: str) -> str:
    """`rail:incomer-1` -> `incomer-1`. A domain with no prefix names itself."""
    return domain.split(":", 1)[1] if ":" in domain else domain


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Plain correlation over the slice. Returns 0.0 when either side is flat."""
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    sa, sb = float(np.std(a)), float(np.std(b))
    if sa <= 0.0 or sb <= 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _fires(domain, members, entity, findings, onset_s, edges, vectors,
           write_onset_s, min_members, window_s):
    """The four conditions of SCENARIOS.md 4.5. Returns the deviating members, or None."""
    # 1. the medium deviated, and so did every member that carries the signal
    present = [m for m in sorted(members) if m in findings and m in vectors]
    carriers = [m for m in sorted(members) if m in vectors]
    if entity not in present or len(present) != len(carriers):
        return None
    # 2. enough members for "common" to mean anything
    if len(present) < min_members:
        return None
    # 3. the onsets sit inside one window
    ons = [onset_s[m] for m in present if m in onset_s]
    if len(ons) != len(present) or (max(ons) - min(ons)) > window_s:
        return None
    # 4a. no member other than the medium SOURCES a source-evidenced edge inside this domain.
    #     A bare lag-0 edge between two victims is a tie-break, not an aggressor, so it does
    #     not count. A write-evidenced edge does: the engine can already name that cause, and
    #     this rule must never overrule a cause with evidence behind it.
    if any(WRITE in e.get("evidence", []) and e["src"] in members
           and e["src"] != entity and e["dst"] in members for e in edges):
        return None
    # 4b. and no member may even LOOK like an aggressor. A member whose own load signal
    #     deviated inside the window is a candidate internal cause even when no edge formed.
    #     Under a supply dip the rails carry no load signal at all, so the supply domain stays
    #     clean, while every machine domain vetoes itself. That is what keeps PS7 to ONE root.
    t_entity = onset_s[entity]
    if any(m != entity and m in members and t <= t_entity + window_s
           for m, t in (write_onset_s or {}).items()):
        return None
    return present


def apply_common_mode(
    domains: dict[str, set | list],
    findings: dict[str, dict],
    onset_s: dict[str, float],
    edges: list[dict],
    vectors: dict[str, np.ndarray],
    write_onset_s: dict[str, float] | None = None,
    *,
    min_members: int = 3,
    window_s: float = 15.0,
    kind: str = "rail",
    r_min: float = 0.6,
) -> list[dict]:
    """Return the edge list with the medium-to-member edges in place.

    domains:  {domain name: members} for ONE family prefix.
    findings: the pass's findings, keyed by member. A member with no finding did not deviate.
    edges:    the edges the source-leads gate accepted. Conditions 4a and 4b read them.
    vectors:  the correlation slice, keyed by member.
    write_onset_s: source-signal onsets, for condition 4b.
    """
    out = list(edges)
    for domain in sorted(domains):
        members = set(domains[domain])
        entity = domain_entity(domain)
        present = _fires(domain, members, entity, findings, onset_s, edges, vectors,
                         write_onset_s, min_members, window_s)
        if present is None:
            continue
        for victim in present:
            if victim == entity:
                continue
            r = _pearson(vectors[entity], vectors[victim])
            if r < r_min:
                continue          # the medium and the member must actually move together
            pair = frozenset((entity, victim))
            existing = [e for e in out if frozenset((e["src"], e["dst"])) == pair]
            if any(WRITE in e.get("evidence", []) or e.get("lag_s", 0) != 0 for e in existing):
                continue                    # real evidence for its own direction: leave it
            out = [e for e in out if frozenset((e["src"], e["dst"])) != pair] + [{
                "src": entity,
                "dst": victim,
                "r": round(r, 3),
                "lag_s": 0,
                "evidence": ["stat", kind, EVIDENCE],
                "common_mode": {"domain": domain, "members": len(present),
                                "spread_s": round(max(onset_s[m] for m in present)
                                                  - min(onset_s[m] for m in present), 1)},
            }]
    return sorted(out, key=lambda e: (e["src"], e["dst"]))
