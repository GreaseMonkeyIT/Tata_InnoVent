"use client";
import { useEffect, useState } from "react";
import Panel from "./Panel";
import Glyph from "./Glyph";
import CommandBar from "./CommandBar";
import MapPanel from "./MapPanel";
import Assets from "./Assets";
import FaultInjection from "./FaultInjection";
import Verdict from "./Verdict";
import Actions from "./Actions";
import EventLog from "./EventLog";
import Tabs from "./Tabs";
import useHistory from "./lib/useHistory";
import { DEV } from "./lib/api";
import { mockVariant } from "./lib/mock";

// The operator console (LOG-062): one static screen, no page scroll. Every panel keeps a fixed
// place, and only panel bodies scroll. The layout reads left to right in the order of the engine
// pipeline: the symptoms (assets), where they are (map and detail tabs), why (verdict), and what
// to do (actions, then the event log that records it). The operator picked this layout ("A").
export default function Console({ d }) {
  const [mock, setMock] = useState("incident");
  const [plane, setPlane] = useState("floor");   // map: plant floor | edge stack
  const [view, setViewReq] = useState({ mode: "iso", n: 0 });   // floor camera preset request
  const setView = (mode) => setViewReq((v) => ({ mode, n: v.n + 1 }));
  const [tab, setTab] = useState("selected");
  const [picked, setPicked] = useState(null);    // operator pick. null = follow the verdict
  const [focusPlc, setFocusPlc] = useState(null);
  const hist = useHistory(d.plant);

  useEffect(() => { setMock(mockVariant()); }, []);

  // Until the operator picks: the root cause, else the soonest forecast, else the first rail.
  const soonest = [...(d.graph?.incipient || [])].sort((a, b) => (a.eta_s ?? 1e9) - (b.eta_s ?? 1e9))[0];
  const sel = picked || d.derived.root?.pod || soonest?.pod || Object.keys(d.plant?.rails || {})[0] || null;
  const onPick = (kind, id) => {
    if (kind === "plc") { setFocusPlc(id); setTab("fleet"); return; }
    setPicked(id); setTab("selected");
  };
  // Dev build only: switch the mock state for design review. The deployed console has no switch.
  const review = DEV ? {
    mock,
    setMock: (x) => {
      const u = new URL(window.location.href);
      u.searchParams.set("mock", x);
      window.history.replaceState(null, "", u);
      setMock(x); d.reloadAll();
    },
  } : null;

  const plantCount = Object.keys(d.plant?.devices || {}).length;
  const audit = d.audit;
  const actionCount = (d.actions?.proposals?.length || 0) + (d.actions?.active?.length || 0) + d.derived.advisory.length;

  return (
    <main className="console">
      <CommandBar d={d} review={review} />
      <div className="col col-l">
        <Panel id="assets" title="Assets" meta={plantCount ? `${plantCount} machines · simulated` : "simulated plant"}>
          <Assets plant={d.plant} hist={hist} sel={sel} onSelect={(id) => onPick("asset", id)} statusOf={d.derived.statusOf} />
        </Panel>
        <Panel id="faults" title="Fault injection" meta="sim"
          tools={<button className="btn sm warn" onClick={d.resetAll} disabled={d.pending.__all} title="Clear every active plant fault and reset the PLC trip">{d.pending.__all ? "resetting…" : "Reset plant"}</button>}>
          <FaultInjection d={d} />
        </Panel>
      </div>
      <div className="col col-c">
        <MapPanel d={d} plane={plane} setPlane={setPlane} view={view} setView={setView} sel={sel} onPick={onPick} />
        <Tabs d={d} hist={hist} tab={tab} setTab={setTab} sel={sel} picked={picked} clearPick={() => setPicked(null)} focusPlc={focusPlc} />
      </div>
      <div className="col col-r">
        <Panel id="verdict" title="Verdict"
          meta={<><Glyph st={d.health ? (d.health.ok ? "ok" : "hot") : "idle"} size={7} />{d.health?.ok ? "engine online" : d.health ? "engine offline" : "connecting"}</>}>
          <Verdict d={d} />
        </Panel>
        <Panel id="actions" title="Actions" meta={`${actionCount} cited`}>
          <Actions d={d} />
        </Panel>
        <Panel id="log" title="Event log"
          meta={audit ? <><Glyph st={audit.chain_ok ? "ok" : "hot"} size={7} />{audit.chain_ok ? "chain intact" : "CHAIN BROKEN"} · {audit.count ?? 0}</> : "hash-chained ledger"}>
          <EventLog audit={audit} />
        </Panel>
      </div>
    </main>
  );
}
