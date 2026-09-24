"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Glyph from "./Glyph";
import { send } from "./lib/api";
import { fmtMs, istTs } from "./lib/format";

// Fleet (2H, FLEET.md section 11): the virtual PLCs. Every card is a virtual PLC with a protocol
// profile, not vendor firmware, and says so. The six onboarding phases are REAL timestamps from
// Kubernetes, the tag server, and the aggregator window. Nothing on this card animates on a timer.
// Add PLC opens as a form inside this tab, and Remove asks for a second click, so no dialog ever
// covers the console (LOG-062). A click on a PLC cabinet on the map focuses its card here.
const PHASE_LABEL = {
  requested: "req", scheduled: "pod", running: "run", enrolled: "enroll", polling: "scada", in_window: "engine",
};
// time since the request, short enough for the card: 15.6s, 4.2m, 3.1h
const ago = (s) => (s < 100 ? `+${s.toFixed(1)}s` : s < 6000 ? `+${(s / 60).toFixed(1)}m` : `+${(s / 3600).toFixed(1)}h`);
const STATE_ST = { RUN: "ok", STOP: "strained", FAULT: "hot", STARTING: "busy", OFFLINE: "idle" };
const STATE_C = { RUN: "var(--teal)", STOP: "var(--amber)", FAULT: "var(--red)", STARTING: "var(--teal)", OFFLINE: "var(--text-faint)" };
const ARM_S = 5;   // seconds a Remove stays armed for the second click

function Phases({ phases }) {
  const t0 = phases?.[0]?.ts;
  return (
    <div className="phases">
      {(phases || []).map((p) => {
        const done = p.ts != null;
        const dt = done && t0 != null ? p.ts - t0 : null;
        return (
          <div key={p.phase} className={`ph${done ? " done" : ""}`} title={done ? istTs(p.ts) : "not reached yet"}>
            <span className="pd" />
            <span className="pl">{PHASE_LABEL[p.phase] || p.phase}</span>
            <span className="pt">{dt == null ? "" : p.phase === "requested" ? "t0" : ago(dt)}</span>
          </div>
        );
      })}
    </div>
  );
}

function PlcCard({ p, tasks, onDone, focus }) {
  const ref = useRef(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [pick, setPick] = useState("");
  const [armed, setArmed] = useState(false);
  const current = tasks.find((t) => t.name === p.task?.name);
  const sameLayout = (t) => {
    const key = (c) => JSON.stringify(c?.machines_fixed || (c?.machines || []).map((m) => m.prefix || m.name));
    return current && key(t.cell) === key(current.cell);
  };
  const swaps = tasks.filter((t) => t.name !== p.task?.name && sameLayout(t));

  useEffect(() => { if (focus) ref.current?.scrollIntoView({ block: "nearest" }); }, [focus]);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), ARM_S * 1000);
    return () => clearTimeout(t);
  }, [armed]);

  async function act(label, fn) {
    setBusy(true); setMsg(`${label}…`);
    try { await fn(); setMsg(`${label} done`); onDone(); } catch (e) { setMsg(String(e.message || e)); }
    finally { setBusy(false); }
  }

  return (
    <div ref={ref} className={`plc st-${(p.state || "").toLowerCase()}${focus ? " focus" : ""}`}>
      <div className="plc-top">
        <Glyph st={STATE_ST[p.state] || "idle"} />
        <span className="nm">{p.name}</span>
        <span className="virt">virtual</span>
        <span className="st" style={{ color: STATE_C[p.state] }}>{p.state}</span>
      </div>
      <div className="plc-prof">{p.profile_label} · {p.protocol?.kind === "s7comm" ? "S7comm" : "Modbus TCP"} :{p.protocol?.port}{p.managed === "static" ? " · base" : ""}</div>
      <div className="plc-task"><b>{p.task?.title || p.task?.name || "no task"}</b>{p.task?.sha256 ? <i> · {p.task.sha256.slice(0, 8)}</i> : null}</div>
      <div className="plc-stats">
        <span><em>scan</em>{fmtMs(p.scan?.last_ms)}</span>
        <span><em>scada rtt</em>{fmtMs(p.scada?.rtt_ms)}</span>
        <span><em>tags</em>{p.scada?.good ?? 0}/{p.scada?.tags ?? 0} good</span>
      </div>
      <div className="plc-cell">rail {p.cell?.rail || "—"} · {(p.cell?.machines || []).join(", ") || "—"}</div>
      {p.fault ? <div className="plc-fault">{p.fault}</div> : null}
      {p.managed !== "static" && <Phases phases={p.phases} />}
      <div className="plc-act">
        {p.state === "RUN"
          ? <button className="btn sm" disabled={busy} onClick={() => act("stop", () => send("POST", `/api/fleet/plcs/${p.name}/stop`))}>Stop</button>
          : <button className="btn sm cmd" disabled={busy || p.state === "STARTING"} onClick={() => act("run", () => send("POST", `/api/fleet/plcs/${p.name}/run`))}>Run</button>}
        {swaps.length > 0 && (
          <>
            <select value={pick} onChange={(e) => setPick(e.target.value)} disabled={busy}>
              <option value="">load task…</option>
              {swaps.map((t) => <option key={t.name} value={t.name}>{t.title}</option>)}
            </select>
            <button className="btn sm cmd" disabled={busy || !pick} onClick={() => act(`load ${pick}`, () => send("PUT", `/api/fleet/plcs/${p.name}/task`, { task: pick }))}>Load</button>
          </>
        )}
        {p.managed !== "static" && (armed
          ? <button className="btn sm warn armed" disabled={busy} onClick={() => { setArmed(false); act("remove", () => send("DELETE", `/api/fleet/plcs/${p.name}`)); }}>Confirm remove</button>
          : <button className="btn sm warn" disabled={busy} onClick={() => setArmed(true)}>Remove</button>)}
      </div>
      {armed && <div className="plc-msg warn">cell machines leave the plant</div>}
      {msg && <div className="plc-msg">{msg}</div>}
    </div>
  );
}

