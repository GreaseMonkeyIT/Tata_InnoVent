"use client";
import { useEffect, useRef, useState } from "react";

// VISR Boot — the honest system check (master plan 2D-1; the one approved VISR section never
// built until now). Every line is a REAL probe against the live API with its measured round-trip;
// nothing renders that didn't happen. No fake progress bars. Click anywhere to skip; auto-enters
// ~1.2s after a clean pass; on failures it holds with an ENTER button so the operator sees what's
// down before the dashboard shows its (honest) degraded state.
const CHECKS = [
  { id: "core", label: "engine core", path: "/api/health",
    detail: (j) => Object.entries(j?.services || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "ok" },
  { id: "telemetry", label: "telemetry", path: "/api/pod-resources",
    detail: (j) => `${j?.source || "?"} · ${j?.pods?.length ?? 0} pod series` },
  { id: "workloads", label: "workloads", path: "/api/pods",
    detail: (j) => `${Array.isArray(j) ? j.length : 0} workloads seen` },
  { id: "graph", label: "causal graph", path: "/api/graph",
    detail: (j) => `${j?.meta?.pods ?? "—"} pods · ${j?.meta?.accepted_edges ?? 0} edges accepted` },
  { id: "plant", label: "plant sim", path: "/api/plant",
    detail: (j) => `${Object.keys(j?.devices || {}).length} assets · ${Object.keys(j?.rails || {}).length} rails · physics-simulated` },
];

const withTimeout = (p, ms) =>
  Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`timeout ${ms}ms`)), ms))]);

export default function Boot({ getJSON, onDone }) {
  const [rows, setRows] = useState(CHECKS.map((c) => ({ id: c.id, label: c.label, st: "wait", d: "", ms: null })));
  const [phase, setPhase] = useState("run");   // run | done | out
  const alive = useRef(true);
  const leaving = useRef(false);

  function leave() {
    if (leaving.current) return;
    leaving.current = true;
    alive.current = false;
    setPhase("out");
    setTimeout(onDone, 320);                   // matches the CSS fade
  }

  useEffect(() => {
    alive.current = true;
    let fails = 0;
    (async () => {
      for (let i = 0; i < CHECKS.length; i++) {
        if (!alive.current) return;
        setRows((r) => r.map((x, j) => (j === i ? { ...x, st: "run" } : x)));
        const t0 = performance.now();
        try {
          const j = await withTimeout(getJSON(CHECKS[i].path), 4000);
          const ms = Math.round(performance.now() - t0);
          if (!alive.current) return;
          setRows((r) => r.map((x, k) => (k === i ? { ...x, st: "ok", d: CHECKS[i].detail(j), ms } : x)));
        } catch (e) {
          const ms = Math.round(performance.now() - t0);
          fails++;
          if (!alive.current) return;
          setRows((r) => r.map((x, k) => (k === i ? { ...x, st: "fail", d: String(e?.message || e), ms } : x)));
        }
      }
      if (!alive.current) return;
      setPhase("done");
      // ?boot=hold keeps the screen up after the checks (design review / pacing a recording);
      // click or ENTER still dismisses. Normal path: auto-enter 1.2s after a clean pass.
      const hold = new URLSearchParams(window.location.search).get("boot") === "hold";
      if (fails === 0 && !hold) setTimeout(() => { if (alive.current) leave(); }, 1200);
    })();
    return () => { alive.current = false; };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const failed = rows.filter((r) => r.st === "fail").length;
  const foot = phase !== "done"
    ? "system check running · click to skip"
    : failed
      ? `${failed} check${failed > 1 ? "s" : ""} failed — dashboard will show the degraded state`
      : "all systems nominal";

  return (
    <div className={`boot${phase === "out" ? " out" : ""}`} onClick={leave}>
      <div className="boot-panel">
        <div className="boot-brand"><b>VISR</b><span>causal aiops · edge inference node</span></div>
        <div className="boot-rows">
          {rows.map((r) => (
            <div key={r.id} className={`boot-row ${r.st}`}>
              <span className="k">{r.label}</span>
              <span className="d">{r.st === "run" ? "…" : r.d}</span>
              <span className="ms">{r.ms != null ? `${r.ms}ms` : ""}</span>
              <span className="st">{r.st === "ok" ? "OK" : r.st === "fail" ? "FAIL" : r.st === "run" ? "··" : "--"}</span>
            </div>
          ))}
        </div>
        <div className="boot-foot">
          <span>{foot}</span>
          {phase === "done" && <button className="btn cyan" onClick={leave}>enter dashboard</button>}
        </div>
      </div>
    </div>
  );
}
