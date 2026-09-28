"use client";
import { useEffect, useState } from "react";
import Glyph from "./Glyph";
import { TagStrip, tagsFor } from "./MachineTags";
import { railSt, machSt, flowSt, tempSt, thruSt, ST_COLOR } from "./lib/palette";
import { PLANT } from "./Brand";

// Overlays on the map well, at the edges so they never cover a machine (LOG-062):
//   top-left     the selected asset as a glass card: state, readings, its tags behind one toggle.
//                It closes with × or Escape and opens again for the next pick.
//   bottom-left  the plant summary, only while the `info` map tool is on
// Every number is a reading from /api/plant, /api/tags, or /api/fleet/tags.
const ink = (st) => (st === "ok" ? undefined : ST_COLOR[st === "trip" ? "hot" : st]);

function Val({ k, v, u, st }) {
  return (
    <div className="hud-val">
      <span className="k">{k}</span>
      <b style={{ color: ink(st || "ok") }}>{v}<small>{u}</small></b>
    </div>
  );
}

export default function MapHud({ d, sel, statusOf, info }) {
  const [closed, setClosed] = useState(false);
  const [showTags, setShowTags] = useState(false);
  useEffect(() => { setClosed(false); setShowTags(false); }, [sel]);   // a new pick opens the card again
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setClosed(true); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const plant = d.plant;
  if (!plant?.devices) return null;
  const dev = plant.devices[sel];
  const rail = plant.rails?.[sel];
  const loop = plant.loop?.name === sel ? plant.loop : null;
  const seg = plant.segments?.[sel];
  const role = statusOf?.(sel);
  const tags = tagsFor(sel, d.scada, d.fleetTags);
  const plcs = d.fleet?.plcs || [];
  const run = plcs.filter((p) => p.state === "RUN").length;
  const cooled = Object.values(plant.devices).filter((x) => x.cooled).length;

  let head = null, vals = null, st = "ok";
  if (dev) {
    st = dev.tripped ? "trip" : role || machSt(dev, dev.trip_c ?? plant.trip_c ?? 80);
    head = <>{sel}<span className="hud-kind">{dev.kind}{dev.controller ? ` · ${dev.controller}` : ""}{dev.cooled ? " · cooled" : ""}</span></>;
    vals = (
      <div className="hud-vals">
        <Val k="draw" v={dev.amps?.toFixed(1)} u="A" />
        {dev.cooled && dev.temp != null && <Val k="temp" v={dev.tripped ? "OPEN" : dev.temp.toFixed(1)} u={dev.tripped ? "" : "°C"} st={dev.tripped ? "trip" : tempSt(dev.temp, dev.trip_c ?? 80)} />}
        <Val k="throughput" v={Math.round(dev.throughput)} u="%" st={thruSt(dev.throughput, dev)} />
        <Val k="rail" v={dev.rail} u="" st={rail ? railSt(rail) : "ok"} />
      </div>
    );
  } else if (rail) {
    st = railSt(rail);
    head = <>rail {sel}<span className="hud-kind">feeder · source {Math.round(rail.v_src)} V</span></>;
    vals = <div className="hud-vals"><Val k="bus" v={rail.volts.toFixed(1)} u="V" st={st} /><Val k="feeder" v={rail.amps?.toFixed(1)} u="A" /></div>;
  } else if (loop) {
    st = flowSt(loop);
    head = <>loop {sel}<span className="hud-kind">coolant · chiller {loop.chiller?.running === false ? "stopped" : "running"}</span></>;
    vals = (
      <div className="hud-vals">
        <Val k="flow" v={loop.flow.toFixed(1)} u="L/min" st={st} />
        <Val k="supply" v={loop.t_supply?.toFixed(1)} u="°C" st={loop.t_supply > (loop.t_setpoint ?? 28) + 1 ? "strained" : "ok"} />
        <Val k="pump" v={Math.round((loop.pump_health ?? 1) * 100)} u="%" />
        <Val k="chiller" v={loop.chiller?.load_pct?.toFixed(0)} u="%" />
      </div>
    );
  } else if (seg) {
    const u = seg.utilization ?? 0;
    st = u >= 1 ? "hot" : u >= 0.8 ? "strained" : "ok";
    head = <>segment {sel}<span className="hud-kind">field network</span></>;
    vals = <div className="hud-vals"><Val k="load" v={Math.round(u * 100)} u="%" st={st} /><Val k="latency" v={(seg.latency_ms ?? 0).toFixed(0)} u="ms" /><Val k="drops" v={Math.round((seg.drop_ratio ?? 0) * 100)} u="%" /></div>;
  }

  return (
    <>
      {head && !closed && (
        <div className={`map-hud ${st}`} role="dialog" aria-label={`${sel} details`}>
          <button className="x" onClick={() => setClosed(true)} aria-label="close" title="close (Esc)">×</button>
          <div className="hud-head"><Glyph st={st === "trip" ? "trip" : st} size={9} /><span className="hud-name">{head}</span></div>
          {vals}
          {tags.length > 0 && (
            <button className="tagtoggle" onClick={() => setShowTags(!showTags)} aria-expanded={showTags}>
              tags ({tags.length}) {showTags ? "−" : "+"}
            </button>
          )}
          {showTags && <TagStrip tags={tags} max={4} />}
        </div>
      )}
      {info && (
        <div className="map-twin" aria-label="plant summary">
          <span className="tw-plant">{PLANT.name}</span>
          <span>{PLANT.site}</span>
          <span>{PLANT.twin}</span>
          <span>{Object.keys(plant.devices).length} machines · {cooled} cooled</span>
          <span>{Object.keys(plant.rails || {}).length} rails · {plant.loop ? "1 loop" : "no loop"}</span>
          {plcs.length > 0 && <span>{run}/{plcs.length} PLCs in run</span>}
        </div>
      )}
    </>
  );
}
