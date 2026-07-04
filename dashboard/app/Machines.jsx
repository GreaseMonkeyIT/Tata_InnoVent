"use client";
import { useEffect, useRef, useState } from "react";

// Machines — the plant floor (plane 2). PRIMARY telemetry: the machines are the subject;
// the Pods matrix below is just the hosting plane (AIOps engine + SCADA). Data arrives via
// /api/plant (the physics sim's /state, honestly labeled SIMULATED); client-side ring
// buffers per series drive the sparklines (same pattern as PodResources.jsx).
const WINDOW = 60; // samples kept per series (~5 min at the page's 5s poll)

// Display bands only — matched to the sim's measured physics so steady state reads calm:
// rail A idles ~0.900*Vsrc, rail B dips to ~0.887 in the compressor's duty window, PS1 drives
// A to ~0.866 (verified against a live sim run). Deviation judgment belongs to the ENGINE
// (2C'); these thresholds just keep the floor readable without flicker at the boundaries.
const C = { hot: "var(--red)", strained: "var(--orange)", ok: "var(--green)" };
const railSt = (r) => (r.volts < 0.872 * r.v_src ? "hot" : r.volts < 0.882 * r.v_src ? "strained" : "ok");
const machSt = (d, trip) =>
  d.tripped || (d.cooled && d.temp != null && d.temp >= trip - 3) || d.throughput < 70 ? "hot"
    : (d.cooled && d.temp != null && d.temp >= trip - 8) || d.throughput < 82 ? "strained" : "ok";
const tempColor = (t, trip) => (t >= trip - 3 ? C.hot : t >= trip - 8 ? C.strained : C.ok);
const thruColor = (p) => (p < 70 ? C.hot : p < 82 ? C.strained : C.ok);

