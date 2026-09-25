"""LOG-088: GET /metrics through FastAPI, with the fakes of test_scenarios_api."""
from test_scenarios_api import api  # noqa: F401  (the fixture)


def test_route_serves_the_verdict_as_text(api):  # noqa: F811
    main, client, _, world, _, _ = api
    world["engine"] = {"root_cause_ranking": [{"pod": "press-1", "score": 0.9}], "edges": [], "blast_radius": [],
                       "findings": [], "incipient": [], "meta": {}}
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert 'visr_root_score{asset="press-1"} 0.9' in r.text
    assert "visr_engine_up 1" in r.text


def test_route_says_engine_down_instead_of_failing(api):  # noqa: F811
    main, client, _, world, _, _ = api
    world["down"].add("engine.test")
    r = client.get("/metrics")
    assert r.status_code == 200 and "visr_engine_up 0" in r.text
    assert "visr_root_active" not in r.text


def test_route_needs_no_token_and_writes_nothing(api):  # noqa: F811
    main, client, _, _, posts, calls = api
    before = len(main.AUDIT.entries(0))
    assert client.get("/metrics").status_code == 200
    assert posts == [] and not [c for c in calls if c[0] != "GET"]
    assert len(main.AUDIT.entries(0)) == before
