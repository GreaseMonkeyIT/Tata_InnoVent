"""2E secure pass — action authorization + tamper-evident audit ledger (master plan 2E steps 2-3).

Pure stdlib (hashlib/hmac/json/threading) so it unit-tests with plain pytest, no FastAPI needed;
api/main.py owns the HTTP wiring. Design is deliberately boring (2E honesty note): a single
shared operator token checked in constant time, and an append-only JSONL ledger where every
entry carries the hash of the previous one — edit or delete any line and the chain breaks at
that point. This is the same ledger the Stage-3 act loop will write its verbs into.

Env contract (read by main.py, passed in here):
  VISR_OPERATOR_TOKEN  unset/empty -> auth DISABLED (pre-2E behavior; safe rollout, /api/health
                       says so honestly). Set -> every state-changing endpoint requires it.
  AUDIT_PATH           ledger file (deploy mounts a volume; default /data/audit.jsonl).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time

GENESIS = "0" * 64  # prev-hash of the first entry


def token_ok(provided: str | None, expected: str | None) -> bool:
    """True when the action may proceed. No expected token -> auth disabled (rollout mode).
    Comparison is constant-time so the token can't be guessed byte-by-byte."""
    if not expected:
        return True
    return bool(provided) and hmac.compare_digest(provided, expected)


def _canonical(record: dict) -> bytes:
    """Deterministic serialization for hashing: sorted keys, no whitespace."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def chain_hash(prev: str, record: dict) -> str:
    """Hash of an entry = sha256(prev_hash + canonical(entry-without-hash-fields))."""
    return hashlib.sha256(prev.encode() + _canonical(record)).hexdigest()


class AuditLedger:
    """Append-only, hash-chained JSONL audit trail: who/what/when + the evidence cited.

    Each line: {ts, actor, verb, target, status, evidence, prev, hash}. `hash` covers `prev`
    plus every other field, so any edit, insertion, or deletion breaks verification from that
    entry onward. Appends are serialized under a lock; the tail hash is recovered from the
    file on open, so the chain survives process restarts.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._last = GENESIS
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        # recover the tail hash so a restarted process extends the existing chain
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._last = json.loads(line).get("hash", self._last)
        except FileNotFoundError:
            pass

    def append(self, actor: str, verb: str, target: str, status: str,
               evidence: dict | None = None) -> dict:
        """Record one state-changing action (or denied attempt). Returns the stored entry."""
        with self._lock:
            record = {
                "ts": round(time.time(), 3),
                "actor": actor or "anonymous",
                "verb": verb,
                "target": target,
                "status": status,
                "evidence": evidence or {},
                "prev": self._last,
            }
            record["hash"] = chain_hash(self._last, {k: v for k, v in record.items() if k != "prev"})
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._last = record["hash"]
            return record

    def entries(self, limit: int = 200) -> list[dict]:
        """Most-recent-last list of entries (bounded)."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                rows = [json.loads(s) for s in (line.strip() for line in f) if s]
        except FileNotFoundError:
            return []
        return rows[-limit:]

    def verify(self) -> tuple[bool, int]:
        """Walk the whole file re-deriving every hash. Returns (chain_ok, n_entries)."""
        prev = GENESIS
        n = 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    body = {k: v for k, v in row.items() if k not in ("prev", "hash")}
                    if row.get("prev") != prev or chain_hash(prev, body) != row.get("hash"):
                        return False, n
                    prev = row["hash"]
                    n += 1
        except FileNotFoundError:
            return True, 0
        return True, n