function AddPlc({ tasks, profiles, rails, fleet, onClose, onDone }) {
  const creatable = tasks.filter((t) => !t.cell?.machines_fixed);
  const [task, setTask] = useState(creatable[0]?.name || "");
  const t = creatable.find((x) => x.name === task);
  const [profile, setProfile] = useState(t?.profile_hint || profiles[0]?.id || "");
  const [rail, setRail] = useState(t?.cell?.rail_default || rails[0] || "");
  const taken = new Set((fleet?.plcs || []).map((p) => p.name));
  const suggest = useMemo(() => {
    const base = `plc-${(t?.cell?.name || "cell").slice(0, 10)}`;
    let n = 1;
    while (taken.has(`${base}-${n}`)) n++;
    return `${base}-${n}`;
  }, [task, fleet]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  function chooseTask(v) {
    setTask(v);
    const nt = creatable.find((x) => x.name === v);
    if (nt?.profile_hint) setProfile(nt.profile_hint);
    if (nt?.cell?.rail_default) setRail(nt.cell.rail_default);
  }

  async function create() {
    setBusy(true); setErr(null);
    try {
      await send("POST", "/api/fleet/plcs", { name: name || suggest, profile, task, rail });
      onDone(); onClose();
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="addplc">
      <div className="addplc-h">
        <span className="brk">add plc</span>
        <button className="btn sm" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn sm cmd" disabled={busy || !task || !profile || !rail} onClick={create}>{busy ? "creating…" : "Create PLC"}</button>
      </div>
      <div className="addplc-b">
        <div>
          <div className="form">
            <label>name<input value={name} placeholder={suggest} onChange={(e) => setName(e.target.value.toLowerCase())} /></label>
            <label>task<select value={task} onChange={(e) => chooseTask(e.target.value)}>
              {creatable.map((x) => <option key={x.name} value={x.name}>{x.title}</option>)}
            </select></label>
            <label>profile<select value={profile} onChange={(e) => setProfile(e.target.value)}>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.label} · {p.protocol}:{p.port}</option>)}
            </select></label>
            <label>rail<select value={rail} onChange={(e) => setRail(e.target.value)}>
              {rails.map((r) => <option key={r} value={r}>{r}</option>)}
            </select></label>
          </div>
          {t && (
            <>
              <div className="form-note">{t.description}</div>
              <div className="form-note">cell machines: {(t.cell?.machines || []).map((m) => `${m.prefix}-n (${m.i_base} A${m.cooled ? ", cooled" : ""})`).join(" · ")}</div>
            </>
          )}
          {err && <div className="plc-msg err">{err}</div>}
        </div>
        {t && <pre className="stsrc" aria-label="task source, IEC 61131-3 Structured Text">{t.st}</pre>}
      </div>
    </div>
  );
}

export default function Fleet({ fleet, tasks, profiles, plant, onChanged, focus }) {
  const [adding, setAdding] = useState(false);
  if (!fleet) return <div className="empty">waiting for the fleet…</div>;
  if (fleet.source === "unavailable")
    return <div className="empty">fleet unavailable{fleet.error ? ` · ${fleet.error}` : " · the fleet runs only in the cluster"}</div>;
  const rails = Object.keys(plant?.rails || {});
  if (adding) {
    return <AddPlc tasks={tasks || []} profiles={profiles || []} rails={rails} fleet={fleet} onClose={() => setAdding(false)} onDone={onChanged} />;
  }
  return (
    <>
      <div className="fleet-bar">
        <span className="brk">plc fleet</span>
        {fleet.enroll !== "enabled" && <span className="fleet-note">enrollment {fleet.enroll}</span>}
        <button className="btn sm cmd" disabled={fleet.enroll !== "enabled" || !tasks?.length} onClick={() => setAdding(true)}>Add PLC</button>
      </div>
      <div className="plcs">
        {(fleet.plcs || []).length
          ? fleet.plcs.map((p) => <PlcCard key={p.name} p={p} tasks={tasks || []} onDone={onChanged} focus={focus === p.name} />)
          : <div className="empty">no PLCs yet</div>}
      </div>
    </>
  );
}
