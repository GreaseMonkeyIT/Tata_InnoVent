"use client";
import { useState } from "react";
import { send } from "./lib/api";

// 3D act loop (FLEET.md sections 10 and 11): explain -> recommend -> ACT, with a human.
// A proposal exists only while the CURRENT verdict cites it. Execute opens the confirmation inside
// the card, so the map and the verdict stay in view while the operator reads it. The API
// re-derives the proposal (a changed verdict answers 409), SCADA writes one bounded setpoint over
// the PLC protocol, and the ledger records the action, its citation, and the relief measured after
// it. The ledger rows show in the Event log panel. If the verdict changes while the card is open,
// the proposal leaves and the confirmation leaves with it.
export default function ActLoop({ actions, onChanged }) {
  const [confirm, setConfirm] = useState(null);   // id of the proposal under confirmation
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const proposals = actions?.proposals || [];
  const active = actions?.active || [];
  const blocked = actions?.blocked || [];   // SCENARIOS.md 5.2: assets whose PLC channel is under suspicion

  async function execute(p) {
    setBusy(true); setMsg("writing the setpoint…");
    try {
      const r = await send("POST", "/api/actions/execute", { id: p.id });
      setMsg(`executed: ${p.tag} → ${p.to} % · relief measured in ${Math.round(r.relief_check_s)} s`);
    } catch (e) {
      setMsg(String(e.message || e));
    } finally {
      setConfirm(null); setBusy(false); onChanged();
    }
  }

  async function restore(a) {
    setBusy(true); setMsg(`restoring ${a.asset}…`);
    try { await send("POST", "/api/actions/restore", { asset: a.asset }); setMsg(`${a.asset} restored to 100 %`); onChanged(); }
    catch (e) { setMsg(String(e.message || e)); }
    finally { setBusy(false); }
  }

  if (!proposals.length && !active.length && !blocked.length) return msg ? <div className="plc-msg">{msg}</div> : null;
  return (
    <div className="actloop">
      {blocked.map((b) => (
        <div key={`blocked-${b.asset}`} className="rec held">
          <span className="actpill alarm">blocked</span>
          <div className="b">
            <div className="nm">no action through {b.plc} for {b.asset}</div>
            <div className="ct"><b>why:</b> {b.reason}. VISR does not act through a controller whose report it cannot trust.</div>
          </div>
        </div>
      ))}
      {proposals.map((p) => {
        const open = confirm === p.id;
        return (
          <div key={p.id} className={`rec exec${open ? " confirming" : ""}`}>
            <span className="actpill act">{p.verb}</span>
            <div className="b">
              <div className="nm">{p.asset} → {p.to} % via {p.plc}</div>
              {!open ? (
                <>
                  <div className="ds">{p.expected}. One bounded setpoint, written over the PLC protocol.</div>
                  <div className="ct"><b>cites:</b> root {p.cites.root} · edge {p.cites.edge} · {(p.cites.evidence || []).join("+")}{p.cites.confidence != null ? ` · ${p.cites.confidence.toFixed(2)} confidence` : ""}</div>
                </>
              ) : (
                <div className="confirm" role="group" aria-label={`confirm ${p.verb}`}>
                  <div className="lbl">human confirmation · audited</div>
                  <div className="kv"><span>write</span><b>{p.tag}: {p.from} → {p.to}</b></div>
                  <div className="kv"><span>through</span><b>{p.plc} (SCADA setpoint write)</b></div>
                  <div className="kv"><span>cites</span><b>root {p.cites.root} · {p.cites.edge}</b></div>
                  <div className="echips">{(p.cites.evidence || []).map((x) => <span key={x} className="echip">{x}</span>)}</div>
                  <div className="confirm-note">Expected: {p.expected}. The ledger records the write now and the measured relief after it. If the verdict changes before you confirm, the API refuses.</div>
                  <div className="confirm-f">
                    <button className="btn sm" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
                    <button className="btn sm cmd" disabled={busy} onClick={() => execute(p)}>{busy ? "writing…" : "Confirm and execute"}</button>
                  </div>
                </div>
              )}
            </div>
            {!open && <button className="btn cmd" disabled={busy || actions?.write !== "enabled"} onClick={() => setConfirm(p.id)}>Execute</button>}
          </div>
        );
      })}
      {active.map((a) => (
        <div key={a.tag} className="rec held">
          <span className={`actpill ${a.signed === false ? "alarm" : "warn"}`}>{a.signed === false ? "unsigned" : "holding"}</span>
          <div className="b">
            <div className="nm">{a.asset} held at {Math.round(a.value)} % by {a.plc}</div>
            <div className="ct"><b>tag:</b> {a.tag} · {a.quality}{a.signed === false ? " · no signed ledger row wrote this value" : ""}</div>
          </div>
          <button className="btn" disabled={busy} onClick={() => restore(a)}>Restore</button>
        </div>
      ))}
      {msg && <div className="plc-msg">{msg}</div>}
    </div>
  );
}
