"""PS7 fixtures: the common-mode rule (SCENARIOS.md 2.8 and 4.5).

Every test plants a known electrical truth and asserts the engine rediscovers it blind:
- a supply dip sags every rail together with no machine leading -> root `incomer-1`;
- PS1, where one machine load DOES lead, must not fire the rule -> root stays `press-1`;
- a machine domain vetoes itself under the same dip, because its members carry a load signal,
  so PS7 gives ONE root and not one per rail;
- the rule is additive: with it off, the pass returns exactly what it returned before.
"""
import numpy as np

from engine.common_mode import EVIDENCE, domain_entity
from engine.gate import Witness
from engine.pipeline import run_pass

rng = np.random.default_rng(11)
N = 180  # 15-minute window at 5 s

SUPPLY_DOMAIN = {"rail:incomer-1": {"incomer-1", "psu-a", "psu-b", "psu-c"}}
RAIL_B_DOMAIN = {"rail:psu-b": {"conveyor-1", "compressor-1", "furnace-1", "chiller-1", "psu-b"}}
RAIL_A_DOMAIN = {"rail:psu-a": {"press-1", "press-2", "cnc-1", "qa-scanner-1", "psu-a"}}


def noise(scale=1.0, n=N):
    return rng.normal(0, scale, n)


def sag_step(onset=100, level=60.0, base=39.0):
    """A sag vector as build_inputs ingests it (sag = nominal - volts)."""
    x = noise(0.4) + base
    x[onset:] += level
    return x


def witness_for(*domain_maps):
    pairs = set()
    for dmap in domain_maps:
        for members in dmap.values():
            ms = sorted(members)
            for i, a in enumerate(ms):
                for b in ms[i + 1:]:
                    pairs.add(frozenset((a, b)))
    return Witness(shared_relation=pairs, relation_kind="rail")


def cm(*domain_maps, min_members=3, window_s=15.0):
    merged = {}
    for d in domain_maps:
        merged.update(d)
    return {"domains": merged, "kind": "rail", "min_members": min_members, "window_s": window_s}


def test_domain_entity_names_the_medium():
    assert domain_entity("rail:incomer-1") == "incomer-1"
    assert domain_entity("loop:cool-1") == "cool-1"
    assert domain_entity("bare") == "bare"


def test_supply_dip_roots_the_board_not_a_rail():
    """Every member of the supply domain sags inside one grid step, and no member carries a
    load signal that leads. The medium is the only cause left."""
    onset = 100
    v = {"incomer-1": sag_step(onset, base=0.5),      # the board sits at nominal until it dips
         "psu-a": sag_step(onset, base=39.0),
         "psu-b": sag_step(onset, base=27.0),
         "psu-c": sag_step(onset, base=0.5)}
    # the board's own current RESPONDS to the dip: it rises AFTER the sag, so it never leads
    draw = {"incomer-1": noise(0.3) + 120.0}
    draw["incomer-1"][onset + 3:] += 14.0

    out = run_pass(v, witness_for(SUPPLY_DOMAIN), write_vectors=draw,
                   common_mode=cm(SUPPLY_DOMAIN))
    cme = [e for e in out["edges"] if EVIDENCE in e["evidence"]]
    assert cme, f"the rule did not fire: {out['edges']}"
    assert all(e["src"] == "incomer-1" for e in cme)
    assert {e["dst"] for e in cme} == {"psu-a", "psu-b", "psu-c"}
    assert all(e["common_mode"]["domain"] == "rail:incomer-1" for e in cme)
    roots = out["root_cause_ranking"]
    assert roots and roots[0]["pod"] == "incomer-1", roots


def test_ps1_has_a_leading_machine_so_the_rule_stays_quiet():
    """press-1's current_draw leads its rail-mates' sag. The engine can name that cause, so the
    common-mode rule must not overrule it, even though every member of rail:psu-a deviates."""
    onset = 100
    v = {"press-1": sag_step(onset, level=14.0),
         "cnc-1": sag_step(onset + 2, level=14.0),
         "qa-scanner-1": sag_step(onset + 2, level=14.0),
         "psu-a": sag_step(onset + 2, level=14.0)}
    draw = {"press-1": noise(0.3) + 42.0}
    draw["press-1"][onset - 1:] += 38.0               # the amps step LEADS the sag

    out = run_pass(v, witness_for(RAIL_A_DOMAIN), write_vectors=draw,
                   common_mode=cm(RAIL_A_DOMAIN))
    assert not [e for e in out["edges"] if EVIDENCE in e["evidence"]], out["edges"]
    roots = out["root_cause_ranking"]
    assert roots and roots[0]["pod"] == "press-1", roots


