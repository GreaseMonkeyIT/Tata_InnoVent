"use client";
import Glyph from "./Glyph";
import Spark from "./Spark";
import { railSt, machSt, flowSt, tempSt, thruSt, ST_COLOR } from "./lib/palette";

// Assets outliner: every machine, grouped by the shared medium that carries the causal story
// (rail, coolant loop). One compact row per machine. The full trends and the SCADA tags of a
// machine open in the Selected tab. Values are neutral text while normal and take the warning or
// alarm color only when abnormal, so a calm plant reads calm.
const ink = (st) => (st === "ok" ? undefined : ST_COLOR[st === "trip" ? "hot" : st]);

// ISA-101 analog indicator: a black track, the normal range marked, and a marker at the value.
function Band({ v, lo, hi, okLo, okHi, st }) {
  const pos = (x) => Math.max(0, Math.min(100, ((x - lo) / (hi - lo)) * 100));
  return (
    <span className="band" aria-hidden="true">
      <b style={{ left: `${pos(okLo)}%`, width: `${pos(okHi) - pos(okLo)}%` }} />
      <i style={{ left: `${pos(v)}%`, background: ST_COLOR[st] }} />
    </span>
  );
}

// The glyph shows the engine's verdict role when the machine has one (root cause or blast radius),
// the same encoding the map uses. Otherwise it shows the physical display band. The value columns
// always show the sensors.
function Row({ name, d, trip, hist, sel, role, onSelect }) {
  const st = d.tripped ? "trip" : machSt(d, trip);
  const glyph = d.tripped ? "trip" : role || st;
  const amps = hist(`m/${name}/a`);
  const ampsHi = Math.max(...(amps.length ? amps : [d.amps]), d.amps) * 1.2;
  const derated = d.controller && d.speed_pct != null && d.speed_pct < 99;
  const temp = d.tripped ? "OPEN" : d.cooled && d.temp != null ? <>{d.temp.toFixed(1)}<small>°C</small></> : <span className="nil">·</span>;
  const roleText = role === "hot" ? " · root cause" : role === "strained" ? " · blast radius" : "";
  return (
    <button className={`as-row${sel ? " sel" : ""}`} onClick={() => onSelect(name)}
      title={`${name}${roleText} · rail ${d.rail}${d.controller ? ` · ${d.controller}` : ""}${d.cooled ? " · cooled" : ""}`}>
      <Glyph st={glyph} title={roleText ? roleText.slice(3) : undefined} />
      <span className="nm">
        <span className="t" style={{ color: role === "hot" ? "var(--red)" : undefined }}>{name}</span>
        {derated && <em className="as-drv">▼{Math.round(d.speed_pct)}%</em>}
      </span>
      <span className="v">{d.amps.toFixed(1)}<small>A</small></span>
      <span className="sp"><Spark hist={amps} lo={0} hi={ampsHi} color={ST_COLOR[st === "trip" ? "hot" : st]} w={46} h={16} /></span>
      <span className="v" style={{ color: d.tripped ? "var(--red)" : d.cooled && d.temp != null ? ink(tempSt(d.temp, trip)) : undefined }}>{temp}</span>
      <span className="v" style={{ color: ink(thruSt(d.throughput)) }}>{Math.round(d.throughput)}<small>%</small></span>
    </button>
  );
}

export default function Assets({ plant, hist, sel, onSelect, statusOf }) {
  if (!plant) return <div className="empty">waiting for plant telemetry…</div>;
  if (plant.source === "unavailable" || !plant.devices) return <div className="empty">plant sim unreachable</div>;
  const trip = plant.trip_c ?? 78;
  const devs = Object.entries(plant.devices);
  const loop = plant.loop;
  const lst = flowSt(loop);
  const nom = loop?.flow_nominal ?? 120;
  return (
    <div className="assets">
      <div className="as-cols" aria-hidden="true"><span /><span>asset</span><span>draw</span><span /><span>temp</span><span>thru</span></div>
      {Object.entries(plant.rails || {}).map(([rn, r]) => {
        const st = railSt(r);
        return (
          <div key={rn} className="as-rail">
            <button className={`as-rhead${sel === rn ? " sel" : ""}`} onClick={() => onSelect(rn)} title={`rail ${rn} · source ${Math.round(r.v_src)} V`}>
              <Glyph st={st} />
              <span className="rn">rail {rn}</span>
              <Band v={r.volts} lo={0.85 * r.v_src} hi={1.01 * r.v_src} okLo={0.882 * r.v_src} okHi={r.v_src} st={st} />
              <span className="rv" style={{ color: ink(st) }}>{r.volts.toFixed(1)}<small>V</small></span>
            </button>
            {r.amps != null && <div className="as-loopnote">feeder meter {r.amps.toFixed(1)} A</div>}
            {devs.filter(([, d]) => d.rail === rn).map(([dn, d]) => (
              <Row key={dn} name={dn} d={d} trip={trip} hist={hist} sel={sel === dn} role={statusOf(dn)} onSelect={onSelect} />
            ))}
          </div>
        );
      })}
      {loop && (
        <div className="as-rail">
          <button className={`as-rhead${sel === loop.name ? " sel" : ""}`} onClick={() => onSelect(loop.name)} title={`coolant loop ${loop.name}`}>
            <Glyph st={lst} />
            <span className="rn">loop {loop.name}</span>
            <Band v={loop.flow} lo={0} hi={nom * 1.1} okLo={0.85 * nom} okHi={nom * 1.1} st={lst} />
            <span className="rv" style={{ color: ink(lst) }}>{loop.flow.toFixed(1)}<small>L/min</small></span>
          </button>
          <div className="as-loopnote">pump {Math.round((loop.pump_health ?? 1) * 100)} % · trip {Math.round(trip)} °C · nominal {Math.round(nom)} L/min</div>
          {Object.entries(plant.devices).filter(([, d]) => d.trip_reason).map(([dn, d]) => (
            <div key={dn} className="as-loopnote hot">{dn} tripped · {d.trip_reason}</div>
          ))}
        </div>
      )}
      {Object.entries(plant.segments || {}).map(([sn, sg]) => {
        const u = sg.utilization ?? 0;
        const sst = u >= 1 ? "hot" : u >= 0.8 ? "strained" : "ok";
        return (
          <div key={sn} className="as-rail">
            <button className={`as-rhead${sel === sn ? " sel" : ""}`} onClick={() => onSelect(sn)} title={`field network segment ${sn} (a queue model)`}>
              <Glyph st={sst} />
              <span className="rn">segment {sn}</span>
              <Band v={u} lo={0} hi={1.5} okLo={0} okHi={0.8} st={sst} />
              <span className="rv" style={{ color: ink(sst) }}>{(sg.latency_ms ?? 0).toFixed(0)}<small>ms</small></span>
            </button>
            <div className="as-loopnote">load {Math.round(u * 100)} % · drops {Math.round((sg.drop_ratio ?? 0) * 100)} % · {Object.entries(sg.members || {}).map(([m, v]) => `${m} ${Math.round(v.offered_fps ?? 0)} fps`).join(" · ")}</div>
          </div>
        );
      })}
    </div>
  );
}
