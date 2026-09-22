"use client";
import Glyph from "./Glyph";
import Spark from "./Spark";
import { machSt, railSt, flowSt, tempSt, thruSt, ST_COLOR, QST } from "./lib/palette";
import { fmtTag } from "./lib/format";

// Selected: the detail of one asset (ISA-101 level 3). Full trends for each measured value, and
// the SCADA tags that carried them (quality is the freshness truth, the address is the proof).
// Until the operator picks an asset, it follows the root cause.
const ink = (st) => (st === "ok" ? undefined : ST_COLOR[st]);

function Metric({ k, v, st = "ok", hist, lo, hi, sub }) {
  return (
    <div className="mt-row">
      <span className="k">{k}</span>
      <span className="v" style={{ color: ink(st) }}>{v}</span>
      <span className="sp"><Spark hist={hist} lo={lo} hi={hi} color={ST_COLOR[st]} w={240} h={24} fluid /></span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

function TagTable({ tags }) {
  if (!tags.length) return <div className="empty">no SCADA tags for this asset</div>;
  return (
    <table className="tt">
      <tbody>
        {tags.map((t) => (
          <tr key={t.tag} title={`${t.tag} · ${t.kind}`}>
            <td className="q"><Glyph st={QST[t.quality] || "idle"} size={7} title={t.quality} /></td>
            <td className="k">{t.signal}{t.kind === "derived" ? " ·d" : ""}</td>
            <td className="v">{fmtTag(t)}</td>
            <td className="a">{t.address}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Selected({ plant, scada, hist, sel, picked, onClear }) {
  if (!plant?.devices) return <div className="empty">waiting for plant telemetry…</div>;
  if (!sel) return <div className="empty">Select a machine or a rail in Assets. The root cause opens here on its own.</div>;
  const trip = plant.trip_c ?? 78;
  const tags = (scada?.tags || []).filter((t) => t.asset === sel);
  const d = plant.devices[sel];
  const r = plant.rails?.[sel];
  const loop = plant.loop?.name === sel ? plant.loop : null;
  const seg = plant.segments?.[sel] || null;   // PS3: the field network segment (a queue model)
  const origin = picked ? "operator pick" : "auto · root cause, forecast, or first rail";

  let head, metrics;
  if (d) {
    const st = d.tripped ? "hot" : machSt(d, trip);
    const amps = hist(`m/${sel}/a`);
    head = <><Glyph st={d.tripped ? "trip" : st} size={11} /><span className="nm">{sel}</span>
      <span className="kind">machine · rail {d.rail} · {d.cooled ? "cooled" : "uncooled"}{d.tripped ? " · tripped, contactor open" : ""}</span></>;
    metrics = (
      <>
        <Metric k="draw" v={`${d.amps.toFixed(1)} A`} st={d.tripped ? "hot" : "ok"} hist={amps} lo={0} hi={Math.max(...(amps.length ? amps : [d.amps]), d.amps) * 1.2} />
        {d.cooled && d.temp != null && (
          <Metric k="temp" v={`${d.temp.toFixed(1)} °C`} st={tempSt(d.temp, trip)} hist={hist(`m/${sel}/t`)} lo={30} hi={trip + 8} sub={`trip at ${Math.round(trip)} °C`} />
        )}
        <Metric k="thru" v={`${Math.round(d.throughput)} %`} st={thruSt(d.throughput)} hist={hist(`m/${sel}/p`)} lo={0} hi={100} />
        {d.controller && (
          // 2H: a PLC controls this machine. speed_pct is the simulated drive after its ramp. The
          // commanded value is what the PLC wrote on the field port this tick.
          <Metric k="drive" v={d.speed_pct != null ? `${Math.round(d.speed_pct)} %` : "—"}
            st={d.speed_pct != null && d.speed_pct < 90 ? "strained" : "ok"} hist={hist(`m/${sel}/s`)} lo={0} hi={105}
            sub={`${d.controller}${d.commanded?.run == null ? " · no link" : d.commanded.run ? ` · commanded run ${d.commanded.speed_pct} %` : " · commanded stop"}${d.cell ? ` · cell ${d.cell}` : ""}`} />
        )}
      </>
    );
  } else if (r) {
    const st = railSt(r);
    const members = Object.entries(plant.devices).filter(([, x]) => x.rail === sel);
    head = <><Glyph st={st} size={11} /><span className="nm">rail {sel}</span><span className="kind">shared power rail · source {Math.round(r.v_src)} V</span></>;
    metrics = (
      <>
        <Metric k="volts" v={`${r.volts.toFixed(1)} V`} st={st} hist={hist(`r/${sel}`)} lo={r.v_src * 0.85} hi={r.v_src * 1.01}
          sub={`${((r.volts / r.v_src) * 100).toFixed(1)} % of source · normal from ${Math.round(0.882 * r.v_src)} V`} />
        <div className="mt-members">
          {members.map(([n, x]) => <span key={n}><Glyph st={x.tripped ? "trip" : machSt(x, trip)} size={7} />{n} <b>{x.amps.toFixed(1)} A</b></span>)}
        </div>
      </>
    );
  } else if (loop) {
    const st = flowSt(loop);
    const nom = loop.flow_nominal ?? 120;
    const cooled = Object.entries(plant.devices).filter(([, x]) => x.cooled);
    head = <><Glyph st={st} size={11} /><span className="nm">loop {sel}</span><span className="kind">shared coolant loop · pump {Math.round((loop.pump_health ?? 1) * 100)} %</span></>;
    metrics = (
      <>
        <Metric k="flow" v={`${loop.flow.toFixed(1)} L/min`} st={st} hist={hist("loop")} lo={0} hi={nom * 1.1} sub={`nominal ${Math.round(nom)} L/min · trip at ${Math.round(trip)} °C`} />
        <div className="mt-members">
          {cooled.map(([n, x]) => <span key={n}><Glyph st={x.temp != null ? tempSt(x.temp, trip) : "idle"} size={7} />{n} <b>{x.temp != null ? `${x.temp.toFixed(1)} °C` : "—"}</b></span>)}
        </div>
      </>
    );
  } else if (seg) {
    const u = seg.utilization ?? 0;
    const st = u >= 1 ? "hot" : u >= 0.8 ? "strained" : "ok";
    head = <><Glyph st={st} size={11} /><span className="nm">segment {sel}</span><span className="kind">field network · capacity {Math.round(seg.capacity_fps ?? 0)} frames/s · a queue model</span></>;
    metrics = (
      <>
        <Metric k="load" v={`${Math.round(u * 100)} %`} st={st} hist={[]} lo={0} hi={1.5} sub={`drops ${Math.round((seg.drop_ratio ?? 0) * 100)} % · queue delay ${(seg.latency_ms ?? 0).toFixed(1)} ms`} />
        <div className="mt-members">
          {Object.entries(seg.members || {}).map(([n, m]) => (
            <span key={n}><Glyph st={m.latency_ms > 100 ? "hot" : "ok"} size={7} />{n} <b>{Math.round(m.offered_fps ?? 0)} fps · {Math.round(m.latency_ms ?? 0)} ms</b></span>
          ))}
        </div>
      </>
    );
  } else {
    return <div className="empty">{sel} is not in the plant model. Pick another asset.</div>;
  }

  return (
    <div className="selected">
      <div className="sel-head">
        {head}
        <span className="sel-origin">{origin}</span>
        {picked && <button className="btn sm" onClick={onClear}>follow root</button>}
      </div>
      <div className="sel-grid">
        <div className="sel-metrics">{metrics}</div>
        <div className="sel-tags"><div className="lbl">scada tags · via PLC</div><TagTable tags={tags} /></div>
      </div>
    </div>
  );
}
