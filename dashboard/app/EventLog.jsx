"use client";
import { istTs, ledgerText } from "./lib/format";

// Event log: the 2E hash-chained audit ledger, newest first. It holds every state-changing action
// (fire, reset, execute, relief, restore, fleet changes) and every denied attempt, with the
// evidence each action cited. Verb colors follow the console roles: an injected fault is a
// warning, an operator write is a command, a measured relief or a reset is a return to normal.
const VERB_CLS = { trigger: "fire", execute: "exec", relief: "good", reset: "good", restore: "good",
  unsigned: "alarm", balance: "alarm" };
// An integrity row is bad while open (detected, mismatch) and a return to normal when cleared.
const isBad = (s) => s === "denied" || s === "detected" || s === "mismatch" || /^(error|refused)/.test(String(s || ""));
const verbCls = (e) => (e.status === "cleared" ? "good" : VERB_CLS[e.verb] || "");

export default function EventLog({ audit }) {
  if (!audit) return <div className="empty">waiting for the ledger…</div>;
  const rows = [...(audit.entries || [])].reverse();
  if (!rows.length) return <div className="empty">no state-changing actions recorded yet</div>;
  return (
    <div className="evlog">
      {rows.map((e, i) => (
        <div key={e.hash || i} className={`ev-row${isBad(e.status) ? " bad" : ""}`}>
          <span className="t">{istTs(e.ts)}</span>
          <span className={`verb ${verbCls(e)}`}>{e.verb}</span>
          <span className="tgt">{e.target}</span>
          <span className="st">{e.status}</span>
          <span className="who">{e.actor}</span>
          <span className="dt" title={ledgerText(e)}>{ledgerText(e)}</span>
        </div>
      ))}
    </div>
  );
}
