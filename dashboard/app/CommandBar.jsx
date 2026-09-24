"use client";
import Glyph from "./Glyph";
import { DEV } from "./lib/api";
import { istTime } from "./lib/format";

// Command bar: the brand plate and one lamp per system the operator depends on (ISA-101 level 1).
// The lamps repeat the Boot probes for the whole session, so a failure never hides below the fold.
function Lamp({ st, k, v, title }) {
  return (
    <div className="lamp" title={title}>
      <Glyph st={st} size={8} />
      <span className="k">{k}</span>
      {v != null && <span className="v">{v}</span>}
    </div>
  );
}

const svcSt = (s) => (s === "up" ? "ok" : s === "down" ? "hot" : "idle");

// Design review only (dev build): switch the mock state without editing the URL.
function Review({ mock, setMock }) {
  return (
    <div className="review" title="Design review control. The dev build shows it, the deployed console does not.">
      <span>mock</span>
      <div className="seg">
        {["incident", "forecast", "steady", "chain", "network", "integrity", "blind"].map((x) => <button key={x} className={mock === x ? "on" : ""} onClick={() => setMock(x)}>{x}</button>)}
      </div>
    </div>
  );
}

// LOG-084: the operator's text size for the panel bodies. The panels keep their size.
export const TEXT_SIZES = [1, 1.15, 1.3];
function TextSize({ size, setSize }) {
  return (
    <div className="seg txt-size" role="group" aria-label="text size">
      {TEXT_SIZES.map((x, i) => (
        <button key={x} className={size === x ? "on" : ""} onClick={() => setSize(x)} title={`text ${Math.round(x * 100)} %`}
          aria-label={`text ${Math.round(x * 100)} %`}>{"A" + "+".repeat(i)}</button>
      ))}
    </div>
  );
}

export default function CommandBar({ d, review, text }) {
  const svc = d.health?.services || {};
  const plcs = d.fleet?.plcs || [];
  const run = plcs.filter((p) => p.state === "RUN").length;
  const fleetOff = !d.fleet || d.fleet.source === "unavailable";
  const hist = d.scada?.historian;
  const auth = d.health?.auth || d.audit?.auth;
  const integ = d.derived.integrity || [];
  const tagsDown = d.scada?.source === "unavailable";
  // A frozen clock was the only sign of a failed refresh. Now the clock itself turns amber at 15 s
  // and red at 30 s without a full update, so a stale screen never passes as a calm one.
  const age = d.updated ? (d.now - d.updated.getTime()) / 1000 : null;
  const clockSt = age == null ? "" : age > 30 ? " stale-hot" : age > 15 ? " stale-warn" : "";
  return (
    <header className="cmdbar">
      <div className="brand">
        <span className="brand-plate">VISR</span>
      </div>
      <div className="lamps">
        <Lamp st={svcSt(svc.engine)} k="engine" title="correlation engine /healthz" />
        <Lamp st={svcSt(svc.aggregator)} k="aggregator" title="aggregator /healthz" />
        <Lamp st={!d.scada ? "idle" : tagsDown ? "hot" : "ok"} k="scada" v={tagsDown ? "blind" : null} title="the tag server: every SCADA tag goes through it" />
        <Lamp st={d.scada && !tagsDown ? (d.scada.plc_connected ? "ok" : "hot") : "idle"} k="plc link" title="OpenPLC over Modbus, read by the tag server" />
        <Lamp st={hist ? (hist.connected ? "ok" : "hot") : "idle"} k="historian" title="TimescaleDB plant_tags" />
        <Lamp st={fleetOff ? "idle" : run === plcs.length ? "ok" : "strained"} k="fleet" v={fleetOff ? null : `${run}/${plcs.length} run`} title="virtual PLC fleet" />
        <Lamp st={auth === "enforced" ? "ok" : auth ? "strained" : "idle"} k="auth" v={auth && auth !== "enforced" ? auth : null} title="operator action gate" />
        <Lamp st={d.audit ? (d.audit.chain_ok ? "ok" : "hot") : "idle"} k="audit" v={d.audit && !d.audit.chain_ok ? "CHAIN BROKEN" : null} title="hash-chained action ledger" />
        <Lamp st={!d.graph ? "idle" : integ.length ? "hot" : "ok"} k="integrity" v={integ.length ? `${integ.length} open` : null} title="unsigned setpoint changes and current-balance checks (SCENARIOS.md 5.2)" />
      </div>
      <div className="cmd-right">
        {DEV && review && <Review {...review} />}
        {text && <TextSize {...text} />}
        <span className={`clock${clockSt}`} title={d.feedErr ? `last failed call: ${d.feedErr.what}` : "every panel refreshes every 5 s"}>{d.updated ? istTime(d.updated) : "…"} IST</span>
      </div>
    </header>
  );
}
