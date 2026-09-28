"use client";
import { useState } from "react";
import Glyph from "./Glyph";
import Fold from "./Fold";
import { getJSON } from "./lib/api";
import { RES_WORD, mib, istTime, istTs, num } from "./lib/format";

// The verdict: one answer instead of an alarm flood. States, each with its own shape and color:
// STEADY (no incident), FORECAST (no driver yet, but a limit is near), ROOT CAUSE (the incident has a
// driver), INCIDENT (an integrity finding or a blind SCADA view with no driver), RECOVERING (the
// origin is back to normal and the plant settles), and ENGINE ERROR. Two bands sit above the state
// when they apply (SCENARIOS.md 7): INTEGRITY and BLIND.
// LOG-092: the state follows the incident record (/api/incident), not the raw 10 s verdict. The
// record keeps the origin (what started it) apart from the current driver (what drives it now), and
// its phases say why the driver changed. With an older api (no record) the panel reads the graph.
// The engine decides the verdict. The narrator only words it, locked to the incident phase.

function Meter({ v, st = "" }) {
  const pct = Math.round(Math.max(0, Math.min(1, v ?? 0)) * 100);
  return <span className={`meter ${st}`}><i style={{ width: `${pct}%` }} /></span>;
}

// One line per open integrity finding: what disagrees, and with what.
function integrityText(f) {
  const tag = String(f.tag || "").split(".").slice(-2).join(".");
  if (f.kind === "unsigned_write") {
    const who = f.clients?.length ? ` · client ${f.clients.join(", ")}` : "";
    return `${tag} ${num(f.from)}→${num(f.to)} unsigned${f.reason === "out of range" ? " · out of range" : ""}${who}`;
  }
  if (f.kind === "current_balance") {
    const ch = f.channel ? ` · ${String(f.channel).split(".").slice(-2).join(".")}` : "";
    return `rail ${f.rail} · feeder ${num(f.feeder_amps)} A · PLC ${num(f.reported_amps)} A · gap ${num(f.gap_amps)} A${ch}`;
  }
  return f.kind;
}

const PHASE_ST = { open: "strained", origin: "hot", driver: "hot", trip: "trip", card: "strained", integrity: "alarm",
  blind: "idle", restart: "ok", action: "ok", relief: "ok", recovering: "ok", close: "ok" };

// Ask VISR (LOG-093): the local model answers with read-only tools. It never changes the verdict.
function Ask() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [a, setA] = useState(null);
  async function go(e) {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true); setA(null);
    try { setA(await getJSON(`/api/ask?q=${encodeURIComponent(q.trim())}`)); }
    catch (err) { setA({ answer: String(err.message || err), source: "error", tools: [] }); }
    finally { setBusy(false); }
  }
  return (
    <form className="vd-ask" onSubmit={go}>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="ask about this incident" maxLength={300} />
        <button className="btn sm" disabled={busy || !q.trim()}>{busy ? "…" : "Ask"}</button>
      </div>
      {a && (
        <div className="ans">
          <span>{a.answer}</span>
          {a.tools?.length ? <small>read: {a.tools.map((t) => t.tool.replace("get_", "") + (t.args?.asset ? ` ${t.args.asset}` : "")).join(" · ")}</small> : null}
        </div>
      )}
    </form>
  );
}