def test_a_machine_domain_vetoes_itself_under_the_same_dip():
    """Under a supply dip the machines on a rail all sag too, and their constant-power loads all
    rise. Any one of them is a candidate internal cause, so the rule refuses the whole domain.
    PS7 therefore yields one root (the board) and not one per rail."""
    onset = 100
    v = {m: sag_step(onset) for m in sorted(RAIL_B_DOMAIN["rail:psu-b"])}
    draw = {"compressor-1": noise(0.3) + 55.0}
    draw["compressor-1"][onset:] += 8.0               # brownout answer, same instant as the sag

    out = run_pass(v, witness_for(RAIL_B_DOMAIN), write_vectors=draw,
                   common_mode=cm(RAIL_B_DOMAIN))
    assert not [e for e in out["edges"] if EVIDENCE in e["evidence"]], out["edges"]


def test_rule_touches_only_the_medium_pairs_and_turns_off():
    """COMMON_MODE=0 passes common_mode=None, and the pass returns what it returned before the
    rule existed. With the rule on, every edge that does NOT join the medium to one of its
    members survives untouched, and the findings never change either way."""
    onset = 100
    v = {"incomer-1": sag_step(onset, base=0.5),
         "psu-a": sag_step(onset, base=39.0),
         "psu-b": sag_step(onset, base=27.0),
         "psu-c": sag_step(onset, base=0.5)}
    w = witness_for(SUPPLY_DOMAIN)
    off = run_pass(v, w, common_mode=None)
    on = run_pass(v, w, common_mode=cm(SUPPLY_DOMAIN))
    assert not [e for e in off["edges"] if EVIDENCE in e["evidence"]]
    assert off["findings"] == on["findings"]

    def away_from_medium(edges):
        return [e for e in edges if "incomer-1" not in (e["src"], e["dst"])]

    assert away_from_medium(on["edges"]) == away_from_medium(off["edges"])
    # and the medium pairs are all present, all pointing away from the medium, all labelled
    med = [e for e in on["edges"] if "incomer-1" in (e["src"], e["dst"])]
    assert {e["dst"] for e in med} == {"psu-a", "psu-b", "psu-c"}
    assert all(e["src"] == "incomer-1" and EVIDENCE in e["evidence"] for e in med)


def test_the_rule_wins_the_coin_flip_against_the_medium():
    """A medium whose name sorts AFTER its members loses the gate's lag-0 tie-break. Declared
    topology must still send the edge away from the medium."""
    onset = 100
    dom = {"rail:zz-board": {"zz-board", "psu-a", "psu-b", "psu-c"}}
    v = {"zz-board": sag_step(onset, base=0.5),
         "psu-a": sag_step(onset, base=39.0),
         "psu-b": sag_step(onset, base=27.0),
         "psu-c": sag_step(onset, base=0.5)}
    out = run_pass(v, witness_for(dom), common_mode=cm(dom))
    med = [e for e in out["edges"] if "zz-board" in (e["src"], e["dst"])]
    assert med and all(e["src"] == "zz-board" for e in med), med
    roots = out["root_cause_ranking"]
    assert roots and roots[0]["pod"] == "zz-board", roots


def test_a_real_lag_keeps_its_own_direction():
    """An edge with a non-zero lag has evidence for its direction. The rule leaves it alone
    even while it labels the other pairs in the same domain."""
    onset = 100
    v = {"incomer-1": sag_step(onset, base=0.5),
         "psu-a": sag_step(onset + 6, base=39.0),      # six grid steps behind: a real lag
         "psu-b": sag_step(onset, base=27.0),
         "psu-c": sag_step(onset, base=0.5)}
    out = run_pass(v, witness_for(SUPPLY_DOMAIN), common_mode=cm(SUPPLY_DOMAIN, window_s=60.0))
    a = [e for e in out["edges"] if {e["src"], e["dst"]} == {"incomer-1", "psu-a"}]
    assert a and a[0]["lag_s"] != 0 and EVIDENCE not in a[0]["evidence"], a


def test_too_few_members_never_fires():
    """Two entities moving together are a pair, not a common mode. The floor keeps the rule
    away from every two-member medium."""
    onset = 100
    two = {"rail:incomer-1": {"incomer-1", "psu-a"}}
    v = {"incomer-1": sag_step(onset, base=0.5), "psu-a": sag_step(onset, base=39.0)}
    out = run_pass(v, witness_for(two), common_mode=cm(two))
    assert not [e for e in out["edges"] if EVIDENCE in e["evidence"]], out["edges"]


def test_a_quiet_member_blocks_the_claim():
    """"Common" means every member. One rail that did not move means the disturbance was not
    common to the medium, so the rule must not claim it was."""
    onset = 100
    v = {"incomer-1": sag_step(onset, base=0.5),
         "psu-a": sag_step(onset, base=39.0),
         "psu-b": sag_step(onset, base=27.0),
         "psu-c": noise(0.4) + 0.5}                    # untouched: no finding
    out = run_pass(v, witness_for(SUPPLY_DOMAIN), common_mode=cm(SUPPLY_DOMAIN))
    assert not [e for e in out["edges"] if EVIDENCE in e["evidence"]], out["edges"]
