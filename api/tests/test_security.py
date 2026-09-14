"""2E fixtures: action authorization + the hash-chained audit ledger (api/security.py).

Runs with plain pytest (stdlib module under test — no FastAPI needed): `python -m pytest tests`
from api/. The tamper cases are the point: edit, delete, or splice a line and verify() must
name the break.
"""
import json

from security import GENESIS, AuditLedger, chain_hash, token_ok


# ---------- token gate ----------

def test_no_expected_token_means_auth_disabled():
    assert token_ok(None, None)
    assert token_ok("anything", "")        # empty env var == unset


def test_token_enforced_when_set():
    assert token_ok("s3cret", "s3cret")
    assert not token_ok("wrong", "s3cret")
    assert not token_ok(None, "s3cret")
    assert not token_ok("", "s3cret")


# ---------- ledger chain ----------

def _ledger(tmp_path):
    return AuditLedger(str(tmp_path / "audit.jsonl"))


def test_appends_chain_and_verify(tmp_path):
    led = _ledger(tmp_path)
    e1 = led.append("operator", "trigger", "PS1", "fired", {"root": "press-1", "confidence": 1.0})
    e2 = led.append("operator", "reset", "PS1", "reset")
    assert e1["prev"] == GENESIS
    assert e2["prev"] == e1["hash"]
    ok, n = led.verify()
    assert ok and n == 2
    assert [r["verb"] for r in led.entries()] == ["trigger", "reset"]


def test_chain_survives_process_restart(tmp_path):
    led = _ledger(tmp_path)
    led.append("operator", "trigger", "PS1", "fired")
    reopened = AuditLedger(led.path)           # new process, same file
    reopened.append("operator", "reset", "PS1", "reset")
    ok, n = reopened.verify()
    assert ok and n == 2


def test_edited_entry_breaks_the_chain(tmp_path):
    led = _ledger(tmp_path)
    led.append("operator", "trigger", "PS1", "fired")
    led.append("operator", "trigger", "PS5", "fired")
    rows = [json.loads(s) for s in open(led.path, encoding="utf-8")]
    rows[0]["actor"] = "someone-else"          # rewrite history
    with open(led.path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    ok, n = led.verify()
    assert not ok and n == 0                   # break detected AT the edited entry


def test_deleted_entry_breaks_the_chain(tmp_path):
    led = _ledger(tmp_path)
    led.append("operator", "trigger", "PS1", "fired")
    led.append("viewer", "trigger", "PS5", "denied")
    led.append("operator", "reset", "PS1", "reset")
    rows = open(led.path, encoding="utf-8").readlines()
    with open(led.path, "w", encoding="utf-8") as f:
        f.writelines([rows[0], rows[2]])       # quietly drop the denied attempt
    ok, n = led.verify()
    assert not ok and n == 1


def test_denied_attempts_are_recorded_with_anonymous_actor(tmp_path):
    led = _ledger(tmp_path)
    led.append("", "trigger", "PS1", "denied")
    e = led.entries()[0]
    assert e["actor"] == "anonymous" and e["status"] == "denied"
    assert led.verify() == (True, 1)


def test_hash_recomputes_deterministically(tmp_path):
    led = _ledger(tmp_path)
    e = led.append("operator", "trigger", "PS2", "fired", {"note": "duty-cycle"})
    body = {k: v for k, v in e.items() if k not in ("prev", "hash")}
    assert chain_hash(e["prev"], body) == e["hash"]
