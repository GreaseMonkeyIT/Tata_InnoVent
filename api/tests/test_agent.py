"""Ask VISR (api/agent.py, LOG-093). The chat callable is faked."""
import agent


def _tools():
    return {"get_incident": lambda: {"driver": {"asset": "chiller-1", "reason": "loop flow is 72 of 120 L/min"}},
            "get_readings": lambda asset="": {"asset": asset, "amps": 38.9, "tripped": False},
            "get_suggestions": lambda: []}


def test_agent_uses_tools_and_answers():
    turns = iter([{"role": "assistant", "tool_calls": [{"function": {"name": "get_incident", "arguments": {}}}]},
                  {"role": "assistant", "content": "chiller-1 drives it: loop flow is 72 of 120 L/min."}])
    out = agent.ask("why chiller-1?", lambda m, t: next(turns), _tools())
    assert out["source"] == "llm" and out["tools"] == [{"tool": "get_incident", "args": {}}]
    assert "72 of 120" in out["answer"]


def test_agent_rejects_an_invented_number_and_survives_bad_calls():
    turns = iter([{"role": "assistant", "tool_calls": [{"function": {"name": "get_readings",
                                                                     "arguments": '{"asset": "press-2"}'}},
                                                       {"function": {"name": "drop_table", "arguments": {}}}]},
                  {"role": "assistant", "content": "press-2 draws 55 A."}])            # 55 is invented
    out = agent.ask("press-2?", lambda m, t: next(turns), _tools())
    assert out["source"] == "rejected" and out["answer"] == agent.FALLBACK
    assert [u["tool"] for u in out["tools"]] == ["get_readings", "drop_table"]
    assert agent.ask("x", lambda m, t: None, _tools())["source"] == "fallback"
