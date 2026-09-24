"use client";
import Glyph from "./Glyph";
import { RES_WORD, mib, istTime, num } from "./lib/format";

// The verdict: one answer instead of an alarm flood. Four states, each with its own shape and
// color: STEADY (the engine is silent), FORECAST (no root yet, but a limit is near), ROOT CAUSE
// (the witness gate accepted an edge), and ENGINE ERROR. Two bands sit above the state when they
// apply (SCENARIOS.md 7): INTEGRITY (a PLC report or a setpoint contradicts the physics or the
// ledger) and BLIND (the SCADA view is down, so the console says so instead of looking calm).
// The engine decides the verdict. The narrator only words it.

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

export default function Verdict({ d }) {
  const { root, rootEdge, blast, resWord, plantSet, chain, chainNodes, mediaNames, integrity } = d.derived;
  const { plant, narr, graph, scada } = d;
  const incip = [...(graph?.incipient || [])].sort((a, b) => (a.eta_s ?? 1e9) - (b.eta_s ?? 1e9));
  const engineErr = graph?.meta?.status === "error";
  const state = !graph ? "wait" : engineErr ? "error" : root ? "root" : incip.length ? "forecast" : "steady";
  // The chain stays on the root's own plane. Domain witnesses never join the plant and the pods.
  const samePlane = (pod) => plantSet.has(pod) === plantSet.has(root?.pod);
  const victims = blast.filter((b) => !chainNodes.has(b.pod) && !mediaNames.has(b.pod) && samePlane(b.pod));
  // The number is not a probability (LOG-086). With a root edge it is the link strength: a running
  // average that moves 40 % toward 1 each pass the engine sees the link again, and loses 10 % each
  // pass it does not. Without an edge it is the root's share of the candidate scores, which is 1.00
  // whenever only one candidate is left.
  const conf = rootEdge?.confidence ?? root?.score;
  const confEdge = rootEdge?.confidence != null;
  const label = { root: "root cause", forecast: "forecast", steady: "steady", wait: "connecting", error: "engine error" }[state];
  const glyph = { root: "hot", forecast: "strained", steady: "ok", wait: "idle", error: "hot" }[state];
  const blind = scada?.source === "unavailable";

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
        {state === "root" && root.onset_s != null && <span className="vd-t">detected in &lt;{Math.ceil(root.onset_s)} s</span>}
      </div>

      {state === "error" && <p className="vd-narr">{graph.meta.error || "engine error"} · last verdict not current</p>}

      {state === "root" && (
        <>
          <div className="vd-who">{root.pod}</div>
          {typeof conf === "number" && (
            <div className="vd-kv mid" title={confEdge
              ? "link strength: how steadily the engine has seen this cause-and-effect link, pass after pass. Not a probability."
              : "root share: this machine's part of the candidate scores. 1.00 means no other candidate is left. Not a probability."}>
              <span className="lbl">{confEdge ? "strength" : "share"}</span><Meter v={conf} /><b>{conf.toFixed(2)}</b></div>
          )}
          {rootEdge?.evidence?.length ? (
            <div className="vd-kv"><span className="lbl">evidence</span>
              <div className="echips">{rootEdge.evidence.map((e) => <span key={e} className="echip">{e}</span>)}</div>
            </div>
          ) : null}
          <div className="vd-kv"><span className="lbl">chain</span>
            <div className="vd-chain">
              <b className="c-root">{root.pod}</b>
              {chain.length
                ? chain.map((h, i) => (
                    <span key={i} className="c-hop">
                      <i>→</i><span className="c-med">{h.medium || RES_WORD[rootEdge?.signal] || resWord}</span>
                      {h.node && <><i>→</i><b className="c-relay">{h.node}</b></>}
                    </span>))
                : <><i>→</i><span className="c-med">{RES_WORD[rootEdge?.signal] || resWord}</span></>}
              <i>→</i>
              {victims.length
                ? victims.map((b) => <span key={b.pod} className="c-vic">{b.pod}{b.eta_s ? <small> ~{Math.round(b.eta_s)} s</small> : null}</span>)
                : <span className="faint">—</span>}
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
                <span className="eta">{coolant ? "trip" : "OOM"} in ~{Math.round(f.eta_s ?? 0)} s</span>
                {frac != null && <Meter v={frac} st="strained" />}
                <span className="val">{coolant ? `${(f.value ?? 0).toFixed(1)} / ${Math.round(f.limit)} °C` : `${mib(f.value)} / ${mib(f.limit)}`}</span>
              </div>
            );
          })}
        </div>
      )}

      {state !== "error" && <p className="vd-narr">{narr?.text || (state === "wait" ? "…" : "")}</p>}
    </div>
  );
}
