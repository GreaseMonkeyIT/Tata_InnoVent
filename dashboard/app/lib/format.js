// Number, time, and ledger formatting shared by the console panels.
export const fmtTag = (t) =>
  t.value == null ? "—"
    : t.unit === "bool" ? (t.value ? "TRIP" : "ok")
      : `${Number(t.value).toFixed(1)} ${t.unit === "pct" ? "%" : t.unit === "degC" ? "°C" : t.unit}`;

export const fmtMs = (v) => (v == null ? "—" : v < 1 ? `${v.toFixed(2)} ms` : `${v.toFixed(1)} ms`);
export const milli = (c) => (c ? Math.round(c * 1000) + "m" : "—");          // cores -> millicpu
export const mib = (b) => (b ? Math.round(b / 1048576) + "MiB" : "—");       // bytes -> MiB
export const pctOf = (use, req, lim) => { const d = lim || req; return d ? (use || 0) / d : null; };
// A reading as the operator says it: 30 not 30.0, 61.25 as 61.3. SCADA sends floats, the ledger ints.
export const num = (v) => (v == null || Number.isNaN(Number(v)) ? "?" : String(Math.round(Number(v) * 10) / 10));

const IST = { timeZone: "Asia/Kolkata", hour12: false };   // 24 h, as a control room reads time
export const istTime = (d) => d.toLocaleTimeString("en-IN", IST);
export const istTs = (ts) => istTime(new Date(ts * 1000));

export const RES_WORD = {
  psi_io: "disk I/O", psi_cpu: "CPU", psi_mem: "memory", bus_voltage: "rail voltage", coolant_temp: "coolant temperature",
  field_latency: "field network latency",
};

// One line of detail for an audit row. Act-loop rows carry the setpoint write, its citation,
// and the relief the API measured after it (FLEET.md section 10).
export function ledgerText(e) {
  const ev = e.evidence || {};
  if (e.verb === "execute" && ev.tag) return `${ev.tag} ${ev.from}→${ev.to} % · cites ${ev.root || "?"} · ${(ev.evidence || []).join("+")}`;
  if (e.verb === "relief") {
    const v = ev.volts_before != null && ev.volts_after != null ? `rail ${ev.rail} ${ev.volts_before.toFixed(1)}→${ev.volts_after.toFixed(1)} V` : "";
    const a = ev.amps_before != null && ev.amps_after != null ? `${e.target} ${ev.amps_before.toFixed(1)}→${ev.amps_after.toFixed(1)} A` : "";
    return `measured after ${ev.after_s ?? "?"} s · ${[a, v].filter(Boolean).join(" · ")}`;
  }
  if (e.verb === "restore" && ev.tag) return `${ev.tag} ${num(ev.from)}→${num(ev.to)} %${ev.reason ? ` · ${ev.reason}` : ""}`;
  // SCENARIOS.md 5.2: the integrity checks write these rows themselves, as actor visr
  if (e.verb === "unsigned") {
    const who = ev.clients?.length ? ` · client ${ev.clients.join(", ")}` : "";
    return `${ev.tag || "?"} ${num(ev.from)}→${num(ev.to)} · ${ev.reason || "no ledger row"}${who}`;
  }
  if (e.verb === "balance") {
    return `rail ${ev.rail} feeder ${num(ev.feeder_amps)} A vs reported ${num(ev.reported_amps)} A${ev.channel ? ` · ${ev.channel}` : ""}`;
  }
  if (ev.root) return `root ${ev.root} · ${(ev.evidence || []).join("+")}`;
  return "";
}
