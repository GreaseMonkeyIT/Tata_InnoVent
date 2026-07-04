"""Persistent graph memory for the L3 engine.

The pure correlation pass in pipeline.py stays stateless and fixture-friendly.
This module is the service-layer memory: it persists edge confidence, promotes
stable incidents into cases, and keeps model/mistake tables that outlive the
14-day telemetry window.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

import numpy as np
from dataclasses import dataclass
from typing import Any

from .ranking import blast_radius, build_graph, rank_root_causes


SCHEMA_VERSION = "l3-memory-v5"  # v5: + baselines (per-workload steady-state; incident = deviation)

# Evidence tags that mark an edge as SOURCE-attributed (a real aggressor, not a victim cascade).
# Only psi_io has one today (`write`, from pipeline._writer_edge); Stage 1/2 (ROAD) add CPU/mem
# source tags here when those source signals land. A case is promoted only from a source-rooted
# verdict (see _promote_case).
SOURCE_EVIDENCE = ("write",)


def stable_workload(pod: str) -> str:
    """Drop ReplicaSet/pod suffixes: cooling-monitor-abc123-xyz -> cooling-monitor."""
    parts = pod.split("-")
    return "-".join(parts[:-2]) if len(parts) > 2 else pod


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _jaccard(a: set, b: set) -> float:
    """Set overlap; 1.0 when both empty (no info -> treated as identical)."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


@dataclass
class MemoryConfig:
    signal: str = "psi_io"
    alpha: float = 0.4
    decay: float = 0.1
    show: float = 0.6
    hide: float = 0.25
    prior: float = 0.2       # topology-prior baseline for a freshly-seeded disk coupling
    floor_frac: float = 0.4  # learned floor = floor_frac * peak confidence the edge reached
    tau_merge: float = 0.85  # case similarity >= this -> fold (same case); conservative
    tau_family: float = 0.60 # case similarity >= this (but < merge) -> variant of the same family
    base_alpha: float = 0.05   # slow EWMA for the steady-state baseline (storms barely move it)
    dev_k: float = 4.0         # incident = signal exceeds baseline by dev_k robust deviations
    mad_floor: float = 0.01    # numerical floor on MAD so near-zero baselines aren't hair-trigger
    base_min_n: int = 12       # baseline must mature (this many updates) before it gates incidents


