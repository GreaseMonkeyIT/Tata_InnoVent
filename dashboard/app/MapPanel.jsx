"use client";
import dynamic from "next/dynamic";
import Panel from "./Panel";
import Glyph from "./Glyph";
import Floor from "./Floor";
import MapHud from "./MapHud";
import { usePref } from "./Fold";

// The 3D causal graph is WebGL (it touches window), so it renders client-only.
const Graph = dynamic(() => import("./Graph"), { ssr: false });

// The map: the plant floor or the edge stack in a black well. Overlays stay at the edges, so they
// never cover a machine, and each one can be closed. The console never says that a fault is
// injected (LOG-081): the operator sees only what the plant and the engine show.
// The floor uses a normal orbit camera: drag rotates, right-drag pans, the wheel zooms, and a
// click selects an asset (or opens a PLC in the Fleet tab). ISO and PLAN are camera presets;
// `info` shows the plant summary strip; `focus` gives the map the whole screen (Esc leaves it).
export default function MapPanel({ d, plane, setPlane, view, setView, sel, onPick, focus, setFocus }) {
  const floor = plane === "floor";
  const [info, setInfo] = usePref("visr.map.info", false);
  return (
    <Panel id="map" title="Map" flush
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
          <div className="seg" role="group" aria-label="map overlays">
            <button className={info ? "on" : ""} onClick={() => setInfo(!info)} aria-pressed={info} title="show the plant summary">info</button>
            <button className={focus ? "on" : ""} onClick={() => setFocus(!focus)} aria-pressed={focus} title="give the map the whole screen (Esc leaves)">focus</button>
          </div>
        </>
      }>
      <div className="map-well">
        {floor
          ? <Floor plant={d.plant} graph={d.graph} selected={sel} onPick={onPick} view={view} />
          : <div className="graph3d"><Graph graph={d.derived.edgeGraph} topo={d.topo} /></div>}
        {floor && <MapHud d={d} sel={sel} statusOf={d.derived.statusOf} info={info} />}
        <div className="map-legend" aria-hidden="true">
          <span><Glyph st="hot" size={8} />root</span>
          <span><Glyph st="strained" size={8} />affected</span>
          {floor && <span><Glyph st="trip" size={8} />tripped</span>}
        </div>
      </div>
    </Panel>
  );
}
