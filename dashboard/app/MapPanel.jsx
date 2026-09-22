"use client";
import dynamic from "next/dynamic";
import Panel from "./Panel";
import Glyph from "./Glyph";
import Floor from "./Floor";
import { scenarioName } from "./lib/format";

// The 3D causal graph is WebGL (it touches window), so it renders client-only.
const Graph = dynamic(() => import("./Graph"), { ssr: false });

// The map: the plant floor or the edge stack in a black well. Overlays stay at the edges, so
// they never cover a machine. The fault banner shows only while the sim has an injected fault.
// The floor uses a normal orbit camera: drag rotates, right-drag pans, the wheel zooms, and a
// click selects an asset (or opens a PLC in the Fleet tab). ISO and PLAN are camera presets.
// A click on the active preset resets the view.
export default function MapPanel({ d, plane, setPlane, view, setView, sel, onPick }) {
  const faults = d.plant?.active_faults || [];
  const floor = plane === "floor";
  return (
    <Panel id="map" title="Map" meta={floor ? "plant floor · drag rotate · right-drag pan · wheel zoom · click select" : "edge stack · force graph"} flush
      tools={
        <>
          {floor && (
            <div className="seg" role="group" aria-label="camera preset">
              <button className={view.mode === "iso" ? "on" : ""} onClick={() => setView("iso")} title="3D hall view. Click again to reset the camera.">iso</button>
              <button className={view.mode === "plan" ? "on" : ""} onClick={() => setView("plan")} title="Top-down schematic. Click again to reset the camera.">plan</button>
            </div>
          )}
          <div className="seg" role="group" aria-label="causal plane">
            <button className={floor ? "on" : ""} onClick={() => setPlane("floor")}>floor</button>
            <button className={!floor ? "on" : ""} onClick={() => setPlane("edge")}>edge</button>
          </div>
        </>
      }>
      <div className="map-well">
        {floor
          ? <Floor plant={d.plant} graph={d.graph} selected={sel} onPick={onPick} view={view} />
          : <div className="graph3d"><Graph graph={d.derived.edgeGraph} topo={d.topo} /></div>}
        {faults.length > 0 && (
          <div className="map-banner">
            {faults.map((f) => <span key={f}><Glyph st="strained" size={9} />{scenarioName(f)} injected · simulated fault</span>)}
          </div>
        )}
        <div className="map-cap">
          {floor ? "plant: physics-simulated · inference: real" : "edge node: real kernel telemetry · topology discovered by eBPF"}
        </div>
        <div className="map-legend" aria-hidden="true">
          <span><Glyph st="hot" size={8} />root</span>
          <span><Glyph st="strained" size={8} />blast radius · forecast</span>
          <span><Glyph st="ok" size={8} />normal</span>
          {floor && <span><Glyph st="trip" size={8} />tripped</span>}
        </div>
      </div>
    </Panel>
  );
}
