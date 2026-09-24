"use client";
import { useEffect, useRef, useState } from "react";
import Panel from "./Panel";
import Glyph from "./Glyph";
import CommandBar, { TEXT_SIZES } from "./CommandBar";
import MapPanel from "./MapPanel";
import Assets from "./Assets";
import Verdict from "./Verdict";
import Actions from "./Actions";
import EventLog from "./EventLog";
import Tabs from "./Tabs";
import Split from "./Split";
import useHistory from "./lib/useHistory";
import { DEV } from "./lib/api";
import { mockVariant } from "./lib/mock";

// The operator console (LOG-062): one static screen, no page scroll. Every panel keeps a fixed
// place, and only panel bodies scroll. The layout reads left to right in the order of the engine
// pipeline: the symptoms (assets, and the event log under them), where they are (map and detail
// tabs), why (verdict), and what to do (actions). The operator picked this layout ("A").
// LOG-081: the operator can drag the gaps between panels and columns to resize them. The sizes
// persist in this browser only, and a double-click on a gap resets it. Faults are not fired from
// the console: deploy/faults.sh runs them from a shell on the box.
const LAY_KEY = "visr.layout";
const TXT_KEY = "visr.text";
const LAY_VAR = { colL: "--col-l", colR: "--col-r", logH: "--log-h", tabsH: "--tabs-h", actionsH: "--actions-h" };
export default function Console({ d }) {
  const [mock, setMock] = useState("incident");
  const [plane, setPlane] = useState("floor");   // map: plant floor | edge stack
  const [view, setViewReq] = useState({ mode: "iso", n: 0 });   // floor camera preset request
  const setView = (mode) => setViewReq((v) => ({ mode, n: v.n + 1 }));
  const [tab, setTab] = useState("selected");
  const [picked, setPicked] = useState(null);    // operator pick. null = follow the verdict
  const [focusPlc, setFocusPlc] = useState(null);
  const hist = useHistory(d.plant);
  const [lay, setLay] = useState({});             // operator panel sizes in px, by LAY_VAR key
  const layRef = useRef(lay);
  layRef.current = lay;

  useEffect(() => { setMock(mockVariant()); }, []);
  const [txt, setTxt] = useState(1);              // operator text size for the panel bodies (LOG-084)
  useEffect(() => {
    try {
      const x = JSON.parse(localStorage.getItem(LAY_KEY) || "{}");
      if (x && typeof x === "object") setLay(x);
      const t = Number(localStorage.getItem(TXT_KEY));
      if (TEXT_SIZES.includes(t)) setTxt(t);
    } catch { /* no storage: the default layout and text size */ }
  }, []);
  const setText = (t) => { setTxt(t); try { localStorage.setItem(TXT_KEY, String(t)); } catch { /* not kept */ } };
  const saveLay = (x) => { try { localStorage.setItem(LAY_KEY, JSON.stringify(x)); } catch { /* not kept */ } };
  const onSize = (k, v, done) => (done ? saveLay(layRef.current) : setLay((l) => ({ ...l, [k]: v })));
  const onReset = (k) => setLay((l) => { const n = { ...l }; delete n[k]; saveLay(n); return n; });
  const layStyle = Object.fromEntries(Object.entries(LAY_VAR).filter(([k]) => lay[k]).map(([k, v]) => [v, `${lay[k]}px`]));
  if (txt !== 1) layStyle["--txt"] = txt;
  const vw = typeof window === "undefined" ? 1920 : window.innerWidth;
  const sp = { onSize, onReset };

  // Until the operator picks: the root cause, else the soonest forecast, else the first rail.
  const soonest = [...(d.graph?.incipient || [])].sort((a, b) => (a.eta_s ?? 1e9) - (b.eta_s ?? 1e9))[0];
  const sel = picked || d.derived.root?.pod || soonest?.pod || Object.keys(d.plant?.rails || {})[0] || null;
  const auto = picked ? null : d.derived.root?.pod ? "root" : soonest?.pod ? "forecast" : "rail";
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

  const audit = d.audit;
  const actionCount = (d.actions?.proposals?.length || 0) + (d.actions?.active?.length || 0) + d.derived.advisory.length;

  return (
    <main className="console" style={layStyle}>
      <CommandBar d={d} review={review} text={{ size: txt, setSize: setText }} />
      <div className="col col-l">
        <Panel id="assets" title="Assets">
          <Assets plant={d.plant} hist={hist} sel={sel} onSelect={(id) => onPick("asset", id)} statusOf={d.derived.statusOf} />
        </Panel>
        <Split dir="h" size="logH" of="next" sign={-1} min={90} max={800} {...sp} />
        <Panel id="log" title="Event log"
          meta={audit && !audit.chain_ok ? <><Glyph st="hot" size={7} />CHAIN BROKEN</> : null}>
          <EventLog audit={audit} />
        </Panel>
        <Split dir="v" size="colL" of="parent" min={240} max={Math.max(260, vw - (lay.colR || 400) - 480)} {...sp} />
      </div>
      <div className="col col-c">
        <MapPanel d={d} plane={plane} setPlane={setPlane} view={view} setView={setView} sel={sel} onPick={onPick} />
        <Split dir="h" size="tabsH" of="next" sign={-1} min={140} max={900} {...sp} />
        <Tabs d={d} tab={tab} setTab={setTab} sel={sel} picked={picked} auto={auto} clearPick={() => setPicked(null)} focusPlc={focusPlc} />
      </div>
      <div className="col col-r">
        <Split dir="v" size="colR" of="parent" sign={-1} min={280} max={Math.max(300, vw - (lay.colL || 360) - 480)} {...sp} />
        <Panel id="verdict" title="Verdict"
          meta={d.health && !d.health.ok ? <><Glyph st="hot" size={7} />engine offline</> : null}>
          <Verdict d={d} />
        </Panel>
        <Split dir="h" size="actionsH" of="next" sign={-1} min={90} max={900} {...sp} />
        <Panel id="actions" title="Actions" meta={actionCount || null}>
          <Actions d={d} />
        </Panel>
      </div>
    </main>
  );
}