// min/max-domained sparkline (voltage lives in a narrow band far from 0 — a 0-based
// scale like PodResources' would flatten the sag we exist to show).
function Spark({ hist, lo, hi, color }) {
  const w = 92, h = 20, n = hist.length;
  if (n < 2) return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" />;
  const min = lo != null ? lo : Math.min(...hist);
  const max = hi != null ? hi : Math.max(...hist);
  const span = max - min || 1;
  const xs = (i) => (i / (n - 1)) * (w - 2) + 1;
  const ys = (v) => h - 2 - Math.max(0, Math.min(1, (v - min) / span)) * (h - 4);
  let line = "";
  hist.forEach((v, i) => { line += (i ? "L" : "M") + xs(i).toFixed(1) + " " + ys(v).toFixed(1) + " "; });
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

function Machine({ name, d, trip, hist }) {
  const st = machSt(d, trip);
  const ampsHi = Math.max(...(hist.amps.length ? hist.amps : [d.amps]), d.amps) * 1.2;
  return (
    <div className={`mach ${st}`}>
      <div className="who">
        <span className="dot" style={{ background: C[st] }} />
        <span className="nm">{name}</span>
        <span className="ns" style={d.tripped ? { color: "var(--red)" } : undefined}>
          {d.tripped ? "tripped · contactor open" : d.cooled ? "cooled" : "uncooled"}
        </span>
      </div>
      <div className="mrow">
        <span className="k">draw</span>
        <span className="v">{d.amps.toFixed(1)} A</span>
        <Spark hist={hist.amps} lo={0} hi={ampsHi} color={C[st]} />
      </div>
      {d.cooled && d.temp != null && (
        <div className="mrow">
          <span className="k">temp</span>
          <span className="v" style={{ color: tempColor(d.temp, trip) }}>{d.temp.toFixed(1)} °C</span>
          <Spark hist={hist.temp} lo={30} hi={trip + 8} color={tempColor(d.temp, trip)} />
        </div>
      )}
      <div className="mrow">
        <span className="k">thru</span>
        <span className="v">{Math.round(d.throughput)} %</span>
        <Spark hist={hist.thru} lo={0} hi={100} color={thruColor(d.throughput)} />
      </div>
    </div>
  );
}

const PANELS = [
  { id: 1, cap: "Bus voltage · per rail" },
  { id: 2, cap: "Current draw · per machine" },
  { id: 3, cap: "Coolant temps · vs trip" },
];

export default function Machines({ plant, host }) {
  const histRef = useRef(new Map()); // series key -> ring buffer
  const [, bump] = useState(0);      // hist mutated after render -> nudge one repaint

  useEffect(() => {
    if (!plant?.devices) return;
    const H = histRef.current;
    const push = (k, v) => {
      if (v == null) return;
      let a = H.get(k);
      if (!a) { a = []; H.set(k, a); }
      a.push(v);
      if (a.length > WINDOW) a.shift();
    };
    for (const [rn, r] of Object.entries(plant.rails || {})) push("r/" + rn, r.volts);
    if (plant.loop) push("loop", plant.loop.flow);
    for (const [dn, d] of Object.entries(plant.devices)) {
      push("m/" + dn + "/a", d.amps);
      push("m/" + dn + "/t", d.temp);
      push("m/" + dn + "/p", d.throughput);
    }
    bump((x) => x + 1);
  }, [plant]);

  if (!plant) return <div style={{ color: "var(--text-faint)" }}>waiting for plant telemetry…</div>;
  if (plant.source === "unavailable" || !plant.devices)
    return <div style={{ color: "var(--text-faint)" }}>plant sim unreachable</div>;

  const H = histRef.current;
  const g = (k) => H.get(k) || [];
  const trip = plant.trip_c ?? 78;
  const loop = plant.loop;
  const faults = plant.active_faults || [];
  const nomFlow = loop?.flow_nominal ?? 120;
  const flowColor = loop ? (loop.flow < 0.6 * nomFlow ? C.hot : loop.flow < 0.85 * nomFlow ? C.strained : C.ok) : C.ok;

  return (
    <>
      {faults.length > 0 && (
        <div className="echips" style={{ marginBottom: 12 }}>
          {faults.map((f) => (
            <span key={f} className="echip" style={{ borderColor: "rgba(242,73,92,0.5)", color: "var(--red)" }}>
              {f} · fault active
            </span>
          ))}
        </div>
      )}
      <div className="rails">
        {Object.entries(plant.rails || {}).map(([rn, r]) => {
          const rst = railSt(r);
          return (
            <div key={rn} className="rail">
              <div className="rail-head">
                <span className="brk">rail · {rn}</span>
                <span className="rv" style={{ color: C[rst] }}>{r.volts.toFixed(1)} V</span>
                <span className="rnom">nom {Math.round(r.v_src)} V</span>
                <Spark hist={g("r/" + rn)} lo={r.v_src * 0.85} hi={r.v_src * 1.01} color={C[rst]} />
              </div>
              <div className="machs">
                {Object.entries(plant.devices).filter(([, d]) => d.rail === rn).map(([dn, d]) => (
                  <Machine key={dn} name={dn} d={d} trip={trip}
                    hist={{ amps: g("m/" + dn + "/a"), temp: g("m/" + dn + "/t"), thru: g("m/" + dn + "/p") }} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {loop && (
        <div className="loopbar">
          <span className="brk">coolant · {loop.name || "cool-1"}</span>
          <span className="v" style={{ color: flowColor }}>{loop.flow.toFixed(1)} L/min</span>
          <span className="vf">nom {Math.round(nomFlow)}</span>
          <Spark hist={g("loop")} lo={0} hi={nomFlow * 1.1} color={flowColor} />
          <span className="vf">pump {Math.round((loop.pump_health ?? 1) * 100)}% · trip {Math.round(trip)}°C</span>
        </div>
      )}
      <div className="gnote" style={{ margin: "14px 0 10px" }}>
        Trends — live from Grafana over Prometheus. Substrate: physics-simulated plant (plane 2); the inference over it is real.
      </div>
      <div className="gframes">
        {PANELS.map((p) => (
          <div key={p.id} className="gframe">
            <div className="cap">{p.cap}</div>
            {host
              ? <iframe title={p.cap} loading="lazy" src={`http://${host}:30030/d-solo/skn-plant/skn-plant?orgId=1&panelId=${p.id}&theme=dark&from=now-15m&to=now&refresh=5s&timezone=Asia/Kolkata`} />
              : <div style={{ height: 220 }} />}
          </div>
        ))}
      </div>
    </>
  );
}
