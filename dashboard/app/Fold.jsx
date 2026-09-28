"use client";
import { useEffect, useState } from "react";

// A collapsible section with one small toggle in its label. `open` sets the default and follows
// changes to it (an incident opening its phases), and the operator's click wins until the next change.
export default function Fold({ label, n, open = false, children }) {
  const [on, setOn] = useState(open);
  useEffect(() => { setOn(open); }, [open]);
  return (
    <div className={`fold${on ? " on" : ""}`}>
      <button className="fold-h" onClick={() => setOn(!on)} aria-expanded={on}>
        <span className="lbl">{label}{n != null ? ` (${n})` : ""}</span>
        <span className="fold-c" aria-hidden="true">{on ? "−" : "+"}</span>
      </button>
      {on && <div className="fold-b">{children}</div>}
    </div>
  );
}

// One persisted operator preference: a `visr.*` key in this browser only.
export function usePref(key, initial) {
  const [v, setV] = useState(initial);
  useEffect(() => { try { const s = localStorage.getItem(key); if (s !== null) setV(JSON.parse(s)); } catch { /* defaults */ } }, [key]);
  const set = (x) => { setV(x); try { localStorage.setItem(key, JSON.stringify(x)); } catch { /* not kept */ } };
  return [v, set];
}