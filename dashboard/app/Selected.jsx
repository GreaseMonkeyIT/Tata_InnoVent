"use client";
import Glyph from "./Glyph";
import { DEV } from "./lib/api";
import { machSt, railSt, flowSt, tempSt, thruSt, ST_COLOR, QST } from "./lib/palette";
import { fmtTag } from "./lib/format";

// Selected: the detail of one asset (ISA-101 level 3). Three parts: the current values, one Grafana
// trend queried for this asset only, and the SCADA tags that carried the values (quality is the
// freshness truth, the address is the proof). Until the operator picks an asset, it follows the
// root cause. The trend is panel 4 of the skn-plant dashboard, with var-asset set to the asset
// (deploy/grafana-plant-dashboard.yaml). It loads through the console's own /grafana/ route.
const ink = (st) => (st === "ok" ? undefined : ST_COLOR[st]);

function Metric({ k, v, st = "ok", sub }) {
  return (
    <div className="mt-row">
      <span className="k">{k}</span>
      <span className="v" style={{ color: ink(st) }}>{v}</span>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}

const trendSrc = (asset) =>
  `/grafana/d-solo/skn-plant/skn-plant?orgId=1&panelId=4&var-asset=${encodeURIComponent(asset)}` +
  "&theme=dark&from=now-15m&to=now&refresh=5s&timezone=Asia/Kolkata";

function Trend({ asset }) {
  return (
    <div className="sel-trend">
      {DEV
        ? <div className="gph">Grafana · skn-plant #4 · {asset}<br />loads on the box through /grafana/</div>
        : <iframe key={asset} title={`${asset} trend`} src={trendSrc(asset)} />}
    </div>
  );
}

// The asset's kind, rail and cooling as chips on the line under the name. An item is a label, or
// [label, state] for a chip in a state colour. false items drop out.
function Kinds({ items }) {
  return (
    <div className="sel-kinds">
      {items.filter(Boolean).map((x) => {
        const [label, st] = Array.isArray(x) ? x : [x, null];
        return <span key={label} className={`sel-kind${st ? ` ${st}` : ""}`}>{label}</span>;
      })}
    </div>
  );
}

function TagTable({ tags }) {
  if (!tags.length) return <div className="empty">no tags</div>;
  return (
    <table className="tt">
      <tbody>
        {tags.map((t) => (
          <tr key={t.tag} title={`${t.tag} · ${t.kind}`}>
            <td className="q"><Glyph st={QST[t.quality] || "idle"} size={7} title={t.quality} /></td>
            <td className="k">{t.signal}</td>
            <td className="v">{fmtTag(t)}</td>
            <td className="a">{t.address}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Selected({ plant, scada, sel, picked, auto, hasRoot, onClear }) {
  if (!plant?.devices) return <div className="empty">waiting for plant telemetry…</div>;
  if (!sel) return <div className="empty">no asset selected</div>;
  const trip = plant.trip_c ?? 78;
  // measured tags only: the derived ones repeat a value shown elsewhere (the Tags tab has them all)
  const tags = (scada?.tags || []).filter((t) => t.asset === sel && t.kind !== "derived");
  const d = plant.devices[sel];
  const r = plant.rails?.[sel];
  const loop = plant.loop?.name === sel ? plant.loop : null;
  const seg = plant.segments?.[sel] || null;   // PS3: the field network segment (a queue model)
  // One control on the right: a pick shows the button back to the root cause (or to auto mode when
  // there is no root). Auto mode shows one label that names why this asset is open.
  const origin = picked
    ? <button className="btn cmd sel-follow" onClick={onClear}>{hasRoot ? "Go to root cause" : "Auto mode"}</button>
    : <span className="sel-origin">{auto === "root" ? "Auto · root cause" : auto === "forecast" ? "Auto · forecast" : "Auto mode"}</span>;

  let head, metrics;
  if (d) {
    const st = d.tripped ? "hot" : machSt(d, trip);
    head = <><Glyph st={d.tripped ? "trip" : st} size={11} /><span className="nm">{sel}</span>
      <Kinds items={["machine", `rail ${d.rail}`, d.cooled ? "cooled" : "uncooled", d.tripped && ["trip", "hot"]]} /></>;
    metrics = (
      <>
        <Metric k="draw" v={`${d.amps.toFixed(1)} A`} st={d.tripped ? "hot" : "ok"} />
        {d.cooled && d.temp != null && (
          <Metric k="temp" v={`${d.temp.toFixed(1)} °C`} st={tempSt(d.temp, trip)} sub={`trip ${Math.round(trip)} °C`} />
        )}
        <Metric k="thru" v={`${Math.round(d.throughput)} %`} st={thruSt(d.throughput)} />
        {d.controller && (
          // 2H: a PLC controls this machine. speed_pct is the simulated drive after its ramp. The
          // commanded value is what the PLC wrote on the field port this tick.
          <Metric k="drive" v={d.speed_pct != null ? `${Math.round(d.speed_pct)} %` : "—"}
            st={d.speed_pct != null && d.speed_pct < 90 ? "strained" : "ok"}
            sub={`${d.controller}${d.commanded?.run == null ? " · NO LINK" : d.commanded.run ? ` · set ${d.commanded.speed_pct} %` : " · STOP"}`} />
        )}
      </>
    );
  } else if (r) {
    const st = railSt(r);
    const members = Object.entries(plant.devices).filter(([, x]) => x.rail === sel);
    head = <><Glyph st={st} size={11} /><span className="nm">rail {sel}</span><Kinds items={["rail", `source ${Math.round(r.v_src)} V`]} /></>;
    metrics = (
      <>
        <Metric k="volts" v={`${r.volts.toFixed(1)} V`} st={st}
          sub={`low ${Math.round(0.882 * r.v_src)} V`} />
        <div className="mt-members">
          {members.map(([n, x]) => <span key={n}><Glyph st={x.tripped ? "trip" : machSt(x, trip)} size={7} />{n} <b>{x.amps.toFixed(1)} A</b></span>)}
        </div>
      </>
    );
  } else if (loop) {
    const st = flowSt(loop);
    const nom = loop.flow_nominal ?? 120;
    const cooled = Object.entries(plant.devices).filter(([, x]) => x.cooled);
    head = <><Glyph st={st} size={11} /><span className="nm">loop {sel}</span><Kinds items={["coolant loop", `pump ${Math.round((loop.pump_health ?? 1) * 100)} %`]} /></>;
    metrics = (
      <>
        <Metric k="flow" v={`${loop.flow.toFixed(1)} L/min`} st={st} sub={`nominal ${Math.round(nom)} L/min`} />
        <div className="mt-members">
          {cooled.map(([n, x]) => <span key={n}><Glyph st={x.temp != null ? tempSt(x.temp, trip) : "idle"} size={7} />{n} <b>{x.temp != null ? `${x.temp.toFixed(1)} °C` : "—"}</b></span>)}
        </div>
      </>
    );
  } else if (seg) {
    const u = seg.utilization ?? 0;
    const st = u >= 1 ? "hot" : u >= 0.8 ? "strained" : "ok";
    head = <><Glyph st={st} size={11} /><span className="nm">segment {sel}</span><Kinds items={["field network", `capacity ${Math.round(seg.capacity_fps ?? 0)} frames/s`]} /></>;
    metrics = (
      <>
        <Metric k="load" v={`${Math.round(u * 100)} %`} st={st} sub={`drops ${Math.round((seg.drop_ratio ?? 0) * 100)} % · queue delay ${(seg.latency_ms ?? 0).toFixed(1)} ms`} />
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
        {origin}
      </div>
      <div className="sel-grid">
        <div className="sel-metrics">{metrics}</div>
        <Trend asset={sel} />
        <div className="sel-tags"><div className="lbl">scada tags</div><TagTable tags={tags} /></div>
      </div>
    </div>
  );
}