export default function Verdict({ d }) {
  const { root, rootEdge, blast, resWord, plantSet, chain, chainNodes, mediaNames, integrity } = d.derived;
  const { plant, narr, graph, scada } = d;
  const incip = [...(graph?.incipient || [])].sort((a, b) => (a.eta_s ?? 1e9) - (b.eta_s ?? 1e9));
  const engineErr = graph?.meta?.status === "error";
  const hasRecord = d.incident != null;                  // the api keeps the incident record
  const inc = d.incident?.active || null;
  const state = !graph ? "wait" : engineErr ? "error"
    : hasRecord
      ? (!inc ? "steady" : inc.status === "recovering" ? "recovering" : inc.driver ? "root" : incip.length ? "forecast" : "incident")
      : root ? "root" : incip.length ? "forecast" : "steady";
  const whoName = hasRecord ? (inc?.driver?.asset || inc?.origin?.asset) : root?.pod;
  const origin = inc?.origin;
  // LOG-099: the reason holds the readings at the shift. Once the plant recovers, or the driver has
  // tripped, they are history, so the past-tense line shows ("press-1 drew 85 A").
  const drv = inc?.driver;
  const reason = state === "recovering" ? (origin?.was || origin?.reason)
    : drv && inc?.tripped?.includes(drv.asset) ? (drv.was || drv.reason) : drv?.reason;
  const rootMatches = root && root.pod === whoName;      // the graph root is the incident driver
  // The chain stays on the root's own plane. Domain witnesses never join the plant and the pods.
  const samePlane = (pod) => plantSet.has(pod) === plantSet.has(whoName);
  const victims = blast.filter((b) => !chainNodes.has(b.pod) && !mediaNames.has(b.pod) && samePlane(b.pod));
  // The machines the driver reaches: the incident's list, else the graph's blast radius for its root.
  const vicList = inc?.victims?.length ? inc.victims.map((p) => ({ pod: p })) : rootMatches ? victims : [];
  // The number is not a probability (LOG-086). With a root edge it is the link strength: a running
  // average that moves 40 % toward 1 each pass the engine sees the link again, and loses 10 % each
  // pass it does not. Without an edge it is the root's share of the candidate scores, which is 1.00
  // whenever only one candidate is left.
  const conf = rootMatches ? (rootEdge?.confidence ?? root?.score) : null;
  const confEdge = rootEdge?.confidence != null;
  const label = { root: "root cause", forecast: "forecast", steady: "steady", wait: "connecting", error: "engine error",
    incident: "incident", recovering: "recovering" }[state];
  const glyph = { root: "hot", forecast: "strained", steady: "ok", wait: "idle", error: "hot", incident: "alarm",
    recovering: "ok" }[state];
  const blind = scada?.source === "unavailable";
  const phases = (inc?.phases || []).slice(-6);

  return (
    <div className={`vd ${state === "error" ? "root" : state}`}>
      {blind && (
        <div className="vd-band blind">
          <Glyph st="idle" size={10} />
          <span>SCADA blind · last good {d.tagsOkAt ? istTime(d.tagsOkAt) : "?"}</span>
        </div>
      )}
      {integrity.length > 0 && (
        <div className="vd-band integrity">
          <div className="vd-bh"><Glyph st="alarm" size={10} /><span>integrity</span></div>
          {integrity.map((f) => <div key={f.id} className="vd-bi">{integrityText(f)}</div>)}
        </div>
      )}

      <div className="vd-state">
        <Glyph st={glyph} size={10} />
        <span>{label}</span>
        {inc ? <span className="vd-t">{inc.id.replace(/^INC-\d{8}-/, "INC ")} · since {istTs(inc.opened_ts)}</span>
          : state === "root" && root?.onset_s != null && <span className="vd-t">detected in &lt;{Math.ceil(root.onset_s)} s</span>}
      </div>

      {state === "error" && <p className="vd-narr">{graph.meta.error || "engine error"} · last verdict not current</p>}

      {(state === "root" || state === "recovering") && whoName && (
        <>
          <div className="vd-who">{whoName}</div>
          {origin && origin.asset !== whoName && (
            <div className="vd-origin">started by <b>{origin.asset}</b> · {istTs(origin.ts)}</div>
          )}
          {reason && <div className="vd-reason">{reason}</div>}
          {typeof conf === "number" && (
            <div className="vd-kv mid" title={confEdge
              ? "link strength: how steadily the engine has seen this cause-and-effect link, pass after pass. Not a probability."
              : "root share: this machine's part of the candidate scores. 1.00 means no other candidate is left. Not a probability."}>
              <span className="lbl">{confEdge ? "strength" : "share"}</span><Meter v={conf} /><b>{conf.toFixed(2)}</b></div>
          )}
          {rootMatches && rootEdge?.evidence?.length ? (
            <Fold label="evidence" n={rootEdge.evidence.length} open={!!inc}>
              <div className="echips">{rootEdge.evidence.map((e) => <span key={e} className="echip">{e}</span>)}</div>
            </Fold>
          ) : null}
          <div className="vd-kv"><span className="lbl">chain</span>
            <div className="vd-chain">
              {inc && inc.chain?.length > 1
                ? inc.chain.map((n, i) => (
                    <span key={n} className="c-hop">{i > 0 && <i>→</i>}<b className={i === 0 ? "c-root" : "c-relay"}>{n}</b></span>))
                : <b className="c-root">{whoName}</b>}
              {rootMatches && !(inc && inc.chain?.length > 1) && chain.map((h, i) => (
                <span key={i} className="c-hop">
                  <i>→</i><span className="c-med">{h.medium || RES_WORD[rootEdge?.signal] || resWord}</span>
                  {h.node && <><i>→</i><b className="c-relay">{h.node}</b></>}
                </span>))}
              <i>→</i>
              {vicList.length
                ? vicList.map((b) => (
                    <span key={b.pod} className="c-vic">{b.pod}{b.eta_s ? <small> ~{Math.round(b.eta_s)} s</small> : null}</span>))
                : <span className="faint">—</span>}
              {inc?.tripped?.length ? <span className="c-trip">tripped: {inc.tripped.join(", ")}</span> : null}
            </div>
          </div>
        </>
      )}

      {incip.length > 0 && (
        <div className="vd-fc">
          {incip.slice(0, 4).map((f) => {
            const coolant = f.class === "trip" || f.signal === "coolant_temp";
            const frac = f.limit ? (f.value ?? 0) / f.limit : null;
            return (
              <div key={`${f.pod}-${f.signal}`} className="fc-row">
                <Glyph st="strained" />
                <span className="nm">{f.pod}</span>
                <span className="eta">{coolant ? "trip" : "OOM"} in ~{Math.round(f.eta_s ?? 0)} s{f.rate_c_min ? ` · +${f.rate_c_min} °C/min` : ""}</span>
                {frac != null && <Meter v={frac} st="strained" />}
                <span className="val">{coolant
                  ? `${(f.value ?? 0).toFixed(1)}${f.t_inf ? ` → ${f.t_inf.toFixed(0)}` : ""} / ${Math.round(f.limit)} °C`
                  : `${mib(f.value)} / ${mib(f.limit)}`}</span>
              </div>
            );
          })}
        </div>
      )}

      {state !== "error" && <p className="vd-narr">{narr?.text || (state === "wait" ? "…" : "")}</p>}

      {phases.length > 0 && (
        <Fold label="phases" n={inc?.phases?.length} open={!!inc && inc.status !== "recovering"}>
        <div className="vd-ph" aria-label="incident phases">
          {phases.map((p) => (
            <div key={p.n} className={`ph ${p.kind}`}>
              <Glyph st={PHASE_ST[p.kind] || "idle"} size={8} />
              <span className="t">{istTs(p.ts)}</span>
              <span className="tx">{p.text}</span>
            </div>
          ))}
        </div>
        </Fold>
      )}

      {inc && <Ask />}
    </div>
  );
}
