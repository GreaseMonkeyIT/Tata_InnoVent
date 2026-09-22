"use client";
import Glyph from "./Glyph";

// Fault injection: the PS-series scenarios (SCENARIOS.md). A plant fault perturbs the plant MODEL,
// and the symptoms emerge from the physics. PS4A and PS6 act on real processes instead: a rogue
// engineering workstation writes a PLC setpoint, and the tag server leaks memory. The row state
// comes from each fault owner through the api catalogue, never from a click, so it stays true
// across reloads. The live row also names the real incident that the scenario is anchored on.
const FALLBACK = [
  { id: "PS1", name: "Rail-sag cascade", mechanism: "press-1 bearing friction → amps up → rail A sags → cnc-1 and qa-scanner-1 degrade" },
  { id: "PS2", name: "Power sag trips the chiller", mechanism: "compressor-1 stuck on → rail B sags → chiller-1 overload trips → coolant flow drops" },
  { id: "PS5", name: "Coolant pump degradation", mechanism: "flow drops → temperatures ramp toward the 78 °C trip (forecast first)" },
];

export default function FaultInjection({ d }) {
  const cat = d.scenarios;
  const rows = (cat || FALLBACK).filter((s) => s.id !== "PS0");
  // The catalogue says which faults are on (null = that owner did not answer). Without it, fall
  // back to the plant's own active faults, which cover PS1, PS2, PS3, PS4B and PS5.
  const plantOn = new Set(d.plant?.active_faults || []);
  const isOn = (s) => (cat ? s.active === true : plantOn.has(s.id));
  const ps0 = cat?.find((s) => s.id === "PS0");
  const calm = ps0 ? ps0.active === true : d.plant && plantOn.size === 0;
  return (
    <div className="faults-list">
      <div className={`fi-row ps0${calm ? " calm" : ""}`}>
        <Glyph st={calm ? "ok" : "idle"} />
        <span className="fi-id">PS0</span>
        <div className="fi-b"><div className="fi-nm">Steady plant</div><div className="fi-ds">no fault · baselines mature · the engine stays silent</div></div>
        <span className="fi-st">{calm ? "now" : ""}</span>
      </div>
      {rows.map((s) => {
        const on = isOn(s);
        const busy = !!d.pending[s.id];
        const unknown = cat && s.active == null;
        return (
          <div key={s.id} className={`fi-row${on ? " live" : ""}`} title={[s.mechanism, s.anchor].filter(Boolean).join("\n")}>
            <Glyph st={on ? "strained" : unknown ? "busy" : "idle"} />
            <span className="fi-id">{s.id}</span>
            <div className="fi-b">
              <div className="fi-nm">{s.name}</div>
              <div className="fi-ds">{on ? "injected · " : unknown ? "owner not answering · " : ""}{s.mechanism}</div>
              {on && s.anchor && <div className="fi-an">{s.anchor}</div>}
            </div>
            {on
              ? <button className="btn sm warn" disabled={busy} onClick={() => d.scenario(s.id, "reset")}>{busy ? "…" : "Reset"}</button>
              : <button className="btn sm cmd" disabled={busy || s.triggerable === false} onClick={() => d.scenario(s.id, "trigger")}>{busy ? "…" : "Fire"}</button>}
          </div>
        );
      })}
      {d.fired && <div className="fi-msg">{d.fired}</div>}
    </div>
  );
}