class GraphMemory:
    """SQLite-backed evolutionary memory for the correlation service."""

    def __init__(self, db_path: str, config: MemoryConfig | None = None):
        self.db_path = db_path
        self.config = config or MemoryConfig()
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        self.record_model_version(
            component="edge-memory",
            version=SCHEMA_VERSION,
            params={
                "signal": self.config.signal,
                "alpha": self.config.alpha,
                "decay": self.config.decay,
                "show": self.config.show,
                "hide": self.config.hide,
            },
            notes="Persistent L3 memory: edge confidence, cases, model versions, mistakes.",
        )

    def _init_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edge_memory (
              src TEXT NOT NULL,
              dst TEXT NOT NULL,
              signal TEXT NOT NULL,
              confidence REAL NOT NULL,
              r REAL NOT NULL,
              lag_s INTEGER NOT NULL,
              evidence_json TEXT NOT NULL,
              hits INTEGER NOT NULL,
              visible INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL,
              first_seen_ts REAL NOT NULL,
              last_seen_ts REAL NOT NULL,
              updated_ts REAL NOT NULL,
              base_conf REAL NOT NULL DEFAULT 0,
              PRIMARY KEY (src, dst, signal)
            );

            CREATE TABLE IF NOT EXISTS cases (
              id TEXT PRIMARY KEY,
              key_json TEXT NOT NULL,
              stressors_json TEXT NOT NULL,
              victims_json TEXT NOT NULL,
              signal TEXT NOT NULL,
              witness_kind TEXT,
              motif_json TEXT NOT NULL,
              lag_structure_json TEXT NOT NULL,
              scenario_label TEXT,
              occurrences INTEGER NOT NULL,
              first_seen_ts REAL NOT NULL,
              last_seen_ts REAL NOT NULL,
              typical_lead_time_s REAL,
              remediation TEXT,
              source TEXT NOT NULL DEFAULT 'encountered',
              family_id TEXT
            );

            CREATE TABLE IF NOT EXISTS case_observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              case_id TEXT NOT NULL,
              root TEXT,
              victims_json TEXT NOT NULL,
              graph_json TEXT NOT NULL,
              outcome TEXT,
              notes TEXT
            );

            CREATE TABLE IF NOT EXISTS graph_snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              graph_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              component TEXT NOT NULL,
              version TEXT NOT NULL,
              params_json TEXT NOT NULL,
              notes TEXT,
              UNIQUE(component, version, params_json)
            );

            CREATE TABLE IF NOT EXISTS mistakes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              incident_id TEXT,
              predicted_json TEXT NOT NULL,
              correction_json TEXT NOT NULL,
              notes TEXT,
              status TEXT NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS baselines (
              workload TEXT NOT NULL,
              signal TEXT NOT NULL,
              median REAL NOT NULL,
              mad REAL NOT NULL,
              n INTEGER NOT NULL,
              updated_ts REAL NOT NULL,
              PRIMARY KEY (workload, signal)
            );
            """
        )
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """One-time migrations keyed on schema_version. edge_memory is a lossy
        running-belief table, so on a key-scheme change we clear it; the permanent
        archive (cases, case_observations, graph_snapshots) is preserved."""
        row = self.db.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        prev = row["value"] if row else None
        if prev != SCHEMA_VERSION:
            if prev == "l3-memory-v1":
                # v1 keyed edge_memory by ephemeral pod name; v2 keys by workload.
                # Drop the stale pod-hash rows so they cannot resurface as ghosts.
                self.db.execute("DELETE FROM edge_memory")
            # v3 adds the learned structural floor; add the column to pre-v3 tables.
            cols = [r[1] for r in self.db.execute("PRAGMA table_info(edge_memory)").fetchall()]
            if "base_conf" not in cols:
                self.db.execute("ALTER TABLE edge_memory ADD COLUMN base_conf REAL NOT NULL DEFAULT 0")
            # v4 adds case families; existing cases become their own one-member family.
            ccols = [r[1] for r in self.db.execute("PRAGMA table_info(cases)").fetchall()]
            if "family_id" not in ccols:
                self.db.execute("ALTER TABLE cases ADD COLUMN family_id TEXT")
                self.db.execute("UPDATE cases SET family_id=id WHERE family_id IS NULL")
            self.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def record_model_version(self, component: str, version: str, params: dict, notes: str = "") -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO model_versions(ts, component, version, params_json, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (time.time(), component, version, _json(params), notes),
        )
        self.db.commit()

    def record_mistake(
        self,
        predicted: dict,
        correction: dict,
        incident_id: str | None = None,
        notes: str = "",
    ) -> None:
        self.db.execute(
            """
            INSERT INTO mistakes(ts, incident_id, predicted_json, correction_json, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (time.time(), incident_id, _json(predicted), _json(correction), notes),
        )
        self.db.commit()

    def observe(self, graph: dict, vectors: dict[str, Any], witness: Any = None,
                ts: float | None = None) -> dict:
        """Update memory from a pure run_pass output and return the rendered graph."""
        ts = ts or time.time()
        self.update_baselines(vectors, ts)
        current_edges = graph.get("edges", [])
        # Work in WORKLOAD space: pod names are ephemeral (new hash every restart).
        pods_present = {stable_workload(p) for p in vectors}
        seen = {
            (stable_workload(e["src"]), stable_workload(e["dst"]), self.config.signal)
            for e in current_edges
        }

        if witness is not None:
            self._seed_structural(witness, pods_present, ts)
        for edge in current_edges:
            self._confirm_edge(edge, ts)
        self._decay_absent_edges(seen, pods_present, ts)

        case = self._promote_case(graph, ts)
        if current_edges:
            self.db.execute(
                "INSERT INTO graph_snapshots(ts, graph_json) VALUES (?, ?)",
                (ts, _json(graph)),
            )
        self.db.commit()

        rendered = self._render(graph, vectors)
        if case:
            rendered.setdefault("meta", {}).update(case)
        rendered.setdefault("meta", {}).update(self.stats())
        return rendered

    def bootstrap_graph(self) -> dict:
        """Status graph at startup. Held edges surface once the first live pass
        identifies the current pods -- we never render an edge against a pod that
        is not currently running, so a bootstrap before any samples shows none."""
        graph = {
            "findings": [],
            "edges": [],
            "root_cause_ranking": [],
            "blast_radius": [],
            "meta": {"status": "bootstrapped_from_memory"},
        }
        rendered = self._render(graph)
        rendered.setdefault("meta", {}).update(self.stats())
        return rendered

    def _seed_structural(self, witness: Any, pods_present: set[str], ts: float) -> None:
        """Seed/maintain the steady-state coupling backbone: every witnessed shared-disk
        pair carries AT LEAST the topology-prior floor, so the 'normal state' graph exists
        before anything storms and survives for pairs whose confidence has decayed away
        (DESIGN section 1.1). New pairs are inserted; existing ones have the floor ensured."""
        prior = self.config.prior
        for pair in getattr(witness, "shared_relation", set()) or set():
            wls = sorted({stable_workload(p) for p in pair})
            if len(wls) != 2 or wls[0] not in pods_present or wls[1] not in pods_present:
                continue
            key = (wls[0], wls[1], self.config.signal)
            row = self.db.execute(
                "SELECT base_conf FROM edge_memory WHERE src=? AND dst=? AND signal=?", key
            ).fetchone()
            if row is None:
                self.db.execute(
                    """
                    INSERT INTO edge_memory
                      (src, dst, signal, confidence, r, lag_s, evidence_json, hits, visible,
                       state, first_seen_ts, last_seen_ts, updated_ts, base_conf)
                    VALUES (?, ?, ?, ?, 0, 0, ?, 0, 0, 'steady', ?, ?, ?, ?)
                    """,
                    (wls[0], wls[1], self.config.signal, prior, _json(["pvc"]), ts, ts, ts, prior),
                )
            elif float(row["base_conf"]) < prior:
                self.db.execute(
                    "UPDATE edge_memory SET base_conf=? WHERE src=? AND dst=? AND signal=?",
                    (prior, *key),
                )

    def _confirm_edge(self, edge: dict, ts: float) -> None:
        # Key by WORKLOAD, not pod: pod-keying resets confidence on every restart
        # (new hash) and strands it under dead pods. The workload is stable.
        src_w, dst_w = stable_workload(edge["src"]), stable_workload(edge["dst"])
        key = (src_w, dst_w, self.config.signal)
        row = self.db.execute(
            "SELECT * FROM edge_memory WHERE src=? AND dst=? AND signal=?",
            key,
        ).fetchone()
        if row:
            prev_base = float(row["base_conf"])
            conf = float(row["confidence"]) + self.config.alpha * (1.0 - float(row["confidence"]))
            hits = int(row["hits"]) + 1
            first_seen = float(row["first_seen_ts"])
            visible = bool(row["visible"]) or conf >= self.config.show
        else:
            prev_base = 0.0
            conf = self.config.alpha
            hits = 1
            first_seen = ts
            visible = conf >= self.config.show
        # Learned structural floor: a pair that has coupled keeps a baseline it decays
        # toward (never back to zero) -- the dynamic, per-edge default weight.
        base_conf = max(prev_base, self.config.floor_frac * conf)
        state = "active" if visible else "confirming"
        self.db.execute(
            """
            INSERT OR REPLACE INTO edge_memory
              (src, dst, signal, confidence, r, lag_s, evidence_json, hits, visible,
               state, first_seen_ts, last_seen_ts, updated_ts, base_conf)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                src_w,
                dst_w,
                self.config.signal,
                conf,
                float(edge["r"]),
                int(edge["lag_s"]),
                _json(edge.get("evidence", [])),
                hits,
                1 if visible else 0,
                state,
                first_seen,
                ts,
                ts,
                base_conf,
            ),
        )

    def _decay_absent_edges(self, seen: set[tuple[str, str, str]], pods_present: set[str], ts: float) -> None:
        rows = self.db.execute("SELECT * FROM edge_memory WHERE signal=?", (self.config.signal,)).fetchall()
        for row in rows:
            key = (row["src"], row["dst"], row["signal"])
            if key in seen:
                continue
            if row["src"] not in pods_present or row["dst"] not in pods_present:
                continue
            base = float(row["base_conf"])
            conf = max(base, float(row["confidence"]) * (1.0 - self.config.decay))  # floor, not zero
            visible = bool(row["visible"]) and conf >= self.config.hide
            at_floor = conf <= base + 1e-9 and base > 0
            state = "steady" if at_floor else ("decaying" if visible else "hidden")
            self.db.execute(
                """
                UPDATE edge_memory
                SET confidence=?, visible=?, state=?, updated_ts=?
                WHERE src=? AND dst=? AND signal=?
                """,
                (conf, 1 if visible else 0, state, ts, row["src"], row["dst"], row["signal"]),
            )

    def _render(self, graph: dict, vectors: dict[str, Any] | None = None) -> dict:
        out = dict(graph)
        # workload -> current pod name, so held (workload-keyed) edges render against
        # the pod that is running NOW, never a dead pod-hash from an old generation.
        wl_pod = {stable_workload(p): p for p in (vectors or {})}
        live_edges = []
        live_pairs = set()
        for edge in graph.get("edges", []):
            sw, dw = stable_workload(edge["src"]), stable_workload(edge["dst"])
            live_pairs.add(frozenset((sw, dw)))
            row = self.db.execute(
                "SELECT * FROM edge_memory WHERE src=? AND dst=? AND signal=?",
                (sw, dw, self.config.signal),
            ).fetchone()
            enriched = dict(edge)  # keep the live (current-pod) src/dst names
            if row:
                cf = round(float(row["confidence"]), 3)
                enriched.update(
                    {
                        "confidence": cf,
                        "state": row["state"],
                        "hits": int(row["hits"]),
                        "render_weight": cf,
                        "source": "live",
                    }
                )
            live_edges.append(enriched)

        # Held + STRUCTURAL backbone: any edge that is visible OR carries a learned
        # baseline (base_conf>0). One edge per unordered pair (strongest wins); skip a
        # pair already shown live; render against the current pod or not at all.
        rows = self.db.execute(
            "SELECT * FROM edge_memory WHERE signal=? AND (visible=1 OR base_conf>0)",
            (self.config.signal,),
        ).fetchall()
        by_pair: dict[Any, Any] = {}
        for row in rows:
            pair = frozenset((row["src"], row["dst"]))
            if pair in live_pairs:
                continue
            if pair not in by_pair or float(row["confidence"]) > float(by_pair[pair]["confidence"]):
                by_pair[pair] = row
        held_edges = []
        now = time.time()
        for row in by_pair.values():
            src_pod, dst_pod = wl_pod.get(row["src"]), wl_pod.get(row["dst"])
            if src_pod is None or dst_pod is None:
                continue  # a participating workload has no live pod -> don't render a ghost
            cf = round(float(row["confidence"]), 3)
            held_edges.append(
                {
                    "src": src_pod,
                    "dst": dst_pod,
                    "r": round(float(row["r"]), 3),
                    "lag_s": int(row["lag_s"]),
                    "evidence": _load_json(row["evidence_json"], []),
                    "confidence": cf,
                    "base_conf": round(float(row["base_conf"]), 3),
                    "render_weight": cf,
                    "state": row["state"],
                    "hits": int(row["hits"]),
                    "last_seen_s": round(now - float(row["last_seen_ts"]), 1),
                    "source": "memory",
                }
            )

        out["edges"] = live_edges + held_edges
        out.setdefault("meta", {})["held_edges"] = len(held_edges)

        if graph.get("findings") and out["edges"]:
            g = build_graph(out["edges"])
            seeds = [f["pod"] for f in graph.get("findings", [])]
            onset_s = {f["pod"]: f.get("onset_s") for f in graph.get("findings", [])}
            ranking = rank_root_causes(g, seeds, onset_s)
            out["root_cause_ranking"] = ranking
            out["blast_radius"] = blast_radius(g, ranking[0]["pod"]) if ranking else []
        return out

    def _promote_case(self, graph: dict, ts: float) -> str | None:
        roots = graph.get("root_cause_ranking") or []
        edges = graph.get("edges") or []
        if not roots or not edges:
            return None
        root = stable_workload(roots[0]["pod"])
        # Promote a case ONLY from a SOURCE-rooted verdict: the ranked root must own an outgoing
        # edge carrying source (write) evidence -- a real aggressor. A psi-only victim->victim
        # cascade (e.g. dcim-bridge->timescaledb between storm pulses, when the live source edge
        # has decayed to its floor) must NOT mint a case, or the library learns the wrong
        # direction. S0 silence is the deviation gate's job; this only changes WHICH incidents
        # become cases. (ROAD Stage 0 / archived REMAINING section 1.)
        if not any(
            stable_workload(e["src"]) == root
            and any(ev in SOURCE_EVIDENCE for ev in (e.get("evidence") or []))
            for e in edges
        ):
            return None
        victims = sorted(
            {
                stable_workload(b.get("pod", ""))
                for b in graph.get("blast_radius", [])
                if b.get("pod") and stable_workload(b["pod"]) != root
            }
        )
        if not victims:
            victims = sorted({stable_workload(e["dst"]) for e in edges if stable_workload(e["dst"]) != root})
        if not victims:
            return None

        motif = sorted(
            (
                {"src": stable_workload(e["src"]), "dst": stable_workload(e["dst"])}
                for e in edges
            ),
            key=lambda item: (item["src"], item["dst"]),
        )
        lag_structure = {
            f"{stable_workload(e['src'])}->{stable_workload(e['dst'])}": int(e.get("lag_s", 0))
            for e in edges
        }
        witness_kind = self._dominant_witness(edges)
        lead = self._lead_time(graph, edges)
        key_json = _json({
            "stressors": [root], "victims": victims, "signal": self.config.signal,
            "witness_kind": witness_kind, "motif": motif,
        })
        fp = {
            "stressors": {root},
            "victims": set(victims),
            "motif": {(m["src"], m["dst"]) for m in motif},
            "signal": self.config.signal,
            "witness_kind": witness_kind,
        }
        new_id = hashlib.sha1(key_json.encode("utf-8")).hexdigest()[:16]

        # Route-1 similarity merge (conservative). Find the nearest existing case; fold a
        # near-identical incident (recurrence), group a close one as a variant of the same
        # family, else open a novel case/family. Replaces the exact-hash identity that
        # minted a new case for every motif/lead permutation and exploded the library.
        best, best_sim = None, 0.0
        for crow in self.db.execute("SELECT * FROM cases").fetchall():
            s = self._case_sim(fp, crow)
            if s > best_sim:
                best, best_sim = crow, s

        if best is not None and best_sim >= self.config.tau_merge:
            register, case_id = "recurrence", best["id"]
            family_id = best["family_id"] or best["id"]
            occurrences = int(best["occurrences"]) + 1
            old_lead = best["typical_lead_time_s"]
            if lead is not None and old_lead is not None:
                lead = (float(old_lead) * int(best["occurrences"]) + lead) / occurrences
            elif lead is None:
                lead = old_lead
            # Keep the prototype STABLE (anchored to the case's shape) so it does not drift
            # toward the last-merged variant and stop recognizing its own family; the full
            # per-incident history still lives in case_observations.
            self.db.execute(
                "UPDATE cases SET occurrences=?, last_seen_ts=?, typical_lead_time_s=? WHERE id=?",
                (occurrences, ts, lead, case_id),
            )
        else:
            case_id = new_id
            if best is not None and best_sim >= self.config.tau_family:
                register, family_id = "variant", (best["family_id"] or best["id"])
            else:
                register, family_id = "novel", new_id
            self.db.execute(
                """
                INSERT OR IGNORE INTO cases
                  (id, key_json, stressors_json, victims_json, signal, witness_kind,
                   motif_json, lag_structure_json, occurrences, first_seen_ts,
                   last_seen_ts, typical_lead_time_s, family_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (case_id, key_json, _json([root]), _json(victims), self.config.signal,
                 witness_kind, _json(motif), _json(lag_structure), 1, ts, ts, lead, family_id),
            )
        self.db.execute(
            """
            INSERT INTO case_observations(ts, case_id, root, victims_json, graph_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, case_id, root, _json(victims), _json(graph)),
        )
        return {"case_id": case_id, "case_family": family_id,
                "case_register": register, "case_sim": round(best_sim, 3)}

    def _case_sim(self, fp: dict, crow: Any) -> float:
        """Structural similarity in [0,1] between a live fingerprint and a stored case.
        Hard-gated by signal + physical witness (a different coupling is a different type);
        otherwise a weighted Jaccard over victims, stressors, and the directed motif.
        Lead/lag time is an *averaged attribute* (typical_lead_time_s), not an identity
        feature, so it does not split structurally-identical incidents into new cases."""
        if fp["signal"] != crow["signal"] or (fp["witness_kind"] or "") != (crow["witness_kind"] or ""):
            return 0.0
        cv = set(_load_json(crow["victims_json"], []))
        cs = set(_load_json(crow["stressors_json"], []))
        cm = {(m["src"], m["dst"]) for m in _load_json(crow["motif_json"], [])}
        return (0.40 * _jaccard(fp["victims"], cv)
                + 0.20 * _jaccard(fp["stressors"], cs)
                + 0.40 * _jaccard(fp["motif"], cm))

    def _dominant_witness(self, edges: list[dict]) -> str | None:
        counts: dict[str, int] = {}
        for edge in edges:
            for ev in edge.get("evidence", []):
                if ev in ("stat", "temporal", "write"):  # keep only physical witnesses (pvc/ebpf/psi)
                    continue
                counts[ev] = counts.get(ev, 0) + 1
        if not counts:
            return None
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    def _lead_time(self, graph: dict, edges: list[dict]) -> float | None:
        etas = [float(b["eta_s"]) for b in graph.get("blast_radius", []) if b.get("eta_s") is not None]
        if etas:
            return sum(etas) / len(etas)
        lags = [float(e.get("lag_s", 0)) for e in edges if e.get("lag_s", 0) > 0]
        return sum(lags) / len(lags) if lags else None

    def update_baselines(self, vectors: dict[str, Any], ts: float | None = None) -> None:
        """Learn each workload's steady-state signal level (robust median + MAD) by slow EWMA.
        A workload that is currently deviating (storming) is skipped, so the baseline stays the
        NORMAL, not the incident -- this is what later lets 'incident = deviation from normal'."""
        ts = ts or time.time()
        cfg = self.config
        for pod, vec in vectors.items():
            arr = np.asarray(vec, dtype=float)
            if arr.size < 12:
                continue
            wl = stable_workload(pod)
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            row = self.db.execute(
                "SELECT median, mad, n FROM baselines WHERE workload=? AND signal=?",
                (wl, cfg.signal),
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO baselines(workload, signal, median, mad, n, updated_ts) VALUES (?, ?, ?, ?, ?, ?)",
                    (wl, cfg.signal, med, mad, 1, ts),
                )
                continue
            n, omed, omad = int(row["n"]), float(row["median"]), float(row["mad"])
            if n >= cfg.base_min_n and float(np.percentile(arr, 90)) > omed + cfg.dev_k * max(omad, cfg.mad_floor):
                continue  # this workload is storming -> don't learn it into the baseline
            a = cfg.base_alpha
            self.db.execute(
                "UPDATE baselines SET median=?, mad=?, n=?, updated_ts=? WHERE workload=? AND signal=?",
                ((1 - a) * omed + a * med, (1 - a) * omad + a * mad, n + 1, ts, wl, cfg.signal),
            )
        self.db.commit()

    def baseline_threshold(self, workload: str) -> float | None:
        """Incident threshold for a workload: median + dev_k * MAD, once the baseline is mature.
        None while still learning (so the caller treats it as 'not yet an incident')."""
        row = self.db.execute(
            "SELECT median, mad, n FROM baselines WHERE workload=? AND signal=?",
            (workload, self.config.signal),
        ).fetchone()
        if row is None or int(row["n"]) < self.config.base_min_n:
            return None
        return float(row["median"]) + self.config.dev_k * max(float(row["mad"]), self.config.mad_floor)

    def stats(self) -> dict:
        edges = self.db.execute("SELECT COUNT(*) FROM edge_memory").fetchone()[0]
        visible = self.db.execute("SELECT COUNT(*) FROM edge_memory WHERE visible=1").fetchone()[0]
        cases = self.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        families = self.db.execute("SELECT COUNT(DISTINCT COALESCE(family_id, id)) FROM cases").fetchone()[0]
        mistakes = self.db.execute("SELECT COUNT(*) FROM mistakes WHERE status='open'").fetchone()[0]
        return {
            "memory_db": self.db_path,
            "edge_memory": int(edges),
            "visible_memory_edges": int(visible),
            "cases": int(cases),
            "families": int(families),
            "open_mistakes": int(mistakes),
        }
