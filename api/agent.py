"""Ask VISR (LOG-093): the small local model answers an operator's question with read-only tools.

The engine decides the verdict and the checker decides what may be executed. The agent only reads:
the incident, the live readings of one asset, and the checked suggestions. It cannot write, and it
cannot change the verdict. A forge test on 2026-09-26 (gemma4 e4b on the 4 GB GPU) chose the right
tools and answered in 5 to 11 s.

Guards: at most MAX_ROUNDS tool rounds; an answer whose numbers do not appear in a tool result is
replaced by a plain pointer to the incident panel; any failure gives the same pointer. Pure except
for `chat`, the callable main.py passes in (one Ollama /api/chat request).
"""
from __future__ import annotations

import json

from narrator import clean, numbers_ok, _NUM

MAX_ROUNDS = 3
SYSTEM = ("You answer a plant operator about the current incident. Use the tools for every fact. "
          "Plain, short sentences. Name machines exactly as the tools write them. Never invent a number, "
          "a cause, or an action. If the tools do not answer the question, say so.")
TOOLS = [
    {"type": "function", "function": {
        "name": "get_incident",
        "description": "The active incident: origin, current driver with its reason, the chain, tripped "
                       "machines, forecast cards, integrity findings, and the phases so far.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_readings",
        "description": "Live readings of one plant asset: current in A, temperature in C, its rail and the "
                       "rail voltage, and whether it tripped.",
        "parameters": {"type": "object", "properties": {"asset": {"type": "string",
                                                                  "description": "for example press-1"}},
                       "required": ["asset"]}}},
    {"type": "function", "function": {
        "name": "get_suggestions",
        "description": "The checked suggestions for the incident. executable=true means the console can do it "
                       "after the operator confirms. executable=false means a person does it.",
        "parameters": {"type": "object", "properties": {}}}},
]
FALLBACK = "I cannot answer that reliably now. The incident panel shows the verified facts."


def ask(question: str, chat, tools: dict) -> dict:
    """tools: {name: callable(**args) -> JSON-able}. chat(messages, tools) -> the assistant message or None."""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": str(question)[:500]}]
    used, results = [], []
    for _ in range(MAX_ROUNDS + 1):
        m = chat(msgs, TOOLS)
        if not m:
            return {"answer": FALLBACK, "tools": used, "source": "fallback"}
        msgs.append(m)
        calls = m.get("tool_calls") or []
        if not calls:
            break
        if len(used) >= MAX_ROUNDS * 3:
            return {"answer": FALLBACK, "tools": used, "source": "fallback"}
        for c in calls:
            fn = (c.get("function") or {}).get("name")
            args = (c.get("function") or {}).get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            try:
                res = tools[fn](**args) if fn in tools else {"error": "no such tool"}
            except Exception as e:                      # a bad argument must not break the answer
                res = {"error": str(e)[:200]}
            used.append({"tool": fn, "args": args})
            text = json.dumps(res, default=str)
            results.append(text)
            msgs.append({"role": "tool", "content": text})
    answer = clean(m.get("content") or "")
    if not answer:
        return {"answer": FALLBACK, "tools": used, "source": "fallback"}
    allowed = {float(x) for r in results for x in _NUM.findall(r)}
    if not numbers_ok(answer, allowed):
        return {"answer": FALLBACK, "tools": used, "source": "rejected"}
    return {"answer": answer, "tools": used, "source": "llm"}
