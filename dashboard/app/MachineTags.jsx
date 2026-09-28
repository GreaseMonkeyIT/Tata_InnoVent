"use client";
import { useState } from "react";
import Glyph from "./Glyph";
import { QST } from "./lib/palette";
import { fmtTag } from "./lib/format";

// Every machine's tags, from the two tag sources the console polls:
//   /api/tags        the base SCADA table read from OpenPLC        (PLANT.<ASSET>.<SIGNAL>)
//   /api/fleet/tags  every virtual PLC's table, read over S7comm or Modbus (FLEET.<PLC>.<ASSET>.<SIGNAL>)
// Nothing here is invented: a chip shows the tag name, its live value, its quality, and the PLC
// address it was read from. The order puts the readings an operator looks at first.
const ORDER = ["AMPS", "TEMP", "VOLTS", "THROUGHPUT", "SPEED_PCT", "DERATE_PCT", "RUN", "READY", "TRIP", "TRIP_LIMIT",
  "HEAT", "AIR_PRESSURE", "SUPPLY_TEMP", "FLOW", "PUMP_HEALTH", "COOLING_SHORTFALL", "CELL_ENABLE"];
const rank = (s) => { const i = ORDER.indexOf(s); return i < 0 ? ORDER.length : i; };

export function tagsFor(asset, scada, fleetTags) {
  if (!asset) return [];
  const out = [];
  for (const t of scada?.tags || []) if (t.asset === asset) out.push({ ...t, source: "PLANT", plc: "openplc" });
  for (const t of fleetTags || []) if (t.asset === asset) out.push({ ...t, source: "FLEET" });
  return out.sort((a, b) => rank(a.signal) - rank(b.signal) || String(a.tag).localeCompare(String(b.tag)));
}

const short = (tag) => String(tag || "").split(".").slice(-1)[0];

function Chip({ t, full }) {
  const q = t.quality || "BAD";
  return (
    <span className={`tagchip ${q.toLowerCase()}${t.writable ? " sp" : ""}`}
      title={`${t.tag}\n${t.address || "calc"} · ${t.source}${t.plc ? " · " + t.plc : ""}\nquality ${q}${t.writable ? "\nsetpoint (VISR may write it)" : ""}`}>
      <Glyph st={QST[q] || "idle"} size={7} />
      <span className="k">{full ? t.tag : short(t.tag)}</span>
      <b className="v">{fmtTag(t)}</b>
      {full && <span className="a">{t.address || "calc"}</span>}
    </span>
  );
}

// A compact strip under an asset row: the first few chips, then "+n" that opens the rest.
export function TagStrip({ tags, max = 4 }) {
  const [open, setOpen] = useState(false);
  if (!tags?.length) return null;
  const shown = open ? tags : tags.slice(0, max);
  return (
    <div className={`tagstrip${open ? " open" : ""}`}>
      {shown.map((t) => <Chip key={t.tag} t={t} full={open} />)}
      {tags.length > max && (
        <button className="tagmore" onClick={(e) => { e.stopPropagation(); setOpen(!open); }} aria-expanded={open}>
          {open ? "less" : `+${tags.length - max}`}
        </button>
      )}
    </div>
  );
}

// The full tag card of one asset: one row per tag with name, value, quality, and address.
export function TagCard({ tags }) {
  if (!tags?.length) return <div className="empty">no tags read for this asset yet</div>;
  return (
    <div className="tagcard">
      {tags.map((t) => (
        <div key={t.tag} className={`tagrow ${(t.quality || "BAD").toLowerCase()}`} title={`${t.source}${t.plc ? " · " + t.plc : ""}`}>
          <Glyph st={QST[t.quality] || "idle"} size={8} />
          <span className="k">{t.tag}</span>
          <b className="v">{fmtTag(t)}</b>
          <span className="a">{t.address || "calc"}</span>
        </div>
      ))}
    </div>
  );
}