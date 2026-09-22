"""2H run-time domains (FLEET.md section 9): attesters publish shared-medium domains while the plant runs.

The rules under test:
- a fetched rail domain admits a witness pair for a newly enrolled cell machine;
- a machine with no shared domain gets no pair (default-deny stays true);
- a failed fetch keeps the last good answer for that source;
- a fetch never removes a static (env-declared) domain or member;
- enrolled machine names stay verbatim through workload(), even with three dash-parts.
"""
import importlib
import sys

import numpy as np
import pytest


@pytest.fixture(scope="module")
def service(tmp_path_factory):
    # service.py opens its SQLite memory at import, so point it at a scratch file first
    mp = pytest.MonkeyPatch()
    mp.setenv("MEMORY_DB", str(tmp_path_factory.mktemp("mem") / "l3-memory.db"))
    mp.setenv("DOMAIN_SOURCES", "http://sim/domains,http://scada/domains")
    sys.modules.pop("service", None)
    mod = importlib.import_module("service")
    yield mod
    mp.undo()


SIM = {"domains": {
    "rail:psu-a": ["press-1", "press-2", "cnc-1", "qa-scanner-1", "psu-a"],
    "rail:psu-c": ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1", "psu-c"],
    "loop:cool-1": ["press-1", "press-2", "cnc-1", "furnace-1", "pack-wrapper-1", "cool-1"],
}}
SCADA = {"domains": {"plc:plc-packaging": ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1", "plc-packaging"]}}


def _fetcher(answers):
    def fetch(url):
        a = answers.get(url)
        if isinstance(a, Exception):
            raise a
        return a
    return fetch


def _vec():
    return np.zeros(180)


def test_fetched_rail_domain_admits_cell_pair(service):
    service._FETCHED.clear()
    service.refresh_domains(_fetcher({"http://sim/domains": SIM, "http://scada/domains": SCADA}))
    vectors = {"pack-conveyor-1": _vec(), "pack-wrapper-1": _vec(), "conveyor-1": _vec()}
    w = service._witness_for("bus_voltage", vectors)
    assert frozenset(("pack-conveyor-1", "pack-wrapper-1")) in w.shared_relation
    # conveyor-1 sits on psu-b, the cell sits on psu-c: no shared rail, no pair
    assert frozenset(("conveyor-1", "pack-conveyor-1")) not in w.shared_relation
    assert w.relation_kind == "rail"


def test_plc_domain_never_feeds_a_plant_family(service):
    service._FETCHED.clear()
    service.refresh_domains(_fetcher({"http://sim/domains": {"domains": {}}, "http://scada/domains": SCADA}))
    vectors = {"pack-conveyor-1": _vec(), "pack-labeler-1": _vec()}
    # both machines share only plc:plc-packaging, which is not a rail or loop domain
    assert service._witness_for("bus_voltage", vectors).shared_relation == set()
    assert "plc" in service.PLANT_DOMAINS


def test_failed_fetch_keeps_last_good(service):
    service._FETCHED.clear()
    service.refresh_domains(_fetcher({"http://sim/domains": SIM, "http://scada/domains": SCADA}))
    service.refresh_domains(_fetcher({"http://sim/domains": OSError("down"),
                                      "http://scada/domains": OSError("down")}))
    assert "pack-wrapper-1" in service.PLANT_DOMAINS["rail"]["rail:psu-c"]
    assert "plc-packaging" in service.PLANT_ENTITIES


def test_fetch_never_removes_static(service):
    service._FETCHED.clear()
    # a source that knows nothing about psu-b must not drop the env-declared psu-b members
    service.refresh_domains(_fetcher({"http://sim/domains": {"domains": {"rail:psu-a": ["press-1"]}},
                                      "http://scada/domains": {"domains": {}}}))
    assert service.PLANT_DOMAINS["rail"]["rail:psu-b"] >= {"conveyor-1", "compressor-1", "furnace-1", "chiller-1"}
    assert service.PLANT_DOMAINS["rail"]["rail:psu-a"] >= {"press-1", "press-2", "cnc-1", "qa-scanner-1"}


def test_enrolled_names_stay_verbatim(service):
    service._FETCHED.clear()
    service.refresh_domains(_fetcher({"http://sim/domains": SIM, "http://scada/domains": SCADA}))
    assert service.workload("pack-conveyor-1") == "pack-conveyor-1"
    assert service.workload("pack-labeler-1") == "pack-labeler-1"
    # a real pod of the PLC keeps the usual replicaset and hash stripping
    assert service.workload("plc-packaging-6d9f8c7b5-x2k4q") == "plc-packaging"
